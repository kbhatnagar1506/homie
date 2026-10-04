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
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents.specialists import caller, later, memory, scout, negotiator, paperwork, pictures, policy, repairs
from homie import hub_client as hub
from homie import mapi
from homie.config import PUBLIC_URL, ROOT, env, seed
from homie.buildings import BUILDINGS, can_call_now, use
from homie.schedule import TZ, human, next_open
from homie.llm import _beds, parse_intent
from homie.places import search_apartments
from homie.rpc import ask, name_of, resolve
from homie.scope import CURRENT_USER
from homie.models import (
    CallRequest,
    CallResult,
    NegotiateRequest,
    NegotiateResult,
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
clarify: dict[str, dict] = {}
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
    CURRENT_USER.set(user_label(sender))
    pending = approvals.get(sender)
    if pending and not pending.done():
        pending.set_result(text)
        return
    if sender in active:
        await say(ctx, sender, await status_line())
        return
    saved = ctx.storage.get(f"offer:{sender}") or ctx.storage.get("offer:last")  # ASI:One may reply from a new session address
    if saved and len(text) < 200 and await means_yes(text):
        await finalize(ctx, sender, saved["best"], saved["offers"], saved["req"])
        return
    if sender in clarify:  # they're answering "how many bedrooms?"
        intent = clarify.pop(sender)
        more = await parse_intent(text)
        beds = _beds(text.lower()) if _beds(text.lower()) is not None else more.get("beds")
        if beds is None and text.strip().isdigit():
            beds = int(text.strip())
        intent.update({k: v for k, v in more.items() if v not in (None, "", []) and k not in ("intent", "_text")})
        intent["beds"] = beds if beds is not None else 1
        await start_search(ctx, sender, intent)
        return
    intent = await parse_intent(text)

    if intent["intent"] == "repair":
        await handle_repair(ctx, sender, intent)
    elif intent["intent"] == "policy":
        await handle_policy(ctx, sender, text)
    elif intent["intent"] == "status":
        await say(ctx, sender, await status_line(), end=True)
    elif intent["intent"] == "keys":
        await handle_keys(ctx, sender)
    elif intent["intent"] == "schedule":
        last = ctx.storage.get(f"lastreq:{sender}") or {}
        res = await ask(ctx, later.address, ScheduleRequest(text=text, payload={"sender": sender, "req": last}), 40)
        if isinstance(res, ScheduleResult):
            import re as _re

            what = _re.sub(r"^(please\s+)?(remind me|can you|could you)\s+", "", text, flags=_re.I)
            what = _re.sub(r"\b(tomorrow|today|tonight|on \w+day|at \d{1,2}(:\d{2})?\s*(am|pm)?|in \d+ \w+)\b", "", what, flags=_re.I)
            what = _re.sub(r"\s+", " ", _re.sub(r"^(to|that|about)\s+", "", what.strip(), flags=_re.I)).strip(" ,.")
            await say(ctx, sender, f"Done ⏰ {res.when_human}: {what or text}", end=True)
        else:
            await say(ctx, sender, "I couldn't schedule that just now. Try again in a minute?", end=True)
    elif intent["intent"] == "memory":
        mem = await ask(ctx, memory.address, MemoryRequest(question=text), 30)
        await say(ctx, sender, (mem.answer if isinstance(mem, MemoryResult) and mem.answer else "I don't know that about you yet. Tell me and I'll remember."), end=True)
    elif intent["intent"] == "pictures":
        import re as _re

        link = _re.search(r"https?://\S+", text)
        pics = await ask(ctx, pictures.address, PicturesRequest(url=link.group(0) if link else None,
                                                               building_ids=[] if link else list(BUILDINGS)[:3]), 120)
        shots = pics.images if isinstance(pics, PicturesResult) else []
        await say(ctx, sender, ("Screenshots:\n" + "\n".join(shots)) if shots else "Send me a listing link, or start a search first and I'll screenshot the buildings.", end=True)
    elif intent["intent"] == "search":
        intent["_text"] = text
        await fill_from_memory(ctx, intent)
        if intent.get("beds") is None:
            clarify[sender] = intent
            await say(ctx, sender, "Quick one before I start calling: how many bedrooms? Studio, 1, 2 or 3?")
            return
        await start_search(ctx, sender, intent)
    else:
        await say(ctx, sender,
                  "I'm Homie, your person in America. Tell me where and when you're moving, your budget, "
                  "and whether you have an SSN, and I'll call every building for you. "
                  "Already moved in? Tell me what's broken and I'll get it fixed.", end=True)


async def means_yes(text: str, offer: str = "the apartment Homie offered") -> bool:
    """Jev judges the reply ("sure, but make it the cheaper one" is still a yes); keyword check if Jev is down."""
    from homie import jev

    verdict = await jev.judge_reply(text, offer)
    if verdict:
        return verdict == "approve"
    return text.lower().strip().startswith(YES)


def user_label(sender: str) -> str:
    if name_of(sender) == "homie-relay-bridge":
        return f"@{env('RELAY_OWNER', 'owner')} · Relay"
    return f"ASI:One · {sender[:12]}…{sender[-4:]}"


async def fill_from_memory(ctx: Context, req: dict) -> None:
    """Ask Homie Memory (Mapi) what we already know about this person and fill gaps in the request."""
    mem = await ask(ctx, memory.address, MemoryRequest(
        question="Apartment preferences: city or neighborhood, budget, move-in date, bedrooms, SSN status, must-haves, deal-breakers",
        remember=req.get("_text", "")), 30)
    if isinstance(mem, MemoryResult) and mem.facts:
        known = await parse_intent(" ".join(mem.facts))
        for key in ("area", "city", "max_rent", "move_in", "beds", "no_ssn"):
            if req.get(key) in (None, "") and known.get(key) not in (None, ""):
                req[key] = known[key]
        req["_memory"] = mem.answer or "; ".join(mem.facts[:4])
        await hub.log_event("Memory: " + req["_memory"][:200])


async def start_search(ctx: Context, sender: str, req: dict) -> None:
    active.add(sender)
    try:
        await handle_search(ctx, sender, req)
    finally:
        active.discard(sender)


async def handle_search(ctx: Context, sender: str, req: dict) -> None:
    ctx.storage.set(f"lastreq:{sender}", {k: v for k, v in req.items() if not k.startswith("_")})
    import re as _re

    link = _re.search(r"https?://\S+|\b[\w-]+\.(com|net|org|apartments|life)\b", req.get("_text", ""))
    if req.get("building") or req.get("url") or link:
        await handle_building(ctx, sender, req, req.get("url") or (link.group(0) if link else ""))
        return
    area = req.get("area") or req.get("city") or env("DEFAULT_AREA", "downtown Atlanta, GA")
    budget = req.get("max_rent")
    beds = req.get("beds") if req.get("beds") is not None else 1
    label = "studio" if beds == 0 else f"{beds}-bedroom"
    await hub.post("/api/reset", {"request": {**req, "city": area}, "buildings": []})
    for s in STEPS:
        await hub.step(s, "todo")
    await hub.step("find", "active", f"Searching apartments in {area}")
    if env("DEMO_BUILDINGS", "0") != "1":
        try:
            found = [b for b in await search_apartments(area, limit=16) if b.get("phone")]
        except Exception as e:
            ctx.logger.error(f"Places search failed: {e}")
            found = []
        if not found:
            await hub.step("find", "blocked", "Search failed")
            await say(ctx, sender, f"I couldn't search {area} just now. Try again in a minute.", end=True)
            return
        use(found[: int(env("MAX_CALLS", "10"))])
    await hub.post("/api/buildings", {"buildings": list(BUILDINGS.values())})
    open_now = sum(1 for b in BUILDINGS.values() if b.get("open_now"))
    await say(ctx, sender,
              f"On it. Found {len(BUILDINGS)} buildings in {area}"
              f"{f' ({open_now} open right now)' if any(b.get('real') for b in BUILDINGS.values()) else ''}. "
              f"Calling them now about a {label}{f' under ${budget}' if budget else ''}"
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
    unanswered = [b for b, r in zip(BUILDINGS, replies) if not (isinstance(r, CallResult) and r.answered)
                  or "call back" in (r.summary or "").lower()]
    await hub.log_event(f"Calling session over: {len(offers)} offers, {len(unanswered)} offices to call back")
    if not offers:
        await recover_without_calls(ctx, sender, req, unanswered, label)
        return
    if unanswered:
        await schedule_retry(ctx, sender, req, unanswered, target=min(o["price"] for o in offers))
    await run_offers(ctx, sender, req, offers)


async def handle_building(ctx: Context, sender: str, req: dict, url: str) -> None:
    """'Get me an apartment at The Mix': the whole team goes deep on one building."""
    from homie.events import team_post

    name_hint = req.get("building") or url
    area = req.get("area") or req.get("city") or env("DEFAULT_AREA", "downtown Atlanta, GA")
    beds = req.get("beds") if req.get("beds") is not None else 1
    label = "studio" if beds == 0 else f"{beds}-bedroom"
    await hub.post("/api/reset", {"request": {**req, "city": area}, "buildings": []})
    for s_ in STEPS:
        await hub.step(s_, "todo")
    await hub.step("find", "active", f"Finding {name_hint}")
    await say(ctx, sender, f"On it 🏠 Going all in on {name_hint} for a {label}. The whole team's on this one.")

    # 1. Find the building (Google Places), matching the website if they gave one.
    found = []
    try:
        found = await search_apartments(f"{name_hint} {area}", limit=5)
    except Exception as e:
        ctx.logger.error(f"Places lookup failed: {e}")
    domain = _domain(url)
    building = next((b for b in found if domain and domain in _domain(b.get("website") or "")), None) or (found[0] if found else None)
    if building is None and url:
        building = {"id": _domain(url).split(".")[0] or "target", "name": name_hint, "website": url if url.startswith("http") else f"https://{url}",
                    "address": "", "phone": "", "real": True}
    if building is None:
        await say(ctx, sender, f"I couldn't find {name_hint}. Send me their website and I'll go from there.", end=True)
        return
    if url and not building.get("website"):
        building["website"] = url if url.startswith("http") else f"https://{url}"
    use([building])
    await hub.post("/api/buildings", {"buildings": [building]})
    await hub.step("find", "done", f"{building['name']} · {building.get('address', '')}")

    # 2. Scout reads every page of their site; Pics screenshots it meanwhile.
    await hub.step("offers", "active", "Scout is reading their whole website")
    pics_task = asyncio.ensure_future(ask(ctx, pictures.address, PicturesRequest(building_ids=[building["id"]]), 120))
    res = await ask(ctx, scout.address, ScoutRequest(building_id=building["id"], url=building.get("website") or "", beds=beds), 240)
    ctx.logger.info(f"Scout answer: {type(res).__name__} {len((getattr(res, 'facts', None) or {}).get('floor_plans') or [])} plans")
    facts = res.facts if isinstance(res, ScoutResult) else {}
    plans = [p for p in (facts.get("floor_plans") or []) if p.get("price")]
    match = [p for p in plans if p.get("beds") == beds] or plans
    budget = req.get("max_rent")
    fits = sorted([p for p in match if not budget or p["price"] <= budget] or match, key=lambda p: p["price"])
    if isinstance(res, ScoutResult) and res.shots:
        team_post("pics", f"Here's {building['name']}'s site and floor plans, straight from their website.", res.shots[:3])
    if not fits:
        await hub.step("offers", "blocked", "No prices on their site")
        await say(ctx, sender, f"{building['name']}'s site doesn't list {label} prices right now. I'll get them from the office when it opens.")
    else:
        best = fits[0]
        per = " per bed" if best.get("per_bed") else ""
        await hub.offer(building["id"], status="best unit found", price=best["price"], discount=facts.get("specials") or "")
        await hub.step("offers", "done", f"{best.get('name') or label}: ${best['price']}{per}")
        lines = "; ".join(f"{p.get('name') or str(p.get('beds')) + ' bed'} ${p['price']}{' per bed' if p.get('per_bed') else ''} ({p.get('availability') or 'availability not listed'})" for p in plans[:5])
        team_post("calls", f"{building['name']} live prices: {lines}." + (f" Special right now: {facts['specials']}." if facts.get("specials") else ""))

    # 3. Papers: what they'll need without an SSN.  Policy: fees and lease terms to watch.
    await hub.step("paperwork", "active", "Working out the no-SSN application")
    intl = facts.get("international_or_no_ssn") or ""
    fees = facts.get("fees") or {}
    pw = await ask(ctx, paperwork.address, PaperworkRequest(building_id=building["id"], ssn_alternative=intl or "Passport, I-20 and proof of funds (not stated on their site, Homie will confirm with the office)",
                                                            payment=facts.get("payment") or "", move_in=req.get("move_in")), 60)
    pol = await ask(ctx, policy.address, PolicyRequest(question=f"Before applying at {building['name']}: fees {fees}, lease terms {facts.get('lease_terms')}, "
                                                                f"utilities {facts.get('utilities')}, pet policy {facts.get('pet_policy')}. What should an international student without an SSN watch out for?",
                                                       building_id=building["id"]), 60)

    # 4. Calls: call now if the office is open and a line is connected; otherwise Homie Later books the call for opening time.
    when = next_open(building.get("hours") or [])
    allowed, _ = can_call_now(building)
    if allowed and env("MOCK_CALLS", "1") != "1" and (env("TWILIO_ACCOUNT_SID") or building.get("relay_handle")):
        await ask(ctx, caller.address, CallRequest(building_id=building["id"], purpose="quote", context={"move_in": req.get("move_in") or "August 20"}), CALL_TIMEOUT)
    else:
        await ask(ctx, later.address, ScheduleRequest(text=f"Call {building['name']} to confirm the {label}, the special, and what they accept instead of an SSN",
                                                      kind="call_building", when_iso=when.isoformat(),
                                                      payload={"sender": sender, "req": {k: v for k, v in req.items() if not k.startswith("_")}, "buildings": [building]}), 30)
        team_post("homie", f"{building['name']}'s office isn't reachable right this second, so Homie Later will call them {human(when)} to lock it in.")
    await pics_task

    # 5. The plan, ready for one yes.
    best = fits[0] if fits else None
    apply_url = facts.get("application_url") or ""
    summary = (f"Here's the plan for {building['name']}" + (f": {best.get('name') or label} at ${best['price']}{' per bed' if best.get('per_bed') else ''}/mo" if best else "") + ".\n"
               + (f"Special: {facts['specials']}\n" if facts.get("specials") else "")
               + (f"Fees: {', '.join(f'{k} {v}' for k, v in fees.items() if v)}\n" if any(fees.values()) else "")
               + (f"No SSN: {', '.join(pw.documents)}\n" if isinstance(pw, PaperworkResult) else "")
               + (f"Heads-up: {pol.answer[:300]}\n" if isinstance(pol, PolicyResult) and pol.answer else "")
               + f"Office call: {human(when)} (Homie Later)\n"
               + (f"Application: {apply_url}\n" if apply_url else "")
               + "Want me to prep the application? I'll fill in everything except your personal details, and you hit submit.")
    await hub.step("held", "active", f"Ready for your yes · {building['name']}")
    ctx.storage.set(f"home:{sender}", building["id"])
    await mapi.remember(f"Homie researched {building['name']} for them: {best.get('name') if best else label} at ${best['price'] if best else '?'}{' per bed' if best and best.get('per_bed') else ''}.",
                        tags=["activity", "homie"], source="homie")
    await say(ctx, sender, summary, end=True)


def _domain(u: str) -> str:
    import re as _re

    m = _re.search(r"(?:https?://)?(?:www\.)?([^/\s]+)", u or "")
    return m.group(1).lower() if m else ""


def deal(o: dict) -> str:
    d = (o.get("discount") or "").strip()
    return f", {d}" if d and not d.lower().startswith(("none", "no ")) else ""


def special(p: dict) -> str:
    return f" ({p['special']})" if p.get("special") else ""


def beat(job: dict) -> str:
    return f", with ${job['target']}/mo as the price to beat" if job.get("target") else ""


async def recover_without_calls(ctx: Context, sender: str, req: dict, building_ids: list[str], label: str) -> None:
    """Offices didn't pick up: read live prices off their websites, lock in the best one as our target, call back when they open."""
    await hub.step("offers", "active", "Offices closed: reading live prices from their websites")
    await say(ctx, sender, "None of the offices picked up (most are closed today). Reading their websites for live prices right now.")
    res = await ask(ctx, pictures.address, PicturesRequest(building_ids=building_ids, scan_prices=True, beds=req.get("beds")), 240)
    prices = sorted(res.prices, key=lambda p: p["price"]) if isinstance(res, PicturesResult) else []
    budget = req.get("max_rent")
    fits = [p for p in prices if not budget or p["price"] <= budget] or prices
    when = await schedule_retry(ctx, sender, req, building_ids, target=fits[0]["price"] if fits else None)
    if not fits:
        await hub.step("offers", "blocked", f"No prices online; calling {human(when)}")
        await say(ctx, sender, f"None of them post {label} prices online. I'll call every office {human(when)} when they open and get real quotes.", end=True)
        return
    best = fits[0]
    name = BUILDINGS[best["building_id"]]["name"]
    ctx.storage.set(f"target:{sender}", best)
    await mapi.remember(f"Best live price found online for their {label}: {name} at ${best['price']}/mo{special(best)}. Calling offices {human(when)} to lock it in.",
                        tags=["activity", "homie"], source="homie")
    lines = "\n".join(f"- {BUILDINGS[p['building_id']]['name']}: ${p['price']}{special(p)}" for p in fits[:4])
    await hub.step("offers", "done", f"Best online: {name} ${best['price']}")
    await hub.step("negotiate", "todo", f"Calling {human(when)} to lock it in")
    await say(ctx, sender,
              f"Live prices for a {label} right now:\n{lines}\n\n"
              f"Locking in {name} at ${best['price']}/mo as our target. I'll call every office {human(when)} when they open, "
              f"ask {name} to hold that price, and use it to push the others for a better deal. I'll message you as soon as I have offers.",
              end=True)


async def schedule_retry(ctx: Context, sender: str, req: dict, building_ids: list[str], target: int | None) -> datetime:
    buildings = [BUILDINGS[b] for b in building_ids if b in BUILDINGS]
    when = min((next_open(b.get("hours") or []) for b in buildings), default=next_open([]))
    job = {"sender": sender, "req": {k: v for k, v in req.items() if not k.startswith("_")}, "buildings": buildings,
           "when": when.isoformat(), "target": target}
    res = await ask(ctx, later.address, ScheduleRequest(text=f"Call {len(buildings)} offices to get offers when they open",
                                                        kind="get_offers", when_iso=when.isoformat(), payload=job), 30)
    from homie.events import team_post

    names = ", ".join(b["name"] for b in buildings[:4]) + (f" and {len(buildings) - 4} more" if len(buildings) > 4 else "")
    team_post("homie", f"Calling session done. Homie Later will call {names} back {human(when)}"
                       + (f", with ${target}/mo as the price to beat." if target else "."))
    if not isinstance(res, ScheduleResult):  # Later unavailable: keep the callback in-process
        asyncio.ensure_future(_run_retry(ctx, job))
    return when


async def _run_retry(ctx: Context, job: dict) -> None:
    CURRENT_USER.set(user_label(job["sender"]))
    delay = (datetime.fromisoformat(job["when"]) - datetime.now(TZ)).total_seconds()
    await asyncio.sleep(max(0, delay))
    jobs = [j for j in (ctx.storage.get("retries") or []) if j != job]
    ctx.storage.set("retries", jobs)
    sender, req = job["sender"], job["req"]
    use([{**b, "open_now": True} for b in job["buildings"]])
    await hub.post("/api/buildings", {"buildings": list(BUILDINGS.values())})
    await say(ctx, sender, f"Offices are open. Calling {len(BUILDINGS)} buildings now"
                           f"{beat(job)}.")
    replies = await asyncio.gather(*(
        ask(ctx, caller.address, CallRequest(building_id=b, purpose="quote", context={"move_in": req.get("move_in") or "August 20"}), CALL_TIMEOUT)
        for b in BUILDINGS))
    offers = [r.dict() for r in replies if isinstance(r, CallResult) and r.answered and r.price]
    if not offers:
        await say(ctx, sender, "Still no one picking up. I'll try again at the next opening.", end=True)
        await schedule_retry(ctx, sender, req, list(BUILDINGS), job.get("target"))
        return
    active.add(sender)
    try:
        await run_offers(ctx, sender, req, offers)
    finally:
        active.discard(sender)


async def run_offers(ctx: Context, sender: str, req: dict, offers: list[dict]) -> None:
    await hub.step("offers", "done", f"{len(offers)} offers")
    lines = "\n".join(f"- {BUILDINGS[o['building_id']]['name']}: ${o['price']}{deal(o)}" for o in offers)
    simulated = "\n(Rehearsal mode: these numbers are simulated, no real calls were placed.)" if env("MOCK_CALLS", "1") == "1" else ""
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
    await say(ctx, sender, f"Best deal: {name} at ${best['price']}/mo{deal(best)}. Approve?")
    approvals[sender] = asyncio.get_running_loop().create_future()
    try:
        answer = await asyncio.wait_for(approvals[sender], APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        answer = "approve"  # you asked Homie to book it if the rules match, so silence means go
    finally:
        approvals.pop(sender, None)
    if not await means_yes(answer):
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
    await mapi.remember(f"Homie held an apartment for them at {name}, ${best['price']}/mo{deal(best)}.", tags=["activity", "homie", "home"], source="homie")
    await hub.post("/api/session", {"sender": sender, "building_id": best["building_id"], "saved": saved})
    ctx.storage.set(f"home:{sender}", best["building_id"])
    await say(ctx, sender,
              f"Held at {name}, ${best['price']}/mo{deal(best)}.{f' You saved ${saved:,}.' if saved > 0 else ''}\n\n"
              f"No SSN needed. They accept: {docs}. Show me those on a video call and I'll send the application.\n\n"
              f"{plan}\n\nYou only pay Homie when you get your keys. Text me \"I got my keys\" when you do.", end=True)


async def status_line() -> str:
    state = await hub.get("/api/state")
    done = [k for k, v in (state.get("checklist") or {}).items() if v.get("status") == "done"]
    offers = [o for o in (state.get("offers") or {}).values() if o.get("price")]
    lines = ", ".join(f"{o['name']} ${o['price']} ({o.get('discount') or 'no discount'})" for o in offers)
    return f"Still working on it. Done so far: {', '.join(done) or 'starting calls'}. Offers: {lines or 'calls in progress'}. I'll message you the moment it's held."


async def handle_repair(ctx: Context, sender: str, req: dict) -> None:
    building_id = ctx.storage.get(f"home:{sender}")
    if not building_id or building_id not in BUILDINGS:
        # No search in this session (or a fresh server): repairs still work for "your building".
        building_id = building_id or "your_building"
        BUILDINGS.setdefault(building_id, {"id": building_id, "name": "your building", "address": "", "phone": env("HOME_OFFICE_PHONE", ""), "real": False})
    await say(ctx, sender, f"Got it. Filing a repair at {BUILDINGS[building_id]['name']} and calling the office now.")
    import re as _re

    issue = req.get("issue") or "Ice maker not working"
    photo = _re.search(r"photo: (https?://\S+)", issue)
    issue = _re.sub(r"\s*photo: https?://\S+", "", issue)
    result = await ask(ctx, repairs.address, RepairRequest(building_id=building_id, issue=issue, photo_url=photo.group(1) if photo else None), CALL_TIMEOUT * 2 + 30)
    if isinstance(result, RepairResult):
        await say(ctx, sender, f"Ticket {result.ticket_id}. {result.note}", end=True)
    else:
        await say(ctx, sender, "The office isn't responding yet. I'll keep chasing and tell you when it's booked.", end=True)


async def handle_keys(ctx: Context, sender: str) -> None:
    """'You only pay when you get your keys': this is that moment."""
    from homie import payments
    from homie.events import team_post

    home = ctx.storage.get(f"home:{sender}")
    place = BUILDINGS.get(home, {}).get("name") if home else None
    await hub.step("keys", "active", "Keys in hand, collecting Homie's fee")
    team_post("homie", f"🔑 KEYS! {('Welcome home at ' + place) if place else 'Welcome home'}! Team, we did it.")
    await mapi.remember(f"They got their keys{(' at ' + place) if place else ''}.", tags=["activity", "homie", "home"], source="homie")
    if name_of(sender) == "homie-relay-bridge":
        await say(ctx, sender, f"[[PAY]] Congrats on the keys{(' at ' + place) if place else ''}! 🎉 As promised, you only pay now: "
                               f"Homie's fee is ${payments.fee_usd_cents() / 100:.2f}.")
    elif _payments_on(sender):
        await say(ctx, sender, f"Congrats on the keys! 🎉 As promised, you only pay now: {payments.fee_fet()} FET. Sending the request.")
        await request_fee(ctx, sender, home or "keys")
    else:
        await say(ctx, sender, "Congrats on the keys! 🎉 Welcome home.", end=True)


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

def _payments_on(sender: str = "") -> bool:
    """The Payment Protocol request goes to ASI:One users; Relay users hear 'you pay when you get your keys'."""
    return env("ENABLE_PAYMENT_REQUEST", "0") == "1" and name_of(sender) != "homie-relay-bridge"


async def request_fee(ctx: Context, sender: str, building_id: str) -> None:
    await ctx.send(sender, RequestPayment(
        accepted_funds=[Funds(amount=env("HOMIE_FEE_AMOUNT", "1"), currency=env("HOMIE_FEE_CURRENCY", "FET"), payment_method="fet_direct")],
        recipient=str(homie.wallet.address()),
        deadline_seconds=3600,
        reference=f"keys-{building_id}",
        description="Homie fee: you got your keys",
    ))


@payments.on_message(CommitPayment)
async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
    """Verify the FET transfer on Fetch mainnet before confirming."""
    from homie import payments as pay

    ok, detail = await pay.verify_fet_transfer(msg.transaction_id, str(homie.wallet.address()), float(pay.fee_fet()))
    await hub.log_event(f"Payment {msg.transaction_id[:12]}…: {detail}")
    if not ok:
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason=detail))
        await say(ctx, sender, f"I couldn't verify that payment: {detail}. No worries, try again when you're ready.", end=True)
        return
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    await hub.step("keys", "done", f"Paid on keys · {detail}")
    await mapi.remember(f"They paid Homie's fee ({detail}).", tags=["activity", "payment"], source="homie")
    await say(ctx, sender, f"Payment verified on the Fetch network ({detail}). Thank you, and welcome home! 🏡", end=True)


@payments.on_message(RejectPayment)
async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
    await say(ctx, sender, "No problem. You'll only pay when you have your keys.", end=True)


@homie.on_message(TaskDue)
async def on_task_due(ctx: Context, sender: str, task: TaskDue):
    """Homie Later woke up: do the task."""
    CURRENT_USER.set(task.user)
    p = task.payload or {}
    who = p.get("sender")
    if task.kind == "get_offers" and p.get("buildings"):
        await _run_retry(ctx, p)
    elif task.kind in ("get_offers", "check_prices") and who and (p.get("req") or {}):
        await say(ctx, who, f"⏰ It's time: {task.text}. Starting now.")
        await start_search(ctx, who, dict(p["req"]))
    elif task.kind == "follow_up_repair" and who:
        await handle_repair(ctx, who, {"issue": f"Follow-up: {task.text}"})
    else:
        from homie.events import team_post

        import re as _re

        what = _re.sub(r"^(please\s+)?remind me\s+(in\s+[\w\s]+?\s+(to|that|about)\s+|(to|that|about)\s+)?", "", task.text, flags=_re.I).strip() or task.text
        what = _re.sub(r"\bmy\b", "your", what)
        team_post("homie", f"⏰ Reminder: {what}")
        if who:
            await say(ctx, who, f"⏰ Reminder: {what}", end=True)


@homie.on_event("startup")
async def resume_retries(ctx: Context):
    for job in ctx.storage.get("retries") or []:  # older callbacks from before Homie Later existed
        asyncio.ensure_future(_run_retry(ctx, job))


@homie.on_message(CallResult)
@homie.on_message(NegotiateResult)
@homie.on_message(PaperworkResult)
@homie.on_message(RepairResult)
@homie.on_message(PolicyResult)
@homie.on_message(PicturesResult)
@homie.on_message(MemoryResult)
@homie.on_message(ScheduleResult)
@homie.on_message(ScoutResult)
async def on_specialist_reply(ctx: Context, sender: str, msg):
    resolve(msg)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


homie.include(chat, publish_manifest=True)
homie.include(payments, publish_manifest=True)
