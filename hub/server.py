"""Demo hub: live scoreboard + checklist, and the test apartment website.

    uvicorn hub.server:app --port 8080

    /        scoreboard (offers, checklist, live log, clock jump)
    /site    test leasing-office website (application status, repair requests)
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from homie.config import ROOT, load_buildings

STATIC = Path(__file__).parent / "static"
(ROOT / "data" / "shots").mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Homie hub")
app.mount("/photos", StaticFiles(directory=STATIC / "photos"), name="photos")
(STATIC / "figurines").mkdir(exist_ok=True)
app.mount("/figurines", StaticFiles(directory=STATIC / "figurines"), name="figurines")
app.mount("/avatars", StaticFiles(directory=ROOT / "relay_app" / "avatars"), name="avatars")
app.mount("/shots", StaticFiles(directory=ROOT / "data" / "shots"), name="shots")
subscribers: set[asyncio.Queue] = set()


def fresh_state() -> dict:
    return {
        "agentlog": [],
        "request": {},
        "clock_day": 1,
        "offers": {},
        "checklist": {},
        "application": None,
        "repairs": [],
        "session": {},
        "log": [],
    }


state = fresh_state()
history: dict[str, list[dict]] = {}  # per-user timeline; survives resets


def remember(user: str, kind: str, text: str, **extra) -> None:
    if not user:
        return
    items = history.setdefault(user, [])
    items.append({"t": time.strftime("%H:%M:%S"), "ts": time.time(), "kind": kind, "text": text, **extra})
    history[user] = items[-400:]


async def publish() -> None:
    snap = snapshot()
    for q in list(subscribers):
        q.put_nowait(snap)


def log(text: str) -> None:
    state["log"].append({"t": time.strftime("%H:%M:%S"), "text": text})
    state["log"] = state["log"][-60:]


@app.on_event("startup")
async def warm_google():
    """Fetch the Google token at boot so the first camera analysis on a call isn't slow."""
    import asyncio

    from homie.llm import _client

    await asyncio.to_thread(_client)


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


# ---------- vibe check (swipe cards) ----------

vibe_decks: dict[str, dict] = {}


@app.get("/vibe/{deck_id}")
def vibe_page(deck_id: str):
    return FileResponse(STATIC / "vibe.html")


@app.post("/api/vibe/decks")
async def new_deck(body: dict):
    vibe_decks[body["deck_id"]] = {"deck_id": body["deck_id"], "user": body.get("user", ""), "cards": body.get("cards", []),
                                   "swipes": {}, "created": time.time()}
    log(f"✨ Vibe check ready: {len(body.get('cards', []))} cards")
    await publish()
    return {"ok": True}


@app.get("/api/vibe/{deck_id}")
def get_deck(deck_id: str):
    deck = vibe_decks.get(deck_id)
    if not deck:
        raise HTTPException(404, "no such deck")
    return {**deck, "done": len(deck["swipes"]) >= len(deck["cards"]) or deck.get("finished", False)}


@app.post("/api/vibe/{deck_id}/swipe")
async def swipe(deck_id: str, body: dict):
    deck = vibe_decks.get(deck_id)
    if not deck:
        raise HTTPException(404, "no such deck")
    bid, direction = body.get("building_id"), body.get("dir")
    card = next((c for c in deck["cards"] if c["building_id"] == bid), None)
    if card is None or direction not in ("left", "right"):
        raise HTTPException(400, "bad swipe")
    deck["swipes"][bid] = direction
    if body.get("finish"):
        deck["finished"] = True
    log(f"{'💚 Liked' if direction == 'right' else '✖ Passed'} {card.get('name')}")
    state["agentlog"].append({"t": time.strftime("%H:%M:%S"), "user": deck.get("user", ""), "from": "you", "to": "homie-vibecheck",
                              "kind": "Swipe", "summary": f"{'right' if direction == 'right' else 'left'} on {card.get('name')}", "ts": time.time()})
    await publish()
    return {"ok": True, "done": len(deck["swipes"]) >= len(deck["cards"])}


@app.get("/flow")
def flow():
    return FileResponse(STATIC / "flow.html")


@app.get("/about")
def landing():
    return FileResponse(STATIC / "landing.html")


@app.get("/avatar")
def avatar():
    return FileResponse(STATIC / "avatar.html")


# ---------- live avatar (rendered in the renter's browser, voice by ElevenLabs Agents) ----------

live_queue: list[dict] = []


@app.get("/live")
def live_page():
    return FileResponse(STATIC / "avatar.html")


