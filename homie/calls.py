"""Outbound phone calls to leasing offices through ElevenLabs Agents + Twilio.

With MOCK_CALLS=1 the calls are simulated from data/buildings.json, so the whole
pipeline can be built and rehearsed before the phone setup works.
"""

import asyncio
import logging
import random
import uuid

import httpx

from homie.config import (
    env,
    ELEVENLABS_AGENT_ID,
    ELEVENLABS_API_KEY,
    ELEVENLABS_PHONE_NUMBER_ID,
    MOCK_CALLS,
)
from homie.llm import extract_call

log = logging.getLogger(__name__)
API = "https://api.elevenlabs.io/v1/convai"

PURPOSE_BRIEFS = {
    "quote": (
        "Ask for the current price of a one-bedroom for a move-in on {move_in}, any upcoming discounts "
        "and which days they apply, total move-in fees, how they pay rent (check, portal), and what they "
        "accept from an international student with no SSN instead of a guarantor."
    ),
    "negotiate": (
        "Another building, {competitor}, is offering {competitor_offer}. Politely ask if they can match "
        "or beat that for a lease signed this week. Confirm the final price and terms."
    ),
    "repair": (
        "You are calling on behalf of the tenant in unit {unit}. Report this issue: {issue}. "
        "A photo has been submitted with ticket {ticket_id}. Ask for the earliest repair slot and confirm it."
    ),
}


async def place_call(building: dict, purpose: str, context: dict) -> dict:
    """Relay voice call if the office has a Relay handle, phone call via ElevenLabs + Twilio if it has a number, else simulated."""
    if not MOCK_CALLS and building.get("relay_handle") and env("RELAY_TOKEN_CALLS"):
        return await _relay_call(building, purpose, context)
    if not MOCK_CALLS and env("TWILIO_ACCOUNT_SID") and building.get("phone"):
        return await _twilio_call(building, purpose, context)
    if not MOCK_CALLS and ELEVENLABS_API_KEY and building.get("phone"):
        return await _real_call(building, purpose, context)
    return await _mock_call(building, purpose, context)


CALLER_PROMPT = (
    "You are Homie Calls, an AI assistant phoning a leasing office on behalf of an international student who is "
    "still abroad. Open by saying you're an AI assistant calling for a student, and ask if it's {building}. "
    "Be warm, brief and specific; this is a phone call, so speak in short natural sentences with no lists. "
    "Your task: {task} Repeat key numbers back to confirm them. When you have what you need, thank them, say "
    "goodbye, and stop talking."
)


async def _twilio_call(building: dict, purpose: str, context: dict) -> dict:
    """Dial the real office. Twilio streams the audio to our server, where Gemini Live does the talking."""
    from homie.config import HUB_URL, PUBLIC_URL

    task = PURPOSE_BRIEFS[purpose].format_map({"move_in": "August 20", "unit": "the tenant's unit", **context})
    async with httpx.AsyncClient(timeout=30) as client:
        key = (await client.post(f"{HUB_URL}/api/calls", json={
            "building_id": building["id"], "prompt": CALLER_PROMPT.format(building=building["name"], task=task),
            "greeting": "The office just picked up. Start the call."})).json()["key"]
        host = PUBLIC_URL.split("://", 1)[1]
        twiml = f'<Response><Connect><Stream url="wss://{host}/twilio/stream/{key}"/></Connect></Response>'
        sid = env("TWILIO_ACCOUNT_SID")
        r = await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                              auth=(sid, env("TWILIO_AUTH_TOKEN")),
                              data=[("To", building["phone"]), ("From", env("TWILIO_FROM_NUMBER")), ("Twiml", twiml),
                                    ("StatusCallback", f"{PUBLIC_URL}/twilio/status/{key}"),
                                    ("StatusCallbackEvent", "completed")])
        if r.status_code >= 300:
            log.warning("Twilio call to %s failed: %s", building["name"], r.text[:300])
            return {"answered": False, "summary": f"Couldn't dial {building['name']}: {r.json().get('message', r.status_code)}"}
        result = (await client.get(f"{HUB_URL}/api/calls/{key}/wait", params={"timeout": 300}, timeout=320)).json()
    if not result.get("answered"):
        return {"answered": False, "summary": f"{building['name']} didn't pick up ({result.get('status', 'no answer')})"}
    fields = await extract_call(result["transcript"])
    return {"answered": True, "transcript": result["transcript"], **{k: v for k, v in fields.items() if v is not None}}


