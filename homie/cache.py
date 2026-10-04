"""Demo cache: Places results and Scout reads (facts + screenshots) saved to disk, so a demo never waits on or breaks with Google.

Live results always refresh the cache; when Places fails (or DEMO_MODE=1 and a cached copy exists) the cache answers instantly.
"""

import json
import re
import time

from homie.config import ROOT

DIR = ROOT / "data" / "cache"
# Phrases a judge might type -> one canonical search, so "UC Berkeley", "Cal" and "berkeley off campus" share a cache.
ALIASES = {
    "berkeley": "UC Berkeley off campus, Berkeley, CA",
    "cal ": "UC Berkeley off campus, Berkeley, CA",
    "san francisco": "San Francisco, CA",
    " sf": "San Francisco, CA",
    "stanford": "near Stanford University, Palo Alto, CA",
    "palo alto": "near Stanford University, Palo Alto, CA",
    "georgia tech": "near Georgia Tech, Atlanta, GA",
    "gatech": "near Georgia Tech, Atlanta, GA",
    "gt ": "near Georgia Tech, Atlanta, GA",
    "atlanta": "downtown Atlanta, GA",
    "umich": "near University of Michigan, Ann Arbor, MI",
    "university of michigan": "near University of Michigan, Ann Arbor, MI",
    "ann arbor": "near University of Michigan, Ann Arbor, MI",
}


def canonical(area: str) -> str:
    a = f" {area.lower()} "
    for key, canon in ALIASES.items():
        if key in a:
            return canon
    return area


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def _path(kind: str, key: str):
    return DIR / kind / f"{_slug(key)}.json"


def get(kind: str, key: str):
    try:
        return json.loads(_path(kind, key).read_text())["value"]
    except Exception:
        return None


def put(kind: str, key: str, value) -> None:
    p = _path(kind, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"saved": time.time(), "value": value}))
