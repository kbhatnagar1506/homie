"""Specialist agents: Caller, Negotiator, Paperwork, Repairs.

Each is its own uAgent with its own address. The Homie agent hands them work
with messages, and Negotiator and Repairs hire the Caller themselves.
"""

import asyncio
import uuid

from uagents import Agent, Context

from homie import hub_client as hub
from homie import mapi
from homie.calls import place_call
from homie.events import team_post
from homie.llm import complete_text
from homie.screenshots import screenshot
from homie.rpc import ask, resolve
from homie.buildings import BUILDINGS, can_call_now, listing_url
from homie.config import PUBLIC_URL, ROOT, env, seed
from homie.models import (
    CallRequest,
    CallResult,
    NegotiateRequest,
    NegotiateResult,
    PolicyRequest,
    PolicyResult,
    PaperworkRequest,
    PaperworkResult,
    MemoryRequest,
    MemoryResult,
    PicturesRequest,
    PicturesResult,
    RepairRequest,
    RepairResult,
)


MAILBOX = env("AGENTVERSE_MAILBOX", "1") == "1"


AVATARS = {"memory": "memory", "caller": "calls", "negotiator": "negotiator", "paperwork": "papers", "repairs": "fix", "policy": "policy", "pictures": "pics"}


def specialist(role: str, description: str, concurrent: bool = True) -> Agent:
    return Agent(
        name=f"homie-{role}", seed=seed(role), mailbox=MAILBOX, handle_messages_concurrently=concurrent,
        description=description, readme_path=str(ROOT / "docs" / "agents" / f"{role}.md"),
        avatar_url=f"{PUBLIC_URL}/avatars/{AVATARS[role]}.png",
    )


caller = specialist("caller", "Phones leasing offices for international students (ElevenLabs + Twilio), always disclosed as an AI. Part of Homie.")
negotiator = specialist("negotiator", "Negotiates apartment offers using competing deals as leverage. Part of Homie.")
paperwork = specialist("paperwork", "No-SSN rental paperwork: documents, applications, cashier's-check plans. Part of Homie.", concurrent=False)
repairs = specialist("repairs", "Files repairs, calls the office, retries and emails until it is booked. Part of Homie.")
policy = specialist("policy", "Plain-English lease and tenant-rights help for Georgia and US renters. Part of Homie.")
memory = specialist("memory", "Long-term memory of everything a renter has told Homie, stored in Mapi. Ask it anything about them. Part of Homie.")
pictures = specialist("pictures", "Opens apartment listings in a real browser and sends screenshots. Part of Homie.")

CALL_TIMEOUT = 300


# ---------- Caller ----------

@caller.on_message(CallRequest, replies=CallResult)
async def on_call(ctx: Context, sender: str, req: CallRequest):
    building = BUILDINGS[req.building_id]
    allowed, why = can_call_now(building)
    if not allowed:
        await hub.offer(req.building_id, status=why)
        await ctx.send(sender, CallResult(request_id=req.request_id, building_id=req.building_id, purpose=req.purpose,
                                          answered=False, summary=f"{building['name']} is {why}, so I didn't call."))
        return
    await hub.offer(req.building_id, status=f"calling ({req.purpose})")
    await hub.log_event(f"Calling {building['name']} to {req.purpose}")
    result = await place_call(building, req.purpose, req.context)
    if req.purpose != "repair":
        fields = {k: result.get(k) for k in ("price", "discount", "fees", "ssn_alternative", "payment") if result.get(k) is not None}
        status = "matched" if result.get("matched") else ("answered" if result.get("answered") else "no answer")
        await hub.offer(req.building_id, status=status, **fields)
    summary = result.get("summary") or f"{building['name']}: {'answered' if result.get('answered') else 'no answer'}"
    await hub.log_event(summary)
    if req.purpose != "repair":
        team_post("calls", summary + (f" No SSN: {result['ssn_alternative']}." if result.get("ssn_alternative") else ""))
    await ctx.send(
        sender,
        CallResult(
            request_id=req.request_id,
            building_id=req.building_id,
            purpose=req.purpose,
            answered=bool(result.get("answered")),
            price=_int(result.get("price")),
            discount=result.get("discount"),
            discount_day=_int(result.get("discount_day")),
            ssn_alternative=result.get("ssn_alternative"),
            fees=_int(result.get("fees")),
            matched=result.get("matched"),
            payment=result.get("payment"),
            repair_slot=result.get("repair_slot"),
            summary=result.get("summary") or "",
        ),
    )


