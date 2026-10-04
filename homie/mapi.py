"""Mapi: long-term, searchable, versioned memory for Homie (https://mapi-jtqptrvpdq-uc.a.run.app/docs)."""

import logging

import httpx

from homie.config import env

log = logging.getLogger(__name__)


def _base() -> tuple[str, dict] | None:
    url, key, space = env("MAPI_URL"), env("MAPI_API_KEY"), env("MAPI_SPACE")
    if not (url and key and space):
        return None
    return f"{url}/v1/spaces/{space}", {"x-api-key": key}


async def remember(content: str, tags: list[str] | None = None, source: str = "homie", metadata: dict | None = None) -> dict:
    base = _base()
    if not base:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{base[0]}/memories", headers=base[1],
                                  json={"content": content, "tags": tags or [], "source": source, "metadata": metadata or {}})
            return r.json() if r.status_code < 300 else {}
    except Exception as e:
        log.warning("Mapi remember failed: %s", e)
        return {}


async def recall(query: str, limit: int = 8, tags: list[str] | None = None) -> list[dict]:
    base = _base()
    if not base:
        return []
    body = {"query": query, "limit": limit}
    if tags:
        body["tags"] = tags
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{base[0]}/search", headers=base[1], json=body)
            r.raise_for_status()
            return [{"content": x["memory"]["content"], "score": x.get("score"), "tags": x["memory"].get("tags", []),
                     "created": x["memory"].get("created_at")} for x in r.json().get("results", [])]
    except Exception as e:
        log.warning("Mapi recall failed: %s", e)
        return []
