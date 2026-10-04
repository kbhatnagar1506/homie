"""Specialist agents: Caller, Negotiator, Paperwork, Repairs.

Each is its own uAgent with its own address. The Homie agent hands them work
with messages, and Negotiator and Repairs hire the Caller themselves.
"""

import asyncio
import json
import uuid
from datetime import datetime

from uagents import Agent, Context

from homie import hub_client as hub
from homie import mapi
from homie.calls import place_call
from homie import events
from homie.events import team_post
from homie.llm import complete_text
from homie.llm import complete_json
from homie.screenshots import read_listing, screenshot
from homie.rpc import ask, resolve
from homie.scope import CURRENT_USER
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
    ScoutRequest,
    ScoutResult,
    ScheduleRequest,
    ScheduleResult,
    TaskDue,
    MemoryRequest,
    MemoryResult,
    PicturesRequest,
    PicturesResult,
    RepairRequest,
    RepairResult,
    ApplyRequest,
    ApplyResult,
    VibeDeck,
    VibeRequest,
    VibeResult,
    VibeWait,
)


MAILBOX = env("AGENTVERSE_MAILBOX", "1") == "1"


async def _log_activity(role: str, text: str, images: list[str]) -> None:
    """Everything the team tells the renter also lands in Mapi, so Homie Memory knows what happened and when."""
    await mapi.remember(f"Homie {role} update: {text}", tags=["activity", role], source="homie-team")


events.subscribe(_log_activity)


AVATARS = {"vibecheck": "vibecheck", "scout": "scout", "later": "later", "memory": "memory", "caller": "calls", "negotiator": "negotiator", "paperwork": "papers", "repairs": "fix", "policy": "policy", "pictures": "pics"}


def specialist(role: str, description: str, concurrent: bool = True) -> Agent:
    return Agent(
        name=f"homie-{role}", seed=seed(role), mailbox=MAILBOX, handle_messages_concurrently=concurrent,
        description=description, readme_path=str(ROOT / "docs" / "agents" / f"{role}.md"),
        avatar_url=f"{PUBLIC_URL}/avatars/{AVATARS[role]}.png",
    )


caller = specialist("caller", "Phones leasing offices for international students, many at once (Gemini Live voice over Twilio), always disclosed as an AI. Part of Homie.")
negotiator = specialist("negotiator", "Scores apartment offers and negotiates the top two using the best competing deal as leverage. Part of Homie.")
paperwork = specialist("paperwork", "No-SSN rental paperwork: documents, applications, cashier's-check plans. Part of Homie.", concurrent=False)
repairs = specialist("repairs", "Repairs: sees the problem on a video call, files the ticket with a photo, and chases the office until it is booked. Part of Homie.")
policy = specialist("policy", "Plain-English lease and tenant-rights help for Georgia and US renters. Part of Homie.")
scout = specialist("scout", "Reads a building's whole website: floor plans, live prices, specials, fees, pet and parking policy, how to apply, no-SSN rules. Part of Homie.")
vibecheck = specialist("vibecheck", "Vibe check: turns the buildings Scout found into swipe cards (left/right) and learns the renter's taste. Part of Homie.")
later = specialist("later", "The waiting agent: holds future tasks (get offers Monday, call when the office opens, rent reminders) and runs them on time. Part of Homie.")
memory = specialist("memory", "Long-term memory of everything a renter has told Homie, stored in Mapi. Ask it anything about them. Part of Homie.")
pictures = specialist("pictures", "Screenshots any apartment listing in a real browser and reads live prices off building websites. Part of Homie.")

CALL_TIMEOUT = 300


# ---------- Caller ----------