async def _relay_call(building: dict, purpose: str, context: dict) -> dict:
    from relaymessenger import Relay

    from homie.voice import AVATARS, run_call

    token = env("RELAY_TOKEN_CALLS")
    relay = Relay(token)
    handle = building["relay_handle"]
    task = PURPOSE_BRIEFS[purpose].format_map({"move_in": "August 20", "unit": "the tenant's unit", **context})
    note = {"quote": "a one-bedroom for an international student", "negotiate": "matching a competing offer",
            "repair": "a repair request"}[purpose]
    try:
        sent = await relay.messages.create(
            to=[handle], idempotency_key=uuid.uuid4().hex,
            message={"parts": [{"type": "text", "value": f"Hi, this is Homie Calls, an AI assistant. Calling you now about {note}."}]})
        call = await relay.calls.create(sent["chat_id"], to=[handle], idempotency_key=uuid.uuid4().hex)
    except Exception as e:
        log.warning("Relay call to %s failed to start: %s", handle, e)
        return {"answered": False, "summary": f"Couldn't reach {building['name']} on Relay"}
    call_id = (call.get("call") or call)["id"]
    from homie import hub_client as hub

    result = await run_call(token, call_id, CALLER_PROMPT.format(building=building["name"], task=task),
                            greeting="The office just picked up. Start the call.", avatar=AVATARS / "calls.png",
                            on_line=lambda line: asyncio.ensure_future(hub.post("/api/transcript", {"building_id": building["id"], "line": line})))
    try:
        await relay.calls.end(call_id)
    except Exception:
        pass
    if not result["answered"] or not result["transcript"]:
        return {"answered": False, "summary": f"{building['name']} didn't pick up"}
    fields = await extract_call(result["transcript"])
    return {"answered": True, "transcript": result["transcript"], **{k: v for k, v in fields.items() if v is not None}}


async def _real_call(building: dict, purpose: str, context: dict) -> dict:
    brief = PURPOSE_BRIEFS[purpose].format_map({"move_in": "August 20", "unit": "the tenant's unit", **context})
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    payload = {
        "agent_id": ELEVENLABS_AGENT_ID,
        "agent_phone_number_id": ELEVENLABS_PHONE_NUMBER_ID,
        "to_number": building["phone"],
        "conversation_initiation_client_data": {
            "dynamic_variables": {"building_name": building["name"], "task": brief},
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API}/twilio/outbound-call", headers=headers, json=payload)
        r.raise_for_status()
        conversation_id = r.json().get("conversation_id")
        if not conversation_id:
            return {"answered": False, "summary": f"Call did not start: {r.text[:200]}"}

        # Poll until the call is finished and analysed (max ~4 minutes).
        for _ in range(80):
            await asyncio.sleep(3)
            c = await client.get(f"{API}/conversations/{conversation_id}", headers=headers)
            if c.status_code != 200:
                continue
            convo = c.json()
            if convo.get("status") in ("done", "failed"):
                break
        else:
            return {"answered": False, "summary": "Call timed out"}

    turns = convo.get("transcript") or []
    transcript = "\n".join(f"{t.get('role')}: {t.get('message')}" for t in turns if t.get("message"))
    office_spoke = any(t.get("role") == "user" for t in turns)
    if convo.get("status") == "failed" or not office_spoke:
        return {"answered": False, "summary": "No answer"}

    fields = await extract_call(transcript)
    collected = (convo.get("analysis") or {}).get("data_collection_results") or {}
    for key, item in collected.items():
        if isinstance(item, dict) and item.get("value") not in (None, ""):
            fields.setdefault(key, item["value"])
    return {"answered": True, "transcript": transcript, **fields}


def _simulated(building: dict) -> dict:
    rng = random.Random(building["name"])
    price = rng.randrange(1450, 2300, 5)
    return {"price": price, "discount": rng.choice(["One month free on a 13-month lease", "$500 off move-in", "Waived admin fee", "None right now"]),
            "discount_day": None, "ssn_alternative": rng.choice(["Passport, visa and I-20 with proof of funds", "Guarantor service (e.g. TheGuarantors)", "Passport plus two months' deposit"]),
            "fees": rng.choice([150, 250, 350]), "matches_free_month": rng.random() < 0.5, "payment": rng.choice(["Online portal", "Cashier's check or money order", "Online portal or cashier's check"])}


async def _mock_call(building: dict, purpose: str, context: dict) -> dict:
    """Rehearsal only. With real buildings every number is simulated and labelled that way."""
    await asyncio.sleep(random.uniform(2.5, 6))
    m = building.get("mock") or _simulated(building)
    tag = " (simulated, no real call placed)" if building.get("real") else ""
    if purpose == "quote":
        return {
            "answered": True,
            "price": m["price"],
            "discount": m["discount"],
            "discount_day": m["discount_day"],
            "ssn_alternative": m["ssn_alternative"],
            "fees": m["fees"],
            "payment": m["payment"],
            "summary": f"{building['name']} quoted ${m['price']}: {m['discount']}.{tag}",
        }
    if purpose == "negotiate":
        if m["matches_free_month"]:
            price = m["price"] - 90
            return {
                "answered": True,
                "matched": True,
                "price": price,
                "discount": "One month free (matched)",
                "summary": f"{building['name']} matched a free month at ${price}.{tag}",
            }
        return {"answered": True, "matched": False, "summary": f"{building['name']} would not match.{tag}"}
    if purpose == "repair":
        if context.get("attempt", 1) == 1 and context.get("simulate_no_answer"):
            return {"answered": False, "summary": "No answer"}
        return {"answered": True, "repair_slot": "Tuesday 10:00 AM", "summary": "Repair booked for Tuesday 10:00 AM."}
    return {"answered": False, "summary": "Unknown purpose"}
