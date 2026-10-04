"""End-to-end test of the whole Homie team, locally (no Relay, no Agentverse mailbox).

Start the hub first:   uvicorn hub.server:app --port 8080
Then:                  python -m scripts.e2e

Runs real scenarios through all eight uAgents, a fake Twilio call through Gemini Live,
and the Relay team voice, then prints PASS/FAIL for each.
"""

import os

os.environ["AGENTVERSE_MAILBOX"] = "0"

import agents  # noqa: E402,F401
import asyncio  # noqa: E402
import base64  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from uuid import uuid4  # noqa: E402

import httpx  # noqa: E402
from uagents import Agent, Bureau, Context  # noqa: E402
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, EndSessionContent, TextContent  # noqa: E402
from uagents_core.contrib.protocols.payment import RejectPayment, RequestPayment  # noqa: E402

from agents.homie_agent import homie  # noqa: E402
from agents.specialists import caller, later, memory, negotiator, paperwork, pictures, policy, repairs  # noqa: E402
from homie.config import HUB_URL  # noqa: E402
from homie.rpc import register  # noqa: E402

SCENARIOS = [
    ("search", "I'm moving to downtown Atlanta Aug 20, budget 2500, no SSN. Book it if there's a month free.",
     lambda r: any(k in r for k in ("Held at", "Locking in", "I'll call every office", "couldn't search"))),
    ("repair", "my ice maker is broken", lambda r: "Ticket" in r or "chasing" in r),
    ("policy", "can my landlord keep my whole security deposit for one scratch?", lambda r: len(r) > 60),
    ("memory", "what do you know about me?", lambda r: len(r) > 20),
    ("schedule (Later)", "remind me tomorrow at 9am to email my bank statement to Homie Papers", lambda r: "⏰" in r),
    ("pictures", "show me pictures of https://generationatl.com", lambda r: "/shots/" in r or "Screenshot" in r),
    ("keys + Fetch payment", "I just got my keys!!", lambda r: "keys" in r.lower()),
]
REPLIES = {"Studio, 1, 2 or 3?": "1 bedroom", "Approve?": "Approve"}
results: list[tuple[str, bool, str]] = []
student = Agent(name="student", seed="homie-e2e-student-local-only")
state = {"i": 0, "transcript": [], "started": 0.0}


async def send(ctx: Context, text: str):
    await ctx.send(homie.address, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=[TextContent(type="text", text=text)]))


@student.on_event("startup")
async def start(ctx: Context):
    await asyncio.sleep(2)
    await next_scenario(ctx)


async def next_scenario(ctx: Context):
    if state["i"] >= len(SCENARIOS):
        await finish()
        return
    name, text, _ = SCENARIOS[state["i"]]
    state["transcript"], state["started"] = [], time.time()
    print(f"\n=== {name}: {text}", flush=True)
    await send(ctx, text)
    asyncio.ensure_future(timeout(ctx, state["i"]))


async def timeout(ctx: Context, index: int):
    await asyncio.sleep(420)
    if state["i"] == index:
        record(ctx, ended=False)
        state["i"] += 1
        await next_scenario(ctx)


def record(ctx, ended: bool):
    name, _, check = SCENARIOS[state["i"]]
    reply = "\n".join(state["transcript"])
    ok = ended and check(reply)
    results.append((f"Scenario: {name}", ok, f"{len(state['transcript'])} replies in {time.time() - state['started']:.0f}s; last: {state['transcript'][-1][:110] if state['transcript'] else '-'}"))


@student.on_message(ChatMessage)
async def on_reply(ctx: Context, sender: str, msg: ChatMessage):
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent))
    ended = any(isinstance(c, EndSessionContent) for c in msg.content)
    if text:
        state["transcript"].append(text)
        print(f"HOMIE > {text[:300]}", flush=True)
        for cue, answer in REPLIES.items():
            if text.rstrip().endswith(cue):
                print(f"STUDENT > {answer}", flush=True)
                await send(ctx, answer)
    if ended:
        record(ctx, ended=True)
        state["i"] += 1
        await next_scenario(ctx)


@student.on_message(RequestPayment)
async def on_payment_request(ctx: Context, sender: str, req: RequestPayment):
    print(f"STUDENT got payment request: {req.accepted_funds[0].amount} {req.accepted_funds[0].currency} ({req.description}), declining for the test", flush=True)
    await ctx.send(sender, RejectPayment(reason="e2e test"))


@student.on_message(ChatAcknowledgement)
async def ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


async def voice_test() -> tuple[bool, str]:
    """A fake Twilio media stream into the hub: proves Gemini Live answers and speaks over phone audio."""
    import websockets

    async with httpx.AsyncClient(timeout=20) as client:
        key = (await client.post(f"{HUB_URL}/api/calls", json={
            "building_id": "e2e", "prompt": "You are Homie Calls, an AI assistant. Greet the office in one short sentence.",
            "greeting": "The office just picked up. Start the call."})).json()["key"]
    url = HUB_URL.replace("http", "ws") + f"/twilio/stream/{key}"
    silence = base64.b64encode(b"\xff" * 160).decode()  # 20 ms of mu-law silence
    got_audio = 0
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        await ws.send(json.dumps({"event": "start", "start": {"streamSid": "MZe2e", "callSid": "CAe2e", "accountSid": "AC", "tracks": ["inbound"],
                                                                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}, "streamSid": "MZe2e"}))
        end = time.time() + 25
        while time.time() < end and got_audio < 25:
            await ws.send(json.dumps({"event": "media", "streamSid": "MZe2e", "media": {"payload": silence}}))
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 0.02))
                if msg.get("event") == "media":
                    got_audio += 1
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.02)
    return got_audio >= 25, f"{got_audio} audio frames spoken back by Gemini Live"


async def team_voice_test() -> tuple[bool, str]:
    from relay_app.runtime import RelayTeam

    team = RelayTeam(send_to_homie=lambda text: asyncio.sleep(0))
    sent: list[tuple[str, str]] = []

    async def fake_send(role, chat_id, text, buttons=None, extra=None):
        sent.append((role, text))

    team.send = fake_send
    team.state = {"direct": {"homie": "x"}, "team_chat": "x", "hello": []}
    await team.on_team_post("calls", "Generation Atlanta quoted $1,915 for a 1-bedroom, one month free on a 13-month lease.")
    await team.on_handoff("homie", "homie-pictures", "screenshot: Generation Atlanta")
    await asyncio.sleep(2)
    has_emoji = any(any(ord(ch) > 0x2600 for ch in t) for _, t in sent)
    keeps_numbers = any("1,915" in t or "1915" in t for _, t in sent)
    return len(sent) >= 2 and has_emoji and keeps_numbers, " | ".join(f"{r}: {t[:90]}" for r, t in sent)


async def finish():
    for name, coro in (("Real-time voice (Twilio stream → Gemini Live)", voice_test()), ("Relay team voice + handoffs", team_voice_test())):
        try:
            ok, detail = await coro
        except Exception as e:
            ok, detail = False, repr(e)[:200]
        results.append((name, ok, detail))
    width = max(len(n) for n, _, _ in results)
    print("\n" + "\n".join(f"{'PASS' if ok else 'FAIL'}  {n:<{width}}  {d}" for n, ok, d in results), flush=True)
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} passed", flush=True)
    os._exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    if "--voice-only" in sys.argv:
        asyncio.run(finish())
    register(homie, caller, negotiator, paperwork, repairs, policy, pictures, memory, later, student)
    Bureau(agents=[homie, caller, negotiator, paperwork, repairs, policy, pictures, memory, later, student], port=8002).run()