@caller.on_message(CallRequest, replies=CallResult)
async def on_call(ctx: Context, sender: str, req: CallRequest):
    CURRENT_USER.set(req.user)
    building = BUILDINGS[req.building_id]
    allowed, why = can_call_now(building)
    if not allowed and env("DEMO_MODE", "0") == "1":
        # Demo: the office is closed, so play a clearly-labelled simulated call instead of skipping it.
        from homie.calls import simulated_live_call

        await hub.log_event(f"📞 Calling {building['name']} · {building.get('phone_display') or building.get('phone') or ''} (demo)")
        result = await simulated_live_call(building, req.purpose, req.context)
        allowed = None
    if allowed is False:
        await hub.offer(req.building_id, status=why)
        await ctx.send(sender, CallResult(request_id=req.request_id, building_id=req.building_id, purpose=req.purpose,
                                          answered=False, summary=f"{building['name']} is {why}, so I didn't call."))
        return
    if allowed:
        await hub.offer(req.building_id, status=f"dialing ({req.purpose})")
        await hub.log_event(f"📞 Dialing {building['name']} · {building.get('phone_display') or building.get('phone') or 'no number'} · {req.purpose}")
        result = await place_call(building, req.purpose, req.context)
        if env("DEMO_MODE", "0") == "1" and not result.get("answered"):
            from homie.calls import simulated_live_call

            await hub.log_event(f"📞 {building['name']} didn't pick up, running the demo call")
            result = await simulated_live_call(building, req.purpose, req.context)
    if result.get("transcript"):
        from homie import jev

        facts = await jev.call_outcome(result["transcript"])
        if facts:
            result.setdefault("matched", facts.get("matched", 0) >= 0.6)
            if facts.get("reached_person", 1) < 0.4:
                result["answered"] = False
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
            special_when=result.get("special_when") or None,
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
    CURRENT_USER.set(req.user)
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
    ranked = await rank_offers(offers)
    best = ranked[0] if ranked else None
    note = f"Best: {BUILDINGS[best['building_id']]['name']} at ${best['price']} ({best.get('discount')})" if best else "No offers"
    await hub.step("negotiate", "done", note)
    await ctx.send(sender, NegotiateResult(request_id=req.request_id, offers=offers, best_building_id=best and best["building_id"], note=note))


async def rank_offers(offers: list[dict]) -> list[dict]:
    """Composite score: price in code (0-1, cheaper is better) + Jev's judged special value, no-SSN friendliness and fit."""
    from homie import jev

    renter = "; ".join(m["content"] for m in await mapi.recall("apartment preferences must-haves budget", limit=5, tags=["profile"]))
    scores = await asyncio.gather(*(jev.score_offer({"name": BUILDINGS[o["building_id"]]["name"], "price": o["price"],
                                                     "special": o.get("discount"), "no_ssn_policy": o.get("ssn_alternative")},
                                                    renter or "International student, no SSN") for o in offers))
    lo, hi = min(o["price"] for o in offers), max(o["price"] for o in offers)
    for o, sc in zip(offers, scores):
        price = 1 - (o["price"] - lo) / (hi - lo) if hi > lo else 1.0
        if sc:
            o["score"] = round(0.45 * price + 0.25 * sc["special"] + 0.2 * sc["no_ssn_ok"] + 0.1 * sc["fits"], 3)
        else:
            o["score"] = round(0.6 * price + (0.4 if _has_free_month(o) else 0), 3)
    return sorted(offers, key=lambda o: -o["score"])


# ---------- Paperwork ----------

@paperwork.on_message(PaperworkRequest, replies=PaperworkResult)
async def on_paperwork(ctx: Context, sender: str, req: PaperworkRequest):
    CURRENT_USER.set(req.user)
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
    CURRENT_USER.set(req.user)
    from homie import jev

    if await jev.is_emergency(req.issue):
        team_post("fix", f"This sounds like an emergency ({req.issue}). If there's gas, fire or flooding, get out and call 911 first. "
                         "I'm calling the office's emergency line right now.")
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
    CURRENT_USER.set(req.user)
    building = BUILDINGS.get(req.building_id or "", {}).get("name")
    known = "; ".join(m["content"] for m in await mapi.recall(req.question, limit=5))
    answer = await complete_text(POLICY_PROMPT + (f"\nWhat you know about this renter: {known}" if known else ""),
                                 [{"role": "user", "content": req.question + (f" (Building: {building})" if building else "")}],
                                 fallback="I couldn't reach my legal notes just now. For anything urgent, Michigan Legal Help (michiganlegalhelp.org) is free.")
    await hub.log_event("Policy question answered")
    await ctx.send(sender, PolicyResult(request_id=req.request_id, answer=answer))


# ---------- Scout: reads a building's whole website ----------

