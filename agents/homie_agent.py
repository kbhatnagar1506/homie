"""Homie: the agent students talk to through ASI:One (Chat Protocol).

It turns one message into a plan, hands each step to a specialist agent,
streams progress back into the chat, and reports the outcome.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents.specialists import BUILDINGS, caller, negotiator, paperwork, repairs
from homie import hub_client as hub
from homie.config import ROOT, env, seed
from homie.llm import parse_intent
from homie.rpc import ask, resolve
from homie.models import (
    CallRequest,
    CallResult,
    NegotiateRequest,
    NegotiateResult,
    PaperworkRequest,
    PaperworkResult,
    RepairRequest,
    RepairResult,
)

homie = Agent(
    name="homie",
    seed=seed("homie"),
    mailbox=env("HOMIE_MAILBOX", "1") == "1",
    handle_messages_concurrently=True,
    description=(
        "Homie is your person in America. It finds and secures US apartments for international students: "
        "calls every leasing office, negotiates discounts, finds out what they accept instead of an SSN, "
        "holds the unit, and chases repairs after you move in. Ann Arbor."
    ),
    readme_path=str(ROOT / "docs" / "agentverse_readme.md"),
)

chat = Protocol(spec=chat_protocol_spec)
payments = Protocol(spec=payment_protocol_spec, role="seller")

CALL_TIMEOUT = 300
STEPS = ["find", "offers", "negotiate", "paperwork", "held", "keys"]


async def say(ctx: Context, to: str, text: str, end: bool = False) -> None:
    content = [TextContent(type="text", text=text)]
    if end:
        content.append(EndSessionContent(type="end-session"))
    await ctx.send(to, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content))


@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if not text:
        return
    ctx.logger.info(f"{sender}: {text}")
    intent = await parse_intent(text)

    if intent["intent"] == "repair":
        await handle_repair(ctx, sender, intent)
    elif intent["intent"] == "search":
        await handle_search(ctx, sender, intent)
    else:
        await say(ctx, sender,
                  "I'm Homie, your person in America. Tell me where and when you're moving, your budget, "
                  "and whether you have an SSN, and I'll call every building for you. "
                  "Already moved in? Tell me what's broken and I'll get it fixed.", end=True)


async def handle_search(ctx: Context, sender: str, req: dict) -> None:
    await hub.post("/api/reset", {"request": req})
    for s in STEPS:
        await hub.step(s, "todo")
    budget = req.get("max_rent")
    await say(ctx, sender,
              f"On it. Looking for a {req.get('beds') or 1}-bedroom in {req.get('city') or 'Ann Arbor'}"
              f"{f' under ${budget}' if budget else ''}{' with no SSN' if req.get('no_ssn') else ''}. "
              f"Calling {len(BUILDINGS)} buildings now. You can go to sleep.")

    # 1. Find + call every building at once (Caller agent).
    await hub.step("find", "done", f"{len(BUILDINGS)} buildings match")
    await hub.step("offers", "active", "Calling every building at once")
    replies = await asyncio.gather(*(
        ask(ctx, caller.address, CallRequest(building_id=b, purpose="quote", context={"move_in": req.get("move_in") or "August 20"}), CALL_TIMEOUT)
        for b in BUILDINGS
    ))
    offers = []
    for reply in replies:
        if isinstance(reply, CallResult) and reply.answered and reply.price:
            offers.append(reply.dict())
    if budget:
        offers = [o for o in offers if o["price"] <= budget] or offers
    if not offers:
        await hub.step("offers", "blocked", "No building answered")
        await say(ctx, sender, "Nobody picked up yet. I'll keep calling and email them, and message you when I hear back.", end=True)
        return
    await hub.step("offers", "done", f"{len(offers)} offers")
    lines = "\n".join(f"- {BUILDINGS[o['building_id']]['name']}: ${o['price']}, {o.get('discount') or 'no discount'}" for o in offers)
    await say(ctx, sender, f"Offers so far:\n{lines}\n\nCalling the best two back to negotiate.")

    # 2. Negotiate (Negotiator agent, which hires the Caller again).
    neg = await ask(ctx, negotiator.address, NegotiateRequest(offers=offers, want_free_month=bool(req.get("require_free_month"))), CALL_TIMEOUT * 2)
    if not isinstance(neg, NegotiateResult) or not neg.best_building_id:
        await say(ctx, sender, "Negotiation stalled. I'll retry in the morning.", end=True)
        return
    best = next(o for o in neg.offers if o["building_id"] == neg.best_building_id)
    name = BUILDINGS[best["building_id"]]["name"]
    await say(ctx, sender, f"{name} came down to ${best['price']} with {best.get('discount')}. Holding it for you now.")

    # 3. Deal watcher: if the deal depends on a future discount day, wait for it.
    if best.get("discount_day") and not best.get("matched"):
        await hub.step("held", "active", f"Waiting for the discount on day {best['discount_day']}")
        await say(ctx, sender, f"The discount only applies on day {best['discount_day']}. I'll apply the moment it goes live.")
        await wait_for_day(best["discount_day"])

    # 4. Paperwork without an SSN (Paperwork agent).
    await hub.step("paperwork", "active", "Finding what they accept instead of an SSN")
    pw = await ask(ctx, paperwork.address,
                   PaperworkRequest(building_id=best["building_id"], ssn_alternative=best.get("ssn_alternative") or "Passport and I-20",
                                    payment=best.get("payment"), move_in=req.get("move_in")), 60)
    docs = ", ".join(pw.documents) if isinstance(pw, PaperworkResult) else "passport and I-20"
    plan = pw.payment_plan if isinstance(pw, PaperworkResult) else ""

    saved = _savings(best, offers)
    await hub.step("held", "done", f"Unit 4B at {name}")
    await hub.post("/api/session", {"sender": sender, "building_id": best["building_id"], "saved": saved})
    ctx.storage.set(f"home:{sender}", best["building_id"])
    await say(ctx, sender,
              f"Held: Unit 4B at {name}, ${best['price']}/mo, {best.get('discount')}. You saved ${saved:,}.\n\n"
              f"No SSN needed. They accept: {docs}. Show me those on a video call and I'll send the application.\n\n"
              f"{plan}\n\nYou only pay Homie when you get your keys.", end=not _payments_on())
    if _payments_on():
        await request_fee(ctx, sender, best["building_id"])


async def handle_repair(ctx: Context, sender: str, req: dict) -> None:
    building_id = ctx.storage.get(f"home:{sender}") or next(iter(BUILDINGS))
    await say(ctx, sender, f"Got it. Filing a repair at {BUILDINGS[building_id]['name']} and calling the office now.")
    result = await ask(ctx, repairs.address, RepairRequest(building_id=building_id, issue=req.get("issue") or "Ice maker not working"), CALL_TIMEOUT * 2 + 30)
    if isinstance(result, RepairResult):
        await say(ctx, sender, f"Ticket {result.ticket_id}. {result.note}", end=True)
    else:
        await say(ctx, sender, "The office isn't responding yet. I'll keep chasing and tell you when it's booked.", end=True)


async def wait_for_day(day: int, timeout_s: int = 900) -> None:
    for _ in range(timeout_s // 3):
        state = await hub.get("/api/state")
        if (state.get("clock_day") or 0) >= day:
            return
        await asyncio.sleep(3)


def _savings(best: dict, offers: list[dict]) -> int:
    worst = max(o["price"] for o in offers)
    free_month = best["price"] if "free" in (best.get("discount") or "").lower() else 0
    return (worst - best["price"]) * 12 + free_month


# ---------- Payment Protocol: "you only pay when you get your keys" ----------

def _payments_on() -> bool:
    return env("ENABLE_PAYMENT_REQUEST", "0") == "1"


async def request_fee(ctx: Context, sender: str, building_id: str) -> None:
    await ctx.send(sender, RequestPayment(
        accepted_funds=[Funds(amount=env("HOMIE_FEE_AMOUNT", "1"), currency=env("HOMIE_FEE_CURRENCY", "FET"), payment_method="fet_direct")],
        recipient=str(homie.wallet.address()),
        deadline_seconds=3600,
        reference=f"keys-{building_id}",
        description="Homie fee, due when you get your keys",
    ))


@payments.on_message(CommitPayment)
async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
    ctx.logger.info(f"Payment committed: {msg.transaction_id}")
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    await hub.step("keys", "done", "Paid on keys")
    await say(ctx, sender, "Payment received. Welcome home.", end=True)


@payments.on_message(RejectPayment)
async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
    await say(ctx, sender, "No problem. You'll only pay when you have your keys.", end=True)


@homie.on_message(CallResult)
@homie.on_message(NegotiateResult)
@homie.on_message(PaperworkResult)
@homie.on_message(RepairResult)
async def on_specialist_reply(ctx: Context, sender: str, msg):
    resolve(msg)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


homie.include(chat, publish_manifest=True)
homie.include(payments, publish_manifest=True)