@app.get("/api/live/signed-url")
async def live_signed_url(role: str = "fix"):
    import httpx

    from homie.config import env

    agent = env("ELEVENLABS_LIVE_AGENT_ID")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url", params={"agent_id": agent},
                             headers={"xi-api-key": env("ELEVENLABS_API_KEY")})
    r.raise_for_status()
    return {"signed_url": r.json()["signed_url"]}


@app.post("/api/live/look")
async def live_look(body: dict):
    """The browser sends a camera frame; Gemini looks at it like a maintenance tech."""
    import base64

    from homie.vision import describe_photo

    jpeg = base64.b64decode(body["image"].split(",", 1)[-1])
    name = f"{uuid.uuid4().hex[:12]}.jpg"
    (ROOT / "data" / "shots" / name).write_bytes(jpeg)
    seen = await describe_photo(jpeg, body.get("what_they_said", ""))
    log(f"Live call: looked at the problem ({seen[:80]})")
    await publish()
    return {"what_i_see": seen or "The picture is unclear.", "photo": f"/shots/{name}"}


@app.post("/api/live/emotion")
async def live_emotion(body: dict):
    from typesafe_sdk import Choice

    from homie import jev
    from homie.jev import AVATAR_EMOTIONS as EMOTIONS

    a = await jev.ask({"sentence_the_avatar_is_saying": body.get("text", "")},
                      {"emotion": Choice(instructions="Which emotion should the avatar show while saying this?", criteria=EMOTIONS)},
                      label="avatar emotion")
    return {"emotion": a["emotion"].choice if a and a["emotion"].confidence >= 0.5 else "calm"}


@app.post("/api/live/request")
async def live_request(body: dict):
    """A maintenance request filed on the live call: ticket now, Repairs agent picks it up from the queue."""
    ticket_id = f"R-{uuid.uuid4().hex[:6].upper()}"
    state["repairs"].append({"ticket_id": ticket_id, "status": "filed", "slot": None, "issue": body.get("title"),
                             "details": body.get("details"), "photo_url": body.get("photo")})
    live_queue.append({**body, "ticket_id": ticket_id})
    log(f"Live call: maintenance request {ticket_id} filed: {body.get('title')}")
    await publish()
    return {"ticket_id": ticket_id, "status": "filed"}


@app.post("/api/trigger")
async def trigger(body: dict):
    """Owner-only: hand Homie a message as if texted on Relay (the team answers in the Relay team chat)."""
    from homie.config import env

    if not env("TRIGGER_TOKEN") or body.get("token") != env("TRIGGER_TOKEN"):
        raise HTTPException(403)
    live_queue.append({"type": "message", "text": body["text"]})
    return {"queued": True}


@app.post("/api/live/claim")
async def live_claim():
    items = live_queue[:]
    live_queue.clear()
    return {"items": items}


@app.get("/api/visemes")
def visemes(word: str, ms: float = 0):
    from homie.lipsync import estimate_ms, timeline

    return timeline(word, ms or estimate_ms(word))


@app.get("/site")
def site():
    return FileResponse(STATIC / "site.html")