@scout.on_message(ScoutRequest, replies=ScoutResult)
async def on_scout(ctx: Context, sender: str, req: ScoutRequest):
    CURRENT_USER.set(req.user)
    from homie.scout import crawl

    url = req.url or BUILDINGS.get(req.building_id, {}).get("website") or ""
    if not url:
        await ctx.send(sender, ScoutResult(request_id=req.request_id, facts={}, shots=[], pages=0))
        return
    await hub.log_event(f"Scout: reading every page of {url}")
    from homie import cache

    out = cache.get("scout", req.building_id) if env("DEMO_MODE", "0") == "1" else None
    if not out:
        try:
            out = await crawl(url, max_pages=req.max_pages or 8)
        except Exception as e:
            ctx.logger.error(f"Scout crawl failed: {e!r}")
            out = cache.get("scout", req.building_id) or {"facts": {}, "shots": [], "pages": 0}
        if out.get("pages"):
            cache.put("scout", req.building_id, out)
    ctx.logger.info(f"Scout read {out['pages']} pages of {url}: {[(p.get('name'), p.get('price')) for p in (out['facts'] or {}).get('floor_plans') or []]}")
    shots = [f"{PUBLIC_URL}/shots/{n}" for n in out["shots"]]
    await hub.log_event(f"Scout: read {out['pages']} pages, {len((out['facts'] or {}).get('floor_plans') or [])} floor plans")
    await ctx.send(sender, ScoutResult(request_id=req.request_id, facts=out["facts"], shots=shots, pages=out["pages"]))


# ---------- Later: the waiting agent ----------

WHEN_PROMPT = """Today is {now} in Atlanta (America/New_York). The renter wants something done later: "{text}".
Return JSON {{"when_iso": ISO 8601 datetime with -04:00 or -05:00 offset, "explicit": true if they named a time or day}}.
Business-type tasks default to 10:15 AM on the named day; "when they open" means 10:15 AM the next business day. JSON only."""


async def _publish_tasks(ctx: Context) -> None:
    from homie.schedule import human

    tasks = ctx.storage.get("tasks") or []
    await hub.post("/api/tasks", {"tasks": [{**{k: t[k] for k in ("task_id", "kind", "text", "when_iso", "user")},
                                             "when_human": human(datetime.fromisoformat(t["when_iso"]))} for t in tasks]})


@later.on_message(ScheduleRequest, replies=ScheduleResult)
async def on_schedule(ctx: Context, sender: str, req: ScheduleRequest):
    CURRENT_USER.set(req.user)
    from homie import jev
    from homie.schedule import TZ, human, next_open

    kind = req.kind or await jev.task_kind(req.text) or "remind"
    when = None
    if req.when_iso:
        when = datetime.fromisoformat(req.when_iso)
    else:
        parsed = await complete_json(WHEN_PROMPT.format(now=datetime.now(TZ).strftime("%A %Y-%m-%d %H:%M"), text=req.text), req.text)
        try:
            when = datetime.fromisoformat(parsed["when_iso"]) if parsed and parsed.get("when_iso") else None
        except ValueError:
            when = None
    if not when or when <= datetime.now(TZ):
        when = next_open([])
    task = {"task_id": f"T-{uuid.uuid4().hex[:6].upper()}", "kind": kind, "text": req.text, "when_iso": when.isoformat(),
            "user": req.user, "payload": req.payload}
    ctx.storage.set("tasks", (ctx.storage.get("tasks") or []) + [task])
    asyncio.ensure_future(_wait_and_run(ctx, task))
    await _publish_tasks(ctx)
    note = f"⏰ {human(when)}: {req.text}"
    await hub.log_event(f"Homie Later scheduled {task['task_id']} ({kind}) for {human(when)}")
    await ctx.send(sender, ScheduleResult(request_id=req.request_id, task_id=task["task_id"], kind=kind,
                                          when_iso=task["when_iso"], when_human=human(when), note=note))


async def _wait_and_run(ctx: Context, task: dict) -> None:
    from homie.rpc import address_of
    from homie.schedule import TZ

    delay = (datetime.fromisoformat(task["when_iso"]) - datetime.now(TZ)).total_seconds()
    await asyncio.sleep(max(0, delay))
    remaining = [t for t in (ctx.storage.get("tasks") or []) if t["task_id"] != task["task_id"]]
    if len(remaining) == len(ctx.storage.get("tasks") or []):
        return  # cancelled
    ctx.storage.set("tasks", remaining)
    await _publish_tasks(ctx)
    CURRENT_USER.set(task.get("user", ""))
    await hub.log_event(f"Homie Later: running {task['task_id']} ({task['kind']})")
    homie_addr = address_of("homie")
    if homie_addr:
        await ctx.send(homie_addr, TaskDue(task_id=task["task_id"], kind=task["kind"], text=task["text"],
                                           payload=task["payload"], user=task.get("user", "")))