# ---------- Negotiator ----------

def _has_free_month(offer: dict) -> bool:
    return "free" in (offer.get("discount") or "").lower()


@negotiator.on_message(NegotiateRequest, replies=NegotiateResult)
async def on_negotiate(ctx: Context, sender: str, req: NegotiateRequest):
    offers = sorted([o for o in req.offers if o.get("price")], key=lambda o: o["price"])
    leverage = next((o for o in offers if _has_free_month(o)), None)
    targets = [o for o in offers if o is not leverage][:2]
    await hub.step("negotiate", "active", "Calling the best two back")
    if leverage:
        team_post("calls", f"Using {BUILDINGS[leverage['building_id']]['name']}'s offer ({leverage.get('discount')}) as leverage. "
                           f"Calling {', '.join(BUILDINGS[t['building_id']]['name'] for t in targets)} back to ask them to match.")

    async def push(target: dict):
        context = {
            "competitor": BUILDINGS[leverage["building_id"]]["name"] if leverage else "another building",
            "competitor_offer": leverage.get("discount") if leverage else "a lower price",
        }
        reply = await ask(ctx, caller.address,
                          CallRequest(building_id=target["building_id"], purpose="negotiate", context=context), CALL_TIMEOUT)
        if isinstance(reply, CallResult) and reply.matched:
            target.update(price=reply.price or target["price"], discount=reply.discount, matched=True)

    await asyncio.gather(*(push(t) for t in targets))
    ranked = sorted(offers, key=lambda o: (not _has_free_month(o), o["price"] + (o.get("fees") or 0) / 12))
    best = ranked[0] if ranked else None
    note = f"Best: {BUILDINGS[best['building_id']]['name']} at ${best['price']} ({best.get('discount')})" if best else "No offers"
    await hub.step("negotiate", "done", note)
    await ctx.send(sender, NegotiateResult(request_id=req.request_id, offers=offers, best_building_id=best and best["building_id"], note=note))


# ---------- Paperwork ----------

@paperwork.on_message(PaperworkRequest, replies=PaperworkResult)
async def on_paperwork(ctx: Context, sender: str, req: PaperworkRequest):
    alt = req.ssn_alternative.lower()
    docs = ["Passport"]
    if "i-20" in alt or "i20" in alt:
        docs.append("I-20")
    if "bank" in alt or "fund" in alt or "3x" in alt:
        docs.append("Bank statement showing 3x rent")
    if "visa" in alt:
        docs.append("Student visa")
    if "guarantor" in alt:
        docs.append("Guarantor service (e.g. TheGuarantors) instead of a US cosigner")
    payment = (req.payment or "").lower()
    if "cashier" in payment or "money order" in payment:
        plan = ("They only take a cashier's check. Plan: wire the deposit to a US bank account you can open "
                "remotely, then order the cashier's check online for delivery by the deadline, or ask the office "
                "to accept an international wire.")
    else:
        plan = f"Pay through: {req.payment or 'the online portal'}."
    await hub.step("paperwork", "done", "No SSN: " + ", ".join(docs))
    team_post("papers", f"{BUILDINGS[req.building_id]['name']} accepts instead of an SSN: {', '.join(docs)}. "
                        f"Application prepared and the unit is on hold. {plan}")
    await hub.post("/api/application", {"building_id": req.building_id, "documents": docs, "status": "HELD"})
    await ctx.send(sender, PaperworkResult(request_id=req.request_id, documents=docs, payment_plan=plan, application_status="HELD"))


# ---------- Repairs ----------

