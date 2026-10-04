"""Small LLM helpers. Every call has a non-LLM fallback so the demo never stalls."""

import json
import logging
import re

from openai import AsyncOpenAI

from homie.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

log = logging.getLogger(__name__)
_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY) if LLM_API_KEY else None


async def complete_json(system: str, user: str) -> dict | None:
    if not _client:
        return None
    try:
        r = await _client.chat.completions.create(
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
    if not _client:
        return fallback
    try:
        r = await _client.chat.completions.create(
            model=LLM_MODEL, messages=[{"role": "system", "content": system}, *messages], temperature=0.6,
        )
        return (r.choices[0].message.content or "").strip() or fallback
    except Exception as e:
        log.warning("LLM call failed: %s", e)
        return fallback


INTENT_PROMPT = """Extract an apartment request into JSON with keys:
intent ("search" | "repair" | "policy" | "status" | "other"; "policy" means a question about a lease, deposit,
tenant rights, landlord rules or the law), city, move_in (string), beds (int),
max_rent (int), no_ssn (bool), require_free_month (bool), fee_cap (int), issue (string, repairs only).
Use null for anything not stated. Reply with JSON only."""


async def parse_intent(text: str) -> dict:
    data = await complete_json(INTENT_PROMPT, text) or {}
    return {**fallback_intent(text), **{k: v for k, v in data.items() if v is not None}}


def fallback_intent(text: str) -> dict:
    t = text.lower()
    money = [int(m.replace(",", "")) for m in re.findall(r"\$?\b(\d{1,2},?\d{3})\b", t)]
    fee = re.search(r"\$?(\d{2,4})\s*(?:on|in)?\s*fees", t)
    repair = any(w in t for w in ("broken", "repair", "fix", "leak", "not working", "ice maker"))
    policy = any(w in t for w in ("rights", "deposit", "evict", "legal", "allowed to", "can my landlord", "clause", "policy", "law"))
    search = any(w in t for w in ("apartment", "bedroom", "move", "rent", "lease"))
    return {
        "intent": "repair" if repair else ("policy" if policy else ("search" if search else "other")),
        "city": "Ann Arbor" if "ann arbor" in t else None,
        "move_in": (re.search(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2}", t) or [None])[0],
        "beds": 2 if "two-bed" in t or "2 bed" in t else 1,
        "max_rent": money[0] if money else None,
        "no_ssn": "ssn" in t,
        "require_free_month": "month free" in t or "free month" in t,
        "fee_cap": int(fee.group(1)) if fee else None,
        "issue": text if repair else None,
    }


EXTRACT_PROMPT = """You read a phone call transcript between an AI assistant and a leasing office.
Return JSON with keys: price (int), discount (string), discount_day (int day of month or null),
ssn_alternative (string), fees (int), matched (bool, did they agree to match a competitor offer),
payment (string, accepted payment methods), repair_slot (string), summary (one sentence).
Use null for anything not said. Reply with JSON only."""


async def extract_call(transcript: str) -> dict:
    return await complete_json(EXTRACT_PROMPT, transcript) or {}
