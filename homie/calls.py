"""Outbound phone calls to leasing offices through ElevenLabs Agents + Twilio.

With MOCK_CALLS=1 the calls are simulated from data/buildings.json, so the whole
pipeline can be built and rehearsed before the phone setup works.
"""

import asyncio
import logging
import random

import httpx

from homie.config import (
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
    if MOCK_CALLS or not (ELEVENLABS_API_KEY and building.get("phone")):
        return await _mock_call(building, purpose, context)
    return await _real_call(building, purpose, context)


async def _real_call(building: dict, purpose: str, context: dict) -> dict:
    brief = PURPOSE_BRIEFS[purpose].format_map({"move_in": "August 20", **context})
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


async def _mock_call(building: dict, purpose: str, context: dict) -> dict:
    await asyncio.sleep(random.uniform(2.5, 6))
    m = building["mock"]
    if purpose == "quote":
        return {
            "answered": True,
            "price": m["price"],
            "discount": m["discount"],
            "discount_day": m["discount_day"],
            "ssn_alternative": m["ssn_alternative"],
            "fees": m["fees"],
            "payment": m["payment"],
            "summary": f"{building['name']} quoted ${m['price']}: {m['discount']}.",
        }
    if purpose == "negotiate":
        if m["matches_free_month"]:
            price = m["price"] - 90
            return {
                "answered": True,
                "matched": True,
                "price": price,
                "discount": "One month free (matched)",
                "summary": f"{building['name']} matched a free month at ${price}.",
            }
        return {"answered": True, "matched": False, "summary": f"{building['name']} would not match."}
    if purpose == "repair":
        if context.get("attempt", 1) == 1 and context.get("simulate_no_answer"):
            return {"answered": False, "summary": "No answer"}
        return {"answered": True, "repair_slot": "Tuesday 10:00 AM", "summary": "Repair booked for Tuesday 10:00 AM."}
    return {"answered": False, "summary": "Unknown purpose"}
