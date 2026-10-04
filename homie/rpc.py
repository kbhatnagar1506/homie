"""Request/response between agents, safe for many requests in flight at once.

uAgents' send_and_receive matches replies by session, so parallel calls to the
same agent collide. Here every request carries a request_id instead.
"""

import asyncio
import uuid

from uagents import Context, Model

_pending: dict[str, asyncio.Future] = {}


async def ask(ctx: Context, destination: str, message: Model, timeout: float) -> Model | None:
    message.request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending[message.request_id] = future
    await ctx.send(destination, message)
    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _pending.pop(message.request_id, None)


def resolve(message: Model) -> None:
    future = _pending.get(getattr(message, "request_id", ""))
    if future and not future.done():
        future.set_result(message)
