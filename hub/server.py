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
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from homie.config import ROOT, load_buildings

STATIC = Path(__file__).parent / "static"
(ROOT / "data" / "shots").mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Homie hub")
app.mount("/photos", StaticFiles(directory=STATIC / "photos"), name="photos")
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


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


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
    return {**state, "users": sorted(history, key=lambda u: -history[u][-1]["ts"]), "history": history}


@app.get("/api/history")
def get_history(user: str = ""):
    return {"user": user, "items": history.get(user, []), "users": list(history)}


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

    return {"key": phone.register(body["building_id"], body["prompt"], body["greeting"])}


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


@app.websocket("/twilio/stream/{key}")
async def twilio_stream(websocket: WebSocket, key: str):
    from homie import phone

    async def on_line(building_id: str, line: str):
        await transcript({"building_id": building_id, "line": line})

    await phone.stream(websocket, key, lambda b, l: asyncio.ensure_future(on_line(b, l)))


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
