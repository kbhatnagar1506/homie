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

from fastapi import FastAPI, HTTPException, Request
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
        "request": {},
        "clock_day": 1,
        "offers": {b["id"]: {"building_id": b["id"], "name": b["name"], "address": b["address"], "status": "waiting"} for b in load_buildings()},
        "checklist": {},
        "application": None,
        "repairs": [],
        "session": {},
        "log": [],
    }


state = fresh_state()


async def publish() -> None:
    for q in list(subscribers):
        q.put_nowait(state)


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


@app.get("/api/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    subscribers.add(q)
    q.put_nowait(state)

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
    log("New request received")
    await publish()
    return {"ok": True}


@app.post("/api/checklist")
async def checklist(body: dict):
    state["checklist"][body["step"]] = {"status": body["status"], "detail": body.get("detail", "")}
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
    await publish()
    return {"ok": True}


@app.post("/api/application")
async def application(body: dict):
    state["application"] = {**body, "unit": "4B", "updated": time.strftime("%H:%M:%S")}
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
