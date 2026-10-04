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

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse

from homie.config import load_buildings

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Homie hub")
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
