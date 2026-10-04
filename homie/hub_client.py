"""Pushes live state to the hub (scoreboard, checklist, test apartment site)."""

import logging

import httpx

from homie.config import HUB_URL

log = logging.getLogger(__name__)


async def post(path: str, payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{HUB_URL}{path}", json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:  # the hub is a display; never let it break the agents
        log.warning("hub post %s failed: %s", path, e)
        return {}


async def get(path: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{HUB_URL}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("hub get %s failed: %s", path, e)
        return {}


async def step(name: str, status: str, detail: str = "") -> None:
    await post("/api/checklist", {"step": name, "status": status, "detail": detail})


async def offer(building_id: str, **fields) -> None:
    await post("/api/offers", {"building_id": building_id, **fields})


async def log_event(text: str) -> None:
    await post("/api/log", {"text": text})