@later.on_event("startup")
async def resume_tasks(ctx: Context):
    for task in ctx.storage.get("tasks") or []:
        asyncio.ensure_future(_wait_and_run(ctx, task))
    await _publish_tasks(ctx)


# ---------- Memory (Mapi) ----------

MEMORY_PROMPT = ("You are Homie Memory. Answer the question using only the memories below, in one to three short "
                 "sentences. If they don't cover it, say you don't know that yet. Memories:\n{memories}")


@memory.on_message(MemoryRequest, replies=MemoryResult)
async def on_memory(ctx: Context, sender: str, req: MemoryRequest):
    CURRENT_USER.set(req.user)
    if req.remember:
        await mapi.remember(req.remember, tags=["profile"], source="homie")
    facts = [m["content"] for m in await mapi.recall(req.question, limit=10)] if req.question else []
    answer = ""
    if req.question:
        answer = await complete_text(MEMORY_PROMPT.format(memories="\n".join(f"- {f}" for f in facts) or "(none)"),
                                     [{"role": "user", "content": req.question}], fallback="; ".join(facts[:3]))
    await ctx.send(sender, MemoryResult(request_id=req.request_id, answer=answer, facts=facts))


# ---------- Pictures ----------

PRICE_PROMPT = """From this apartment website text, find the lowest advertised monthly rent for a {beds} and any current
special or concession. Return JSON {{"price": int or null, "special": string or null, "available": string or null}}.
Only use numbers that appear in the text. JSON only."""


async def scan_prices(building_ids: list[str], beds: int | None) -> tuple[list[dict], list[str]]:
    label = "studio" if beds == 0 else f"{beds or 1}-bedroom"
    browsers = asyncio.Semaphore(int(env("BROWSER_CONCURRENCY", "2")))

    async def one(b: str):
        building = BUILDINGS[b]
        if not building.get("website"):
            return None, None
        async with browsers:
            page = await read_listing(building["website"])
        found = await complete_json(PRICE_PROMPT.format(beds=label), page["text"]) if page["text"] else None
        shot = f"{PUBLIC_URL}/shots/{page['shot']}" if page.get("shot") else None
        if found and found.get("price"):
            await hub.offer(b, status="advertised online", price=_int(found["price"]), discount=found.get("special"))
            return {"building_id": b, "price": _int(found["price"]), "special": found.get("special"),
                    "available": found.get("available"), "beds": beds}, shot
        await hub.offer(b, status="no price online")
        return None, shot

    results = await asyncio.gather(*(one(b) for b in building_ids if b in BUILDINGS))
    return [r for r, _ in results if r], [s for _, s in results if s]


@pictures.on_message(PicturesRequest, replies=PicturesResult)
async def on_pictures(ctx: Context, sender: str, req: PicturesRequest):
    CURRENT_USER.set(req.user)
    if req.scan_prices:
        prices, shots = await scan_prices(req.building_ids, req.beds)
        lines = ", ".join(f"{BUILDINGS[p['building_id']]['name']} ${p['price']}" for p in sorted(prices, key=lambda p: p["price"]))
        team_post("pics", f"Read every building's website for live prices: {lines or 'no prices posted online'}.", shots[:4])
        await ctx.send(sender, PicturesResult(request_id=req.request_id, images=shots, prices=prices))
        return
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


# ---------- Vibecheck: swipe cards ----------

VIBE_PROMPT = """You write swipe cards for apartment buildings, for an international student choosing from abroad.
For each building return a short, honest vibe line (max 14 words, no hype words like "luxury", no em dashes) and 3 short tags
(neighborhood feel, standout amenity or perk, who it suits). Use only the facts given.
Return JSON {"cards": [{"building_id": str, "vibe": str, "tags": [str, str, str]}]}."""


