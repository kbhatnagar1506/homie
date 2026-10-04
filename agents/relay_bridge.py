"""Bridge uAgent: connects the Relay contacts to the Homie uAgent over the Chat Protocol."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec

from agents.homie_agent import homie
from homie.config import seed
from relay_app.runtime import RelayTeam

bridge = Agent(name="homie-relay-bridge", seed=seed("relay_bridge"), handle_messages_concurrently=True)
chat = Protocol(spec=chat_protocol_spec)
_ctx: dict[str, Context] = {}


async def send_to_homie(text: str) -> None:
    await _ctx["ctx"].send(homie.address, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                                      content=[TextContent(type="text", text=text)]))


team = RelayTeam(send_to_homie)


@bridge.on_event("startup")
async def start(ctx: Context):
    _ctx["ctx"] = ctx
    task = asyncio.ensure_future(team.start())
    task.add_done_callback(lambda t: t.exception() and ctx.logger.error(f"Relay team stopped: {t.exception()!r}"))


@chat.on_message(ChatMessage)
async def from_homie(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if text:
        await team.on_homie_reply(text)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


bridge.include(chat)
