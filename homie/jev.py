"""Jev (TypeSafe System One) as the team's orchestrator.

Jev answers typed questions about a state, many at once, each with its own confidence:
Choice (pick one, up to 255 options), Noul (yes/no probability) and Score (an ordered rubric).
It does not write text and is weak at numbers and dates, so Gemini still writes every message
and pulls out amounts and dates. Homie acts on a Jev answer only above a confidence gate and
falls back otherwise.

Docs: https://pydantic.dev/docs/ai/models/typesafe/ · https://openrouter.ai/docs/guides/community/jev
"""

import asyncio
import logging
import os
import time

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

from homie.config import env

log = logging.getLogger("homie.jev")
if env("JEV_API_KEY"):
    os.environ.setdefault("TYPESAFE_API_KEY", env("JEV_API_KEY"))
_client: AsyncTypeSafeClient | None = None


def available() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


async def ask(state: dict | str, questions: dict, label: str = "") -> dict | None:
    """One System One call. Returns {name: answer} or None (Jev unavailable or failed)."""
    global _client
    if not available():
        return None
    try:
        _client = _client or AsyncTypeSafeClient()
        t = time.time()
        r = await _client.system_one(state=state, questions=questions, model=env("JEV_MODEL", "jev-latest"), timeout=5)
        answers = dict(r.answers)
        asyncio.ensure_future(_trace(label, answers, time.time() - t))
        return answers
    except Exception as e:
        log.warning("Jev call failed (%s): %s", label, e)
        return None


def show(a) -> str:
    if a is None:
        return "?"
    if a.type == "choice":
        return f"{a.choice} ({a.confidence:.2f})"
    if a.type == "noul":
        return f"{'yes' if a.noul >= 0.5 else 'no'} ({a.noul:.2f})"
    return f"{a.legend.get(round(a.score), a.score)} ({a.confidence:.2f})"


async def _trace(label: str, answers: dict, seconds: float) -> None:
    """Jev's decisions show up on the mission-control feed like any agent message."""
    from homie import hub_client

    summary = ", ".join(f"{k}={show(v)}" for k, v in answers.items())
    await hub_client.post("/api/agentlog", {"from": "jev", "to": "homie", "kind": label or "decision",
                                            "summary": f"{label}: {summary} · {seconds * 1000:.0f} ms", "ts": time.time()})


def yes(a, gate: float = 0.5) -> bool:
    return a is not None and a.noul >= gate


def pick(a, gate: float = 0.6) -> str | None:
    return a.choice if a is not None and a.confidence >= gate else None


# ---------- the decisions Homie makes with Jev ----------

ROUTES = {
    "search": "They want to find, compare or rent an apartment",
    "repair": "Something is broken or needs maintenance where they live",
    "policy": "A question about a lease, deposit, fees, landlord rules or tenant rights",
    "status": "They are asking for an update on what the team is doing",
    "memory": "They ask what Homie knows about them, or tell it something to remember",
    "pictures": "They want photos or screenshots of a listing",
    "schedule": "They want Homie to do something later, at a time or day, or to remind them",
    "keys": "They say they got their keys, moved in, or signed the lease",
    "chat": "Greeting, thanks, small talk or anything else",
}
BEDROOMS = {"unspecified": "They did not say how many bedrooms", "studio": "A studio", "one": "One bedroom",
            "two": "Two bedrooms", "three": "Three bedrooms", "four": "Four or more bedrooms"}
BED_COUNT = {"studio": 0, "one": 1, "two": 2, "three": 3, "four": 4}


async def read_message(text: str) -> dict:
    """Everything Homie needs to decide about one incoming message, in one Jev call."""
    a = await ask({"renter_message": text}, {
        "route": Choice(instructions="What does the renter mainly want from the Homie apartment team right now?", criteria=ROUTES),
        "bedrooms": Choice(instructions="How many bedrooms is the renter asking for?", criteria=BEDROOMS),
        "no_ssn": Noul(instructions="Does the renter say they do not have a US Social Security Number?"),
        "free_month": Noul(instructions="Does the renter ask for a free month or a move-in special?"),
        "also_repair": Noul(instructions="Does the message also mention something broken that needs fixing?"),
        "urgency": Score(instructions="How urgent is the renter's situation?",
                         criteria=["Not urgent", "Soon, within days", "Urgent, needs action today"]),
    }, label="read message")
    if not a:
        return {}
    out = {"_jev": {k: show(v) for k, v in a.items()}}
    route = pick(a["route"], 0.55)
    if route:
        out["intent"] = route
    beds = pick(a["bedrooms"], 0.7)
    if beds and beds != "unspecified":
        out["beds"] = BED_COUNT[beds]
    if yes(a["no_ssn"], 0.6):
        out["no_ssn"] = True
    if yes(a["free_month"], 0.6):
        out["require_free_month"] = True
    out["also_repair"] = yes(a["also_repair"], 0.7)
    out["urgency"] = round(a["urgency"].score)
    return out