@vibecheck.on_message(VibeRequest, replies=VibeDeck)
async def on_vibe(ctx: Context, sender: str, req: VibeRequest):
    CURRENT_USER.set(req.user)
    facts = []
    for bid in req.building_ids:
        b, site = BUILDINGS.get(bid, {}), (req.site or {}).get(bid, {})
        facts.append({"building_id": bid, "name": b.get("name"), "address": b.get("address"), "rating": b.get("rating"),
                      "price": site.get("price"), "special": site.get("special"), "amenities": site.get("amenities"), "notes": site.get("notes")})
    vibes = {c["building_id"]: c for c in ((await complete_json(VIBE_PROMPT, json.dumps(facts)) or {}).get("cards") or []) if c.get("building_id")}
    cards = []
    for f in facts:
        site = (req.site or {}).get(f["building_id"], {})
        v = vibes.get(f["building_id"], {})
        cards.append({**f, "per_bed": site.get("per_bed"), "shots": site.get("shots") or [], "vibe": v.get("vibe") or "",
                      "tags": (v.get("tags") or [])[:3], "website": BUILDINGS.get(f["building_id"], {}).get("website"),
                      "beds": req.beds, "over_budget": bool(req.budget and f.get("price") and f["price"] > req.budget)})
    deck_id = uuid.uuid4().hex[:10]
    await hub.post("/api/vibe/decks", {"deck_id": deck_id, "user": req.user, "cards": cards})
    await hub.log_event(f"✨ Vibecheck built {len(cards)} swipe cards")
    await ctx.send(sender, VibeDeck(request_id=req.request_id, deck_id=deck_id, url=f"{PUBLIC_URL}/vibe/{deck_id}", cards=len(cards)))


TASTE_PROMPT = """A renter swiped on apartment buildings. In one friendly sentence (max 25 words, no em dashes), say what their taste seems to be,
based on what they liked versus passed. Return JSON {"taste": str}."""


@vibecheck.on_message(VibeWait, replies=VibeResult)
async def on_vibe_wait(ctx: Context, sender: str, req: VibeWait):
    CURRENT_USER.set(req.user)
    deck, end = {}, asyncio.get_event_loop().time() + req.wait_seconds
    while asyncio.get_event_loop().time() < end:
        deck = await hub.get(f"/api/vibe/{req.deck_id}") or {}
        if deck.get("done"):
            break
        await asyncio.sleep(2)
    swipes = deck.get("swipes") or {}
    liked = [b for b, d in swipes.items() if d == "right"]
    passed = [b for b, d in swipes.items() if d == "left"]
    taste = ""
    if liked or passed:
        cards = {c["building_id"]: c for c in deck.get("cards") or []}
        brief = lambda ids: [{k: cards[i].get(k) for k in ("name", "price", "vibe", "tags", "special")} for i in ids if i in cards]  # noqa: E731
        taste = ((await complete_json(TASTE_PROMPT, json.dumps({"liked": brief(liked), "passed": brief(passed)}))) or {}).get("taste", "")
        if taste:
            await mapi.remember(f"Vibe check: {taste} Liked {', '.join(cards[i]['name'] for i in liked if i in cards) or 'none'}.",
                                tags=["profile", "vibecheck"], source="vibecheck")
    await hub.log_event(f"✨ Vibe check {'done' if deck.get('done') else 'timed out'}: {len(liked)} liked, {len(passed)} passed")
    await ctx.send(sender, VibeResult(request_id=req.request_id, deck_id=req.deck_id, liked=liked, passed=passed, taste=taste, done=bool(deck.get("done"))))


# ---------- Papers: the real application, live in a browser ----------

@paperwork.on_message(ApplyRequest, replies=ApplyResult)
async def on_apply(ctx: Context, sender: str, req: ApplyRequest):
    CURRENT_USER.set(req.user)
    from homie import apply

    building = BUILDINGS.get(req.building_id, {"name": req.building_id})
    if not apply.available() or not req.url or not req.email:
        await ctx.send(sender, ApplyResult(request_id=req.request_id, error="no browser agent or application link"))
        return
    await hub.log_event(f"🖥️ Papers is opening {building['name']}'s application: {req.url[:80]}")
    out = await apply.start(building, req.url, req.move_in or "", req.beds, req.first_name, req.last_name, req.email)
    await ctx.send(sender, ApplyResult(request_id=req.request_id, share_url=out.get("share_url", ""), run_id=out.get("run_id", ""), error=out.get("error", "")))
