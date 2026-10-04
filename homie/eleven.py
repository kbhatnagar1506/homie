"""ElevenLabs accounts with failover: each call uses whichever account has the most credit left.

ELEVENLABS_API_KEY / ELEVENLABS_PHONE_AGENT_ID is the main account; ELEVENLABS_BACKUP_KEY /
ELEVENLABS_BACKUP_PHONE_AGENT_ID is a second account with a copy of the same phone agent.
"""

import logging
import time

import httpx

from homie.config import env

log = logging.getLogger("homie.eleven")
_cache: dict[str, tuple[float, int]] = {}  # key -> (checked at, credits left)


def accounts() -> list[dict]:
    out = []
    for key, agent in ((env("ELEVENLABS_API_KEY"), env("ELEVENLABS_PHONE_AGENT_ID")),
                       (env("ELEVENLABS_BACKUP_KEY"), env("ELEVENLABS_BACKUP_PHONE_AGENT_ID"))):
        if key:
            out.append({"key": key, "phone_agent": agent})
    return out


async def credits_left(key: str) -> int:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            d = (await client.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})).json()
        left = int(d.get("character_limit", 0)) - int(d.get("character_count", 0))
    except Exception as e:
        log.warning("couldn't read ElevenLabs credits: %s", e)
        left = 1  # unknown: still usable, ranked last
    _cache[key] = (time.time(), left)
    return left


async def best(need_phone_agent: bool = False) -> dict | None:
    """The account with the most credit left (and a phone agent, if needed)."""
    ranked = []
    for acct in accounts():
        if need_phone_agent and not acct["phone_agent"]:
            continue
        ranked.append((await credits_left(acct["key"]), acct))
    ranked.sort(key=lambda r: -r[0])
    if ranked:
        log.info("ElevenLabs account picked with %s credits left", ranked[0][0])
    return ranked[0][1] if ranked else None


async def best_key() -> str:
    acct = await best()
    return acct["key"] if acct else env("ELEVENLABS_API_KEY")
