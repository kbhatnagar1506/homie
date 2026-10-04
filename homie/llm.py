"""Small LLM helpers. Every call has a non-LLM fallback so the demo never stalls."""

import asyncio
import json
import logging
import re

from openai import AsyncOpenAI

from homie.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER, VERTEX_LOCATION

log = logging.getLogger(__name__)
_static = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY) if LLM_API_KEY and LLM_PROVIDER != "vertex" else None
_vertex: dict = {}


def _client() -> AsyncOpenAI | None:
    """Gemini on Vertex AI (service-account auth, token refreshed hourly) or any OpenAI-compatible API."""
    if LLM_PROVIDER != "vertex":
        return _static
    try:
        import google.auth
        import google.auth.transport.requests

        if "creds" not in _vertex:
            _vertex["creds"], _vertex["project"] = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds = _vertex["creds"]
        if not creds.valid:
            creds.refresh(google.auth.transport.requests.Request())
            _vertex["client"] = AsyncOpenAI(
                base_url=f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{_vertex['project']}/locations/{VERTEX_LOCATION}/endpoints/openapi",
                api_key=creds.token,
            )
        return _vertex["client"]
    except Exception as e:
        log.warning("Vertex auth failed: %s", e)
        return None


async def complete_json(system: str, user: str) -> dict | None:
    client = _client()
    if not client:
        return None
    try:
        r = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
        )
        text = r.choices[0].message.content or ""
        match = re.search(r"\{.*\}", text, re.S)
        return json.loads(match.group(0)) if match else None
    except Exception as e:
        log.warning("LLM call failed: %s", e)
        return None


async def complete_text(system: str, messages: list[dict], fallback: str = "") -> str:
    client = _client()
    if not client:
        return fallback
    try:
        r = await client.chat.completions.create(
            model=LLM_MODEL, messages=[{"role": "system", "content": system}, *messages], temperature=0.6,
        )
        return (r.choices[0].message.content or "").strip() or fallback
    except Exception as e:
        log.warning("LLM call failed: %s", e)
        return fallback


INTENT_PROMPT = """Extract an apartment request into JSON with keys:
intent ("search" | "repair" | "policy" | "status" | "other"; "policy" means a question about a lease, deposit,
tenant rights, landlord rules or the law), city, area (neighborhood + city, e.g. \"downtown Atlanta, GA\"), move_in (string), beds (int; 0 for a studio; null if not stated),
max_rent (int), no_ssn (bool), require_free_month (bool), fee_cap (int), issue (string, repairs only),
building (string: a specific building or complex they named, e.g. "The Mix"), url (a building website if given).
Use null for anything not stated. Reply with JSON only."""


async def parse_intent(text: str) -> dict:
    """Jev makes the decisions (route, bedrooms, flags, urgency); Gemini pulls out the text and numbers it's not built for."""
    from homie import jev

    decided, extracted = await asyncio.gather(jev.read_message(text), complete_json(INTENT_PROMPT, text))
    base = {**fallback_intent(text), **{k: v for k, v in (extracted or {}).items() if v is not None}}
    if decided:
        base.update({k: v for k, v in decided.items() if k in ("intent", "beds", "no_ssn", "require_free_month", "also_repair", "urgency", "_jev")})
        if base.get("intent") == "chat":
            base["intent"] = "other"
    return base


def fallback_intent(text: str) -> dict:
    t = text.lower()
    money = [int(m.replace(",", "")) for m in re.findall(r"\$?\b(\d{1,2},?\d{3})\b", t)]
    fee = re.search(r"\$?(\d{2,4})\s*(?:on|in)?\s*fees", t)
    repair = any(w in t for w in ("broken", "repair", "fix", "leak", "not working", "ice maker"))
    policy = any(w in t for w in ("rights", "deposit", "evict", "legal", "allowed to", "can my landlord", "clause", "policy", "law"))
    search = any(w in t for w in ("apartment", "bedroom", "move", "rent", "lease"))
    return {
        "intent": "repair" if repair else ("policy" if policy else ("search" if search else "other")),
        "city": "Atlanta, GA" if "atlanta" in t else ("Ann Arbor, MI" if "ann arbor" in t else None),
        "area": "downtown Atlanta, GA" if ("downtown" in t or "dwntwn" in t) and "atlanta" in t else None,
        "move_in": (re.search(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2}", t) or [None])[0],
        "beds": _beds(t),
        "max_rent": money[0] if money else None,
        "no_ssn": "ssn" in t,
        "require_free_month": "month free" in t or "free month" in t,
        "fee_cap": int(fee.group(1)) if fee else None,
        "issue": text if repair else None,
    }


def _beds(t: str) -> int | None:
    if "studio" in t:
        return 0
    words = {"one": 1, "two": 2, "three": 3, "four": 4}
    m = re.search(r"\b(\d|one|two|three|four)[\s-]*(?:bed|br\b|bd\b|bedroom)", t)
    return (int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]) if m else None


EXTRACT_PROMPT = """You read a phone call transcript between an AI assistant and a leasing office.
Return JSON with keys: price (int), discount (string), discount_day (int day of month or null),
special_when (string: when the special or a better price applies, e.g. "Wednesdays", "until Oct 31", "move in by the 15th", "first of the month"),
ssn_alternative (string), fees (int), matched (bool, did they agree to match a competitor offer),
payment (string, accepted payment methods), repair_slot (string), summary (one sentence).
Use null for anything not said. Reply with JSON only."""


async def extract_call(transcript: str) -> dict:
    return await complete_json(EXTRACT_PROMPT, transcript) or {}
