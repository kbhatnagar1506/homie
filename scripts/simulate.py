"""Rehearse the full flow locally without ASI:One.

A fake "student" agent sends the demo message to Homie inside one Bureau.

    python -m scripts.simulate                     # apartment search
    python -m scripts.simulate "my ice maker is broken"
"""

import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Bureau, Context
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent

os.environ["AGENTVERSE_MAILBOX"] = "0"  # local rehearsal: no Agentverse connection needed

from agents.homie_agent import homie  # noqa: E402
from agents.specialists import caller, negotiator, memory, paperwork, pictures, policy, repairs

DEMO = ("I'm moving to downtown Atlanta on Aug 20. One-bedroom under $2,000. No SSN. "
        "Book it if there's a month free.")
text = " ".join(sys.argv[1:]) or DEMO

student = Agent(name="student", seed="homie-demo-student-local-only")


@student.on_event("startup")
async def start(ctx: Context):
    ctx.logger.info(f"STUDENT > {text}")
    await ctx.send(homie.address, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                              content=[TextContent(type="text", text=text)]))


@student.on_message(ChatMessage)
async def reply(ctx: Context, sender: str, msg: ChatMessage):
    for c in msg.content:
        if isinstance(c, TextContent):
            print(f"\nHOMIE > {c.text}\n", flush=True)
            if c.text.rstrip().endswith("Studio, 1, 2 or 3?"):
                print("STUDENT > 1 bedroom\n", flush=True)
                await ctx.send(sender, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                                   content=[TextContent(type="text", text="1 bedroom")]))
            if c.text.rstrip().endswith("Approve?"):
                print("STUDENT > Approve\n", flush=True)
                await ctx.send(sender, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                                   content=[TextContent(type="text", text="Approve")]))


@student.on_message(ChatAcknowledgement)
async def ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


if __name__ == "__main__":
    from homie.rpc import register

    register(homie, caller, negotiator, paperwork, repairs, policy, pictures, memory, student)
    Bureau(agents=[homie, caller, negotiator, paperwork, repairs, policy, pictures, memory, student], port=8001).run()