async def judge_reply(text: str, offer: str) -> str:
    """approve / decline / unclear: Jev judges 'sure, but make it the cheaper one' properly."""
    a = await ask({"offer": offer, "renter_reply": text}, {
        "verdict": Choice(instructions="Does the renter's reply approve holding the offered apartment?", criteria={
            "approve": "Yes: they agree to hold or book it, even with a small condition",
            "decline": "No: they don't want it or want Homie to keep looking",
            "unclear": "It's not possible to tell, or they asked a question instead"}),
    }, label="judge reply")
    return (pick(a["verdict"], 0.6) or "unclear") if a else ""


async def teammate_for(text: str) -> str | None:
    a = await ask({"group_chat_message": text}, {
        "teammate": Choice(instructions="Which Homie teammate should answer this group-chat message?", criteria={
            "homie": "Homie, the lead: searches, plans, general questions and updates",
            "calls": "Calls: phone calls to leasing offices, prices and offers",
            "papers": "Papers: documents, applications, no-SSN requirements, cashier's checks",
            "fix": "Fix: repairs and maintenance",
            "policy": "Policy: leases, deposits, tenant rights and the law",
            "pics": "Pics: photos and screenshots of listings"}),
    }, label="route group message")
    return pick(a["teammate"], 0.6) if a else None


async def memory_action(text: str) -> str | None:
    a = await ask({"message_to_memory": text}, {
        "action": Choice(instructions="What does the renter want from Homie Memory?", criteria={
            "answer": "They ask what Homie knows or remembers about them",
            "remember": "They tell Homie a fact or preference to save"}),
    }, label="memory")
    return pick(a["action"], 0.6) if a else None


async def score_offer(offer: dict, renter: str) -> dict | None:
    """Composite scoring: Jev judges the soft dimensions; code adds price and weights them."""
    a = await ask({"offer": offer, "renter": renter}, {
        "special": Score(instructions="How valuable is this building's current special or concession for the renter?",
                         criteria=["No special", "Small perk (waived fee, small credit)", "Big (weeks or a month free)"]),
        "no_ssn_ok": Noul(instructions="Based on what the office said, can someone without a US Social Security Number apply here?"),
        "fits": Score(instructions="How well does this offer fit what the renter said they want?",
                      criteria=["Poor fit", "Partial fit", "Strong fit"]),
    }, label=f"score {offer.get('name', 'offer')}")
    if not a:
        return None
    return {"special": a["special"].score / 2, "no_ssn_ok": a["no_ssn_ok"].noul, "fits": a["fits"].score / 2}


async def call_outcome(transcript: str) -> dict:
    """Yes/no facts from a call transcript, each with a confidence; numbers stay with Gemini."""
    a = await ask({"call_transcript": transcript[-12000:]}, {
        "reached_person": Noul(instructions="Did a person at the leasing office actually talk with the assistant?"),
        "matched": Noul(instructions="Did the office agree to match or beat the competing offer?"),
        "no_ssn_ok": Noul(instructions="Did the office say someone without a Social Security Number can apply?"),
        "available": Noul(instructions="Did the office say a unit is available for the requested move-in?"),
        "callback": Noul(instructions="Did the office ask to call back later or take a message?"),
    }, label="read call")
    return {k: round(v.noul, 2) for k, v in a.items()} if a else {}


async def is_emergency(issue: str) -> bool:
    a = await ask({"repair_issue": issue}, {
        "emergency": Noul(instructions="Is this a safety emergency (gas smell, fire, flooding, no heat in freezing weather, no water, electrical sparks)?"),
    }, label="repair triage")
    return yes(a["emergency"], 0.7) if a else False


TASK_KINDS = {
    "get_offers": "Get apartment offers or prices from leasing offices at a later time",
    "call_building": "Call one specific building or office later",
    "remind": "Remind the renter about something (rent, a viewing, documents, a deadline)",
    "follow_up_repair": "Check on or chase a repair later",
    "check_prices": "Re-check advertised prices or specials later",
}


async def task_kind(text: str) -> str | None:
    a = await ask({"future_task": text}, {"kind": Choice(instructions="What kind of future task is this?", criteria=TASK_KINDS)}, label="schedule task")
    return pick(a["kind"], 0.5) if a else None


AVATAR_EMOTIONS = {"calm": "Calm, informative", "happy": "Warm and pleased", "excited": "Excited, celebrating good news",
                   "concerned": "Sympathetic about a problem or bad news", "thinking": "Considering, asking a question or checking"}
