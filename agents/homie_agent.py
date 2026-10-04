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

from agents.specialists import caller, memory, negotiator, paperwork, pictures, policy, repairs
from homie import hub_client as hub
from homie.config import PUBLIC_URL, ROOT, env, seed
from homie.buildings import BUILDINGS, use
from homie.llm import parse_intent
from homie.places import search_apartments
from homie.rpc import ask, resolve
from homie.models import (
    CallRequest,
    CallResult,
    NegotiateRequest,
    NegotiateResult,
    PaperworkRequest,
    PaperworkResult,
    MemoryRequest,
    MemoryResult,
    PicturesRequest,
    PicturesResult,
    PolicyRequest,
    PolicyResult,
    RepairRequest,
    RepairResult,
)

homie = Agent(
    name="homie",
    handle=env("HOMIE_HANDLE", "homie-usa"),
    seed=seed("homie"),
    mailbox=env("AGENTVERSE_MAILBOX", env("HOMIE_MAILBOX", "1")) == "1",
    handle_messages_concurrently=True,
    description=(
        "Homie is your person in America. It finds and secures US apartments for international students: "
        "calls every leasing office, negotiates discounts, finds out what they accept instead of an SSN, "
        "holds the unit, and chases repairs after you move in. Starting in downtown Atlanta."
    ),
    readme_path=str(ROOT / "docs" / "agentverse_readme.md"),
    avatar_url=f"{PUBLIC_URL}/avatars/homie.png",
)

chat = Protocol(spec=chat_protocol_spec)
payments = Protocol(spec=payment_protocol_spec, role="seller")

CALL_TIMEOUT = 300
APPROVAL_TIMEOUT = 120
approvals: dict[str, asyncio.Future] = {}
active: set[str] = set()
YES = ("approve", "yes", "yep", "yeah", "do it", "ok", "okay", "sure", "go", "book")
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
    pending = approvals.get(sender)
    if pending and not pending.done():
        pending.set_result(text)
        return
    if sender in active:
        await say(ctx, sender, await status_line())
        return
    saved = ctx.storage.get(f"offer:{sender}") or ctx.storage.get("offer:last")  # ASI:One may reply from a new session address
    if saved and text.lower().lstrip().startswith(YES):
        await finalize(ctx, sender, saved["best"], saved["offers"], saved["req"])
        return
    intent = await parse_intent(text)

    if intent["intent"] == "repair":
        await handle_repair(ctx, sender, intent)
    elif intent["intent"] == "policy":
        await handle_policy(ctx, sender, text)
    elif intent["intent"] == "search":
        intent["_text"] = text
        active.add(sender)
        try:
            await handle_search(ctx, sender, intent)
        finally:
            active.discard(sender)
    else:
        await say(ctx, sender,
                  "I'm Homie, your person in America. Tell me where and when you're moving, your budget, "
                  "and whether you have an SSN, and I'll call every building for you. "
                  "Already moved in? Tell me what's broken and I'll get it fixed.", end=True)


