"""Request/response between agents, safe for many requests in flight at once.

uAgents' send_and_receive matches replies by session, so parallel calls to the
same agent collide. Here every request carries a request_id instead.
"""

import asyncio
import time
import uuid

from uagents import Context, Model

from homie.scope import CURRENT_USER

_pending: dict[str, asyncio.Future] = {}
NAMES: dict[str, str] = {}  # agent address -> agent name, for the live agent-to-agent log


def register(*agents) -> None:
    for a in agents:
        NAMES[a.address] = a.name


def address_of(name: str) -> str | None:
    return next((a for a, n in NAMES.items() if n == name), None)


def name_of(address: str) -> str:
    return NAMES.get(address, address[:12] + "…")


def _summary(message: Model) -> str:
    from homie.buildings import name

    d = message.dict()
    if "building_id" in d and "purpose" in d:
        return f"{d['purpose']} {name(d['building_id'])}" + (f": {d['summary']}" if d.get("summary") else "")
    if "offers" in d:
        return f"{len(d['offers'])} offers" + (f", best {name(d['best_building_id'])}" if d.get("best_building_id") else "")
    for key in ("summary", "note", "answer", "question", "issue", "remember", "payment_plan"):
        if d.get(key):
            return str(d[key])[:160]
    if d.get("building_ids"):
        return ("read live prices: " if d.get("scan_prices") else "screenshot: ") + ", ".join(name(b) for b in d["building_ids"])[:160]
    if d.get("images") or d.get("prices"):
        return f"{len(d.get('prices') or [])} prices, {len(d.get('images') or [])} screenshots"
    return type(message).__name__


async def _log(sender: str, receiver: str, message: Model, user: str) -> None:
    from homie import hub_client

    await hub_client.post("/api/agentlog", {"from": sender, "to": receiver, "kind": type(message).__name__,
                                            "summary": _summary(message), "user": user, "ts": time.time()})


async def ask(ctx: Context, destination: str, message: Model, timeout: float) -> Model | None:
    message.request_id = uuid.uuid4().hex
    if hasattr(message, "user") and not message.user:
        message.user = CURRENT_USER.get()
    user = getattr(message, "user", "")
    me, them = ctx.agent.name, name_of(destination)
    asyncio.ensure_future(_log(me, them, message, user))
    from homie import events

    events.handoff(me, them, _summary(message))
    future = asyncio.get_running_loop().create_future()
    _pending[message.request_id] = future
    await ctx.send(destination, message)
    try:
        reply = await asyncio.wait_for(future, timeout)
        asyncio.ensure_future(_log(them, me, reply, user))
        return reply
    except asyncio.TimeoutError:
        return None
    finally:
        _pending.pop(message.request_id, None)


def resolve(message: Model) -> None:
    future = _pending.get(getattr(message, "request_id", ""))
    if future and not future.done():
        future.set_result(message)
