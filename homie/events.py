"""In-process event bus: specialist agents announce what they did, and the
Relay runtime posts it from the matching Relay contact."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)
_listeners: list[Callable[[str, str], Awaitable[None]]] = []


def subscribe(fn: Callable[[str, str], Awaitable[None]]) -> None:
    _listeners.append(fn)


def team_post(role: str, text: str) -> None:
    """role: homie | calls | papers | fix | policy"""
    for fn in _listeners:
        task = asyncio.ensure_future(fn(role, text))
        task.add_done_callback(lambda t: t.exception() and log.warning("team_post failed: %s", t.exception()))