async def handle_search(ctx: Context, sender: str, req: dict) -> None:
    # Ask Homie Memory (Mapi) what we already know, and fill any gaps in this request from it.
    mem = await ask(ctx, memory.address, MemoryRequest(
        question="Apartment preferences: city or neighborhood, budget, move-in date, bedrooms, SSN status, must-haves, deal-breakers",
        remember=req.get("_text", "")), 30)
    if isinstance(mem, MemoryResult) and mem.facts:
        known = await parse_intent(" ".join(mem.facts))
        for key in ("area", "city", "max_rent", "move_in", "beds", "no_ssn"):
            if not req.get(key) and known.get(key):
                req[key] = known[key]
        await hub.log_event("Memory: " + (mem.answer or "; ".join(mem.facts[:3]))[:200])
    area = req.get("area") or req.get("city") or env("DEFAULT_AREA", "downtown Atlanta, GA")
    budget = req.get("max_rent")
    beds = req.get("beds") or 1
    await hub.post("/api/reset", {"request": {**req, "city": area}, "buildings": []})
    for s in STEPS:
        await hub.step(s, "todo")
    await hub.step("find", "active", f"Searching apartments in {area}")
    if env("DEMO_BUILDINGS", "0") != "1":
        try:
            found = [b for b in await search_apartments(area, limit=12) if b.get("phone")]
        except Exception as e:
            ctx.logger.error(f"Places search failed: {e}")
            found = []
        if not found:
            await hub.step("find", "blocked", "Search failed")
            await say(ctx, sender, f"I couldn't search {area} just now. Try again in a minute.", end=True)
            return
        use(found[: int(env("MAX_CALLS", "6"))])
    await hub.post("/api/buildings", {"buildings": list(BUILDINGS.values())})
    open_now = sum(1 for b in BUILDINGS.values() if b.get("open_now"))
    await say(ctx, sender,
              f"On it. Found {len(BUILDINGS)} buildings in {area}"
              f"{f' ({open_now} open right now)' if any(b.get('real') for b in BUILDINGS.values()) else ''}. "
              f"Calling them now{f', looking for a {beds}-bedroom under ${budget}' if budget else ''}"
              f"{', no SSN' if req.get('no_ssn') else ''}. You can go to sleep.")

    # 1. Call every building at once (Caller agent).
    await hub.step("find", "done", f"{len(BUILDINGS)} buildings in {area}")
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
    simulated = "\n(Rehearsal: phone line not connected yet, so these numbers are simulated.)" if env("MOCK_CALLS", "1") == "1" else ""
    await say(ctx, sender, f"Offers so far:\n{lines}{simulated}\n\nCalling the best two back to negotiate.")
    top = [o["building_id"] for o in sorted(offers, key=lambda o: o["price"])[:3]]
    asyncio.ensure_future(ask(ctx, pictures.address, PicturesRequest(building_ids=top), 120))  # Pics screenshots them meanwhile

    # 2. Negotiate (Negotiator agent, which hires the Caller again).
    neg = await ask(ctx, negotiator.address, NegotiateRequest(offers=offers, want_free_month=bool(req.get("require_free_month"))), CALL_TIMEOUT * 2)
    if not isinstance(neg, NegotiateResult) or not neg.best_building_id:
        await say(ctx, sender, "Negotiation stalled. I'll retry in the morning.", end=True)
        return
    best = next(o for o in neg.offers if o["building_id"] == neg.best_building_id)
    name = BUILDINGS[best["building_id"]]["name"]
    ctx.storage.set(f"offer:{sender}", {"best": best, "offers": offers, "req": req})
    ctx.storage.set("offer:last", {"best": best, "offers": offers, "req": req})
    await say(ctx, sender, f"{name} came down to ${best['price']}/mo with {best.get('discount')}. Approve?")
    approvals[sender] = asyncio.get_running_loop().create_future()
    try:
        answer = await asyncio.wait_for(approvals[sender], APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        answer = "approve"  # you asked Homie to book it if the rules match, so silence means go
    finally:
        approvals.pop(sender, None)
    if not answer.lower().strip().startswith(YES):
        await hub.step("held", "blocked", "You asked to keep looking")
        await say(ctx, sender, "Okay, not holding it. I'll keep watching for better deals and ping you.", end=True)
        return
    await hub.log_event("Approved by the student")
    await finalize(ctx, sender, best, offers, req)


async def finalize(ctx: Context, sender: str, best: dict, offers: list[dict], req: dict) -> None:
    name = BUILDINGS[best["building_id"]]["name"]
    ctx.storage.set(f"offer:{sender}", None)
    ctx.storage.set("offer:last", None)

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
    await hub.step("held", "done", f"Held at {name}")
    await hub.post("/api/session", {"sender": sender, "building_id": best["building_id"], "saved": saved})
    ctx.storage.set(f"home:{sender}", best["building_id"])
    await say(ctx, sender,
              f"Held at {name}, ${best['price']}/mo, {best.get('discount')}. You saved ${saved:,}.\n\n"
              f"No SSN needed. They accept: {docs}. Show me those on a video call and I'll send the application.\n\n"
              f"{plan}\n\nYou only pay Homie when you get your keys.", end=not _payments_on())
    if _payments_on():
        await request_fee(ctx, sender, best["building_id"])


async def status_line() -> str:
    state = await hub.get("/api/state")
    done = [k for k, v in (state.get("checklist") or {}).items() if v.get("status") == "done"]
    offers = [o for o in (state.get("offers") or {}).values() if o.get("price")]
    lines = ", ".join(f"{o['name']} ${o['price']} ({o.get('discount') or 'no discount'})" for o in offers)
    return f"Still working on it. Done so far: {', '.join(done) or 'starting calls'}. Offers: {lines or 'calls in progress'}. I'll message you the moment it's held."


async def handle_repair(ctx: Context, sender: str, req: dict) -> None:
    building_id = ctx.storage.get(f"home:{sender}") or next(iter(BUILDINGS))
    await say(ctx, sender, f"Got it. Filing a repair at {BUILDINGS[building_id]['name']} and calling the office now.")
    result = await ask(ctx, repairs.address, RepairRequest(building_id=building_id, issue=req.get("issue") or "Ice maker not working"), CALL_TIMEOUT * 2 + 30)
    if isinstance(result, RepairResult):
        await say(ctx, sender, f"Ticket {result.ticket_id}. {result.note}", end=True)
    else:
        await say(ctx, sender, "The office isn't responding yet. I'll keep chasing and tell you when it's booked.", end=True)


async def handle_policy(ctx: Context, sender: str, question: str) -> None:
    building_id = ctx.storage.get(f"home:{sender}")
    result = await ask(ctx, policy.address, PolicyRequest(question=question, building_id=building_id), 90)
    answer = result.answer if isinstance(result, PolicyResult) else "My policy helper is busy. Try again in a minute."
    await say(ctx, sender, answer, end=True)


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
@homie.on_message(PolicyResult)
@homie.on_message(PicturesResult)
@homie.on_message(MemoryResult)
async def on_specialist_reply(ctx: Context, sender: str, msg):
    resolve(msg)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


homie.include(chat, publish_manifest=True)
homie.include(payments, publish_manifest=True)