@repairs.on_message(RepairRequest, replies=RepairResult)
async def on_repair(ctx: Context, sender: str, req: RepairRequest):
    ticket = await hub.post("/api/repairs", {"building_id": req.building_id, "issue": req.issue, "photo_url": req.photo_url})
    ticket_id = ticket.get("ticket_id") or f"R-{uuid.uuid4().hex[:6].upper()}"
    for attempt in (1, 2):
        reply = await ask(ctx, caller.address,
                          CallRequest(building_id=req.building_id, purpose="repair",
                                      context={"issue": req.issue, "ticket_id": ticket_id, "attempt": attempt}),
                          CALL_TIMEOUT)
        if isinstance(reply, CallResult) and reply.answered:
            team_post("fix", f"Ticket {ticket_id}: called {BUILDINGS[req.building_id]['name']} about '{req.issue}'. Booked for {reply.repair_slot}.")
            await hub.post("/api/repairs/update", {"ticket_id": ticket_id, "slot": reply.repair_slot, "status": "booked"})
            await ctx.send(sender, RepairResult(request_id=req.request_id, ticket_id=ticket_id, slot=reply.repair_slot, channel="call",
                                                note=f"Repair booked: {reply.repair_slot}"))
            return
        await hub.log_event(f"No answer on repair call (attempt {attempt}), retrying")
        team_post("fix", f"No answer from the office on try {attempt} for ticket {ticket_id}. Trying again.")
    await hub.post("/api/repairs/update", {"ticket_id": ticket_id, "status": "emailed"})
    await hub.log_event("No answer twice: emailed the office with the photo and ticket")
    team_post("fix", f"Nobody picked up twice, so I emailed the office about ticket {ticket_id} with the photo. I'll keep chasing.")
    await ctx.send(sender, RepairResult(request_id=req.request_id, ticket_id=ticket_id, slot=None, channel="email",
                                        note="Nobody picked up twice, so I emailed the office with the photo. I'll keep chasing."))


# ---------- Policy ----------

POLICY_PROMPT = (
    "You are Homie Policy, a renter's-rights explainer for international students renting in the US, Georgia first. "
    "Answer in plain English in under 120 words: what the law generally says, what to check in the lease, and one next "
    "step. Georgia basics you can rely on: no statutory cap on security deposits; landlords must return the deposit "
    "within one month of move-out with an itemized list of deductions; larger landlords must give a move-in inspection "
    "list before taking a deposit. If the question is about another state, say the rules differ and give the general "
    "principle. Say you're not a lawyer and point to Georgia Legal Aid (georgialegalaid.org) or the student's university "
    "legal services for anything serious."
)


@policy.on_message(PolicyRequest, replies=PolicyResult)
async def on_policy(ctx: Context, sender: str, req: PolicyRequest):
    building = BUILDINGS.get(req.building_id or "", {}).get("name")
    answer = await complete_text(POLICY_PROMPT, [{"role": "user", "content": req.question + (f" (Building: {building})" if building else "")}],
                                 fallback="I couldn't reach my legal notes just now. For anything urgent, Michigan Legal Help (michiganlegalhelp.org) is free.")
    await hub.log_event("Policy question answered")
    await ctx.send(sender, PolicyResult(request_id=req.request_id, answer=answer))


# ---------- Memory (Mapi) ----------

MEMORY_PROMPT = ("You are Homie Memory. Answer the question using only the memories below, in one to three short "
                 "sentences. If they don't cover it, say you don't know that yet. Memories:\n{memories}")


@memory.on_message(MemoryRequest, replies=MemoryResult)
async def on_memory(ctx: Context, sender: str, req: MemoryRequest):
    if req.remember:
        await mapi.remember(req.remember, tags=["profile"], source="homie")
    facts = [m["content"] for m in await mapi.recall(req.question, limit=10)] if req.question else []
    answer = ""
    if req.question:
        answer = await complete_text(MEMORY_PROMPT.format(memories="\n".join(f"- {f}" for f in facts) or "(none)"),
                                     [{"role": "user", "content": req.question}], fallback="; ".join(facts[:3]))
    await ctx.send(sender, MemoryResult(request_id=req.request_id, answer=answer, facts=facts))


# ---------- Pictures ----------

@pictures.on_message(PicturesRequest, replies=PicturesResult)
async def on_pictures(ctx: Context, sender: str, req: PicturesRequest):
    targets = [(req.url, "that listing")] if req.url else [
        (listing_url(BUILDINGS[b]), BUILDINGS[b]["name"]) for b in req.building_ids if b in BUILDINGS]
    images = []
    for url, label in targets:
        name = await screenshot(url)
        if name:
            images.append(f"{PUBLIC_URL}/shots/{name}")
    if images:
        team_post("pics", f"Screenshots of {', '.join(label for _, label in targets)}.", images)
    await hub.log_event(f"Pics: {len(images)} screenshot(s)")
    await ctx.send(sender, PicturesResult(request_id=req.request_id, images=images))


@negotiator.on_message(CallResult)
async def negotiator_call_done(ctx: Context, sender: str, msg: CallResult):
    resolve(msg)


@repairs.on_message(CallResult)
async def repairs_call_done(ctx: Context, sender: str, msg: CallResult):
    resolve(msg)


def _int(value) -> int | None:
    try:
        return int(str(value).replace("$", "").replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None