@app.get("/site/listing/{building_id}", response_class=HTMLResponse)
def listing(building_id: str):
    b = next((b for b in load_buildings() if b["id"] == building_id), None)
    if not b:
        raise HTTPException(404)
    m = b["mock"]
    shots = "".join(f'<img src="/photos/{b["id"]}_{k}.jpg" alt="{k}">' for k in ("living", "bedroom", "kitchen"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{b["name"]} · 1 Bed</title><style>
body{{margin:0;font:16px/1.5 -apple-system,Helvetica,sans-serif;background:#fff;color:#1d1d1f}}
.hero{{position:relative;height:420px;background:url(/photos/{b["id"]}_exterior.jpg) center/cover}}
.hero div{{position:absolute;left:0;right:0;bottom:0;padding:24px 32px;background:linear-gradient(transparent,rgba(0,0,0,.75));color:#fff}}
.hero h1{{margin:0;font-size:36px}} .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:8px}}
.grid img{{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:6px}}
.facts{{display:flex;gap:28px;padding:16px 32px;font-size:18px}} .facts b{{display:block;font-size:26px}}
.note{{padding:0 32px 24px;color:#666;font-size:13px}}</style></head><body>
<div class="hero"><div><h1>{b["name"]}</h1>{b["address"]}</div></div>
<div class="facts"><div><b>${m["price"]}</b>/month</div><div><b>1 bd</b>1 bath</div><div><b>{m["discount"]}</b>current special</div></div>
<div class="grid">{shots}</div>
<div class="note">Demo listing on the Homie test site. Photos are illustrative.</div></body></html>"""


@app.get("/api/state")
def get_state():
    return state


def snapshot() -> dict:
    return {**state, "users": sorted(history, key=lambda u: -history[u][-1]["ts"]), "history": history, "tasks": tasks}


@app.get("/api/history")
def get_history(user: str = ""):
    return {"user": user, "items": history.get(user, []), "users": list(history)}


tasks: list[dict] = []


@app.post("/api/tasks")
async def set_tasks(body: dict):
    global tasks
    tasks = body.get("tasks", [])
    await publish()
    return {"ok": True}


@app.post("/api/agentlog")
async def agentlog(body: dict):
    entry = {"t": time.strftime("%H:%M:%S"), **body}
    state["agentlog"].append(entry)
    state["agentlog"] = state["agentlog"][-120:]
    remember(body.get("user", ""), "agent", f"{body['from']} → {body['to']}: {body.get('summary', '')}", frm=body["from"], to=body["to"])
    await publish()
    return {"ok": True}


@app.get("/api/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    subscribers.add(q)
    q.put_nowait(snapshot())

    async def stream():
        try:
            while not await request.is_disconnected():
                try:
                    s = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(s)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/reset")
async def reset(body: dict):
    global state
    state = fresh_state()
    state["request"] = body.get("request", {})
    state["user"] = body.get("user", "")
    remember(body.get("user", ""), "request", "New search: " + ", ".join(
        f"{k}={v}" for k, v in body.get("request", {}).items() if v not in (None, "", []) and not k.startswith("_"))[:240])
    log("New request received")
    await publish()
    return {"ok": True}


@app.post("/api/buildings")
async def buildings(body: dict):
    state["offers"] = {b["id"]: {"building_id": b["id"], "name": b["name"], "address": b.get("address", ""),
                                 "phone": b.get("phone_display") or b.get("phone"), "rating": b.get("rating"),
                                 "website": b.get("website"), "open_now": b.get("open_now"), "status": "waiting"}
                       for b in body.get("buildings", [])}
    log(f"Found {len(state['offers'])} buildings")
    await publish()
    return {"ok": True}


@app.post("/api/transcript")
async def transcript(body: dict):
    offer = state["offers"].setdefault(body["building_id"], {"building_id": body["building_id"]})
    offer.setdefault("transcript", []).append(body["line"])
    offer["transcript"] = offer["transcript"][-8:]
    await publish()
    return {"ok": True}


# ---------- real phone calls (Twilio media streams + Gemini Live) ----------

@app.post("/api/calls")
async def register_call(body: dict):
    from homie import phone

    return {"key": phone.register(body["building_id"], body["prompt"], body["greeting"], body.get("first_message", ""))}


@app.get("/api/calls/{key}/wait")
async def wait_call(key: str, timeout: float = 300):
    from homie import phone

    return await phone.wait(key, timeout)


@app.post("/twilio/status/{key}")
async def twilio_status(key: str, request: Request):
    from homie import phone

    form = await request.form()
    status = form.get("CallStatus")
    if status in ("busy", "no-answer", "failed", "canceled"):
        phone.finish(key, answered=False, status=status)
        log(f"Call {status}")
        await publish()
    return {"ok": True}


def _twilio_signed(request: Request, form: dict) -> bool:
    """Twilio signs every webhook with the auth token (HMAC-SHA1 of the URL plus sorted form params)."""
    import base64
    import hashlib
    import hmac

    from homie.config import PUBLIC_URL, env

    token = env("TWILIO_AUTH_TOKEN")
    if not token:
        return False
    url = PUBLIC_URL.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    payload = url + "".join(k + str(form[k]) for k in sorted(form))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", ""))


INBOUND_PROMPT = (
    "You are Homie Calls, an AI assistant answering the phone for an international student who is still abroad "
    "and can't take US calls. Say right away that you're an AI assistant answering for the student. {who} "
    "What the student is looking for: {request}. What the team knows so far: {offers}. "
    "Find out why they're calling, and get the details: prices, specials and the days they apply, fees, what they "
    "accept instead of an SSN, and any deadline or callback time. Repeat key numbers back to confirm them. "
    "Never agree to sign, pay or share personal details; say the student will confirm by text. This is a phone call, "
    "so speak in short natural sentences with no lists. When you're done, thank them, say goodbye, and stop talking."
)


@app.post("/twilio/voice")
async def twilio_voice(request: Request):
    """Someone called Homie's number back: Gemini Live answers with the student's context."""
    from homie import phone
    from homie.config import PUBLIC_URL

    form = dict(await request.form())
    if not _twilio_signed(request, form):
        raise HTTPException(403, "bad signature")
    caller = "".join(ch for ch in str(form.get("From", "")) if ch.isdigit())[-10:]
    match = next((o for o in state["offers"].values()
                  if caller and "".join(ch for ch in str(o.get("phone") or "") if ch.isdigit())[-10:] == caller), None)
    who = f"The caller is most likely {match['name']}, a building the team contacted." if match else "Ask which building or company is calling."
    offers = "; ".join(f"{o.get('name')}: {o.get('status', '')} {('$' + str(o['price'])) if o.get('price') else ''}".strip()
                       for o in list(state["offers"].values())[:6]) or "nothing yet"
    req = ", ".join(f"{k} {v}" for k, v in state["request"].items() if v not in (None, "", []) and not str(k).startswith("_")) or "an apartment in Atlanta"
    building_id = match["building_id"] if match else f"inbound-{caller[-4:] or 'unknown'}"
    key = phone.register(building_id, INBOUND_PROMPT.format(who=who, request=req, offers=offers),
                         "Someone just called and you picked up. Greet them.",
                         "Hi, this is Homie, an AI assistant answering for a student who's abroad right now. Who am I speaking with?")
    log(f"📞 Incoming call from {match['name'] if match else form.get('From', 'unknown')}, Homie Calls is answering")
    await publish()
    asyncio.ensure_future(_after_inbound(key, match["name"] if match else str(form.get("From", "a caller"))))
    host = PUBLIC_URL.split("://", 1)[1].rstrip("/")
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Connect><Stream url="wss://{host}/twilio/stream/{key}"/></Connect></Response>'
    return Response(content=twiml, media_type="application/xml")


async def _after_inbound(key: str, who: str) -> None:
    from homie import phone
    from homie.llm import extract_call

    result = await phone.wait(key, 600)
    if not result.get("transcript"):
        return
    fields = await extract_call(result["transcript"])
    note = f"{who} called back: " + "; ".join(f"{k} {v}" for k, v in fields.items() if v not in (None, "", [], False))[:300]
    log(f"📞 {note}")
    remember("", "event", note)
    await publish()


@app.websocket("/twilio/stream/{key}")
async def twilio_stream(websocket: WebSocket, key: str):
    from homie import phone

    async def on_line(building_id: str, line: str):
        await transcript({"building_id": building_id, "line": line})

    building_id = phone.CALLS.get(key, {}).get("building_id")
    if building_id:
        state["offers"].setdefault(building_id, {"building_id": building_id}).update({"call_key": key, "on_call": True})
        await publish()
    try:
        await phone.stream(websocket, key, lambda b, l: asyncio.ensure_future(on_line(b, l)))
    finally:
        if building_id and building_id in state["offers"]:
            state["offers"][building_id]["on_call"] = False
            await publish()


@app.post("/api/simcall")
async def sim_call(body: dict):
    """A simulated call for demos: the script is voiced with ElevenLabs and streamed to /flow exactly like a live call."""
    from homie import phone

    key = phone.register(body["building_id"], "", "")
    phone.CALLS[key]["live"] = True
    asyncio.ensure_future(_play_sim(key, body["building_id"], body.get("lines", [])))
    return {"key": key}


async def _speak(text: str, voice: str) -> bytes:
    import httpx

    from homie import eleven

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", params={"output_format": "pcm_16000"},
                                  headers={"xi-api-key": await eleven.best_key()},
                                  json={"text": text, "model_id": "eleven_flash_v2_5", "voice_settings": {"stability": 0.32, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}})
        return r.content if r.status_code == 200 else b""
    except Exception:
        return b""


async def _play_sim(key: str, building_id: str, lines: list[dict]) -> None:
    from homie import phone
    from homie.config import env

    offer = state["offers"].setdefault(building_id, {"building_id": building_id})
    offer.update({"call_key": key, "on_call": True, "simulated": True, "status": "on the phone"})
    await publish()
    import random as _r

    office_voice = _r.Random(building_id).choice(["CwhRBWXzGAHq8TQ4Fs17", "EXAVITQu4vr4xnSDxMaL", "bIHbv24MWmeRgasZH58o", "XrExE9yKIg1WjnnlVkGX", "cjVigY5qzO86Huf0OWal"])
    voices = {"homie": env("PHONE_VOICE_ID", "cgSgspJ2msm6clMCkdW9"), "office": env("SIM_OFFICE_VOICE_ID", office_voice)}
    # Voice every line up front (in parallel) so playback never stalls.
    audio = await asyncio.gather(*(_speak(l["text"], voices.get(l["who"], voices["office"])) for l in lines))
    # Two US ringback tones (440 + 480 Hz, 2 s on, 1 s off) before the manager picks up.
    import math
    import struct

    ring = b"".join(struct.pack("<h", int(2600 * (math.sin(2 * math.pi * 440 * n / 16000) + math.sin(2 * math.pi * 480 * n / 16000))))
                    if (n % 48000) < 32000 else b"\x00\x00" for n in range(16000 * 5))
    for i in range(0, len(ring), 3200):
        phone._broadcast(key, 1, 16000, ring[i:i + 3200])
        await asyncio.sleep(0.1)
    said = []
    for line, pcm in zip(lines, audio):
        text = f"{line['who']}: {line['text']}"
        said.append(text)
        await transcript({"building_id": building_id, "line": text})
        if pcm:
            chunk = 3200  # 100 ms of 16 kHz 16-bit audio
            for i in range(0, len(pcm), chunk):
                phone._broadcast(key, 1 if line["who"] == "homie" else 0, 16000, pcm[i:i + chunk])
                await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.35 * len(line["text"].split()) + 0.4)
        await asyncio.sleep(_r.uniform(0.15, 0.45))
    offer.update({"on_call": False})
    phone.CALLS[key]["live"] = False
    phone.finish(key, answered=True, transcript="\n".join(said))
    await publish()


@app.post("/api/apply")
async def apply_status(body: dict):
    state["apply"] = {**state.get("apply", {}), **body, "updated": time.strftime("%H:%M:%S")}
    log(f"🖥️ Application at {body.get('building')}: {body.get('status')}")
    await publish()
    return {"ok": True}


@app.get("/api/calls/live")
def live_calls():
    from homie import phone

    return phone.live()


@app.websocket("/api/listen/{key}")
async def listen_call(websocket: WebSocket, key: str):
    """Listen in on a live call from the workflow page."""
    from homie import phone

    await websocket.accept()
    q = phone.listen(key)
    try:
        while True:
            try:
                await websocket.send_bytes(await asyncio.wait_for(q.get(), 20))
            except asyncio.TimeoutError:
                if key not in phone.CALLS:
                    break
    except Exception:
        pass
    finally:
        phone.unlisten(key, q)
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/api/checklist")
async def checklist(body: dict):
    state["checklist"][body["step"]] = {"status": body["status"], "detail": body.get("detail", "")}
    if body["status"] in ("done", "blocked") and body.get("detail"):
        remember(body.get("user", ""), "step", f"{body['step']}: {body['detail']}")
    await publish()
    return {"ok": True}


@app.post("/api/offers")
async def offers(body: dict):
    offer = state["offers"].setdefault(body["building_id"], {"building_id": body["building_id"]})
    previous = offer.get("price")
    offer.update({k: v for k, v in body.items() if v is not None})
    if previous and offer.get("price") and offer["price"] < previous:
        offer["was"] = previous
    await publish()
    return {"ok": True}


@app.post("/api/log")
async def add_log(body: dict):
    log(body["text"])
    remember(body.get("user", ""), "event", body["text"])
    await publish()
    return {"ok": True}


@app.post("/api/application")
async def application(body: dict):
    state["application"] = {**body, "updated": time.strftime("%H:%M:%S")}
    log(f"Application {body.get('status')} at {state['offers'].get(body['building_id'], {}).get('name')}")
    await publish()
    return {"ok": True}


@app.post("/api/repairs")
async def new_repair(body: dict):
    ticket_id = f"R-{uuid.uuid4().hex[:6].upper()}"
    state["repairs"].append({"ticket_id": ticket_id, "status": "filed", "slot": None, **body})
    log(f"Repair ticket {ticket_id} filed: {body.get('issue')}")
    await publish()
    return {"ticket_id": ticket_id}


@app.post("/api/repairs/update")
async def update_repair(body: dict):
    for r in state["repairs"]:
        if r["ticket_id"] == body["ticket_id"]:
            r.update({k: v for k, v in body.items() if v is not None})
    await publish()
    return {"ok": True}


@app.post("/api/session")
async def session(body: dict):
    state["session"] = body
    await publish()
    return {"ok": True}


@app.post("/api/clock")
async def clock(body: dict):
    state["clock_day"] = int(body.get("day", state["clock_day"] + 1))
    log(f"Clock jumped to day {state['clock_day']}")
    await publish()
    return {"ok": True}
