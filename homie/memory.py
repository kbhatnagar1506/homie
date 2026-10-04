"""Homie's memory of what each person wants: budget, area, move-in, must-haves."""

import json
import threading

from homie.config import ROOT
from homie.llm import complete_json

PATH = ROOT / "data" / "memory.json"
_lock = threading.Lock()

EXTRACT = """You maintain a renter's preference profile. Given the current profile (JSON) and their new
message, return the full updated profile as JSON with keys: city, neighborhoods (list), move_in, beds (int),
max_rent (int), no_ssn (bool), must_haves (list, e.g. in-unit laundry, gym, pets, parking, quiet),
deal_breakers (list), school_or_work (string), notes (list of short facts worth remembering).
Keep existing values unless the message changes them. Use null or [] when unknown. JSON only."""


def _load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get(person: str) -> dict:
    return _load().get(person, {})


async def update(person: str, message: str) -> dict:
    current = get(person)
    updated = await complete_json(EXTRACT, f"Profile: {json.dumps(current)}\nMessage: {message}")
    if isinstance(updated, dict):
        with _lock:
            data = _load()
            data[person] = updated
            PATH.write_text(json.dumps(data, indent=2))
        return updated
    return current


def summary(profile: dict) -> str:
    parts = []
    for key, label in [("city", "City"), ("neighborhoods", "Areas"), ("move_in", "Move-in"), ("beds", "Beds"),
                       ("max_rent", "Budget"), ("must_haves", "Must-haves"), ("deal_breakers", "Deal-breakers"),
                       ("school_or_work", "School/work"), ("notes", "Notes")]:
        value = profile.get(key)
        if value not in (None, [], ""):
            parts.append(f"{label}: {', '.join(map(str, value)) if isinstance(value, list) else value}")
    if profile.get("no_ssn"):
        parts.append("No SSN")
    return "; ".join(parts)
