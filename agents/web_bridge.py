"""Web bridge uAgent: lets you talk to the Homie team from the /chat page instead of Relay.

Run with WEB_CHAT=1. What you type on /chat goes to Homie over the Chat Protocol; Homie's replies,
every teammate's update and every agent-to-agent handoff are mirrored back to the page.
"""

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec

from agents.homie_agent import homie
from homie import events
from homie import hub_client as hub
from homie.config import seed

web_bridge = Agent(name="homie-web-bridge", seed=seed("relay_bridge") + "-web", handle_messages_concurrently=True)
chat = Protocol(spec=chat_protocol_spec)
_ctx: dict[str, Context] = {}
LINK = re.compile(r"https?://\S+")
BUTTONS = {"Approve?": ["Approve", "Keep looking"], "Studio, 1, 2 or 3?": ["Studio", "1 bedroom", "2 bedrooms", "3 bedrooms"]}


async def send_to_homie(text: str) -> None:
    await _ctx["ctx"].send(homie.address, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                                      content=[TextContent(type="text", text=text)]))


async def mirror(role: str, text: str, buttons: list[str] | None = None, images: list[str] | None = None, kind: str = "message") -> None:
    links = LINK.findall(text)
    clean = LINK.sub("", text).strip() if links else text
    await hub.post("/api/chat/mirror", {"role": role, "text": clean, "links": links, "buttons": buttons or [], "images": images or [], "kind": kind})


async def on_team_post(role: str, text: str, images: list[str]) -> None:
    await mirror(role, text, images=images)


async def on_handoff(sender: str, receiver: str, summary: str) -> None:
    await mirror(sender, f"→ {receiver}: {summary}", kind="handoff")


async def pump() -> None:
    """Hand what you type on /chat to Homie."""
    while True:
        await asyncio.sleep(1)
        for text in (await hub.post("/api/chat/claim", {})).get("items", []):
            await send_to_homie(text)


@web_bridge.on_event("startup")
async def start(ctx: Context):
    _ctx["ctx"] = ctx
    events.subscribe(on_team_post)
    events.subscribe_handoffs(on_handoff)
    asyncio.ensure_future(pump())


@chat.on_message(ChatMessage)
async def from_homie(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if text:
        buttons = next((b for cue, b in BUTTONS.items() if text.rstrip().endswith(cue)), None)
        await mirror("homie", text, buttons=buttons)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


web_bridge.include(chat)
