"""Papers fills out the real application on the building's own site, live, with Browser Use Cloud.

It fills what the renter told us and stops at the password / create-account / submit step: the renter
sets their own password and presses the final button themselves.
"""

import asyncio
import logging

import httpx

from homie import hub_client as hub
from homie.config import env

log = logging.getLogger("homie.apply")
API = "https://api.browser-use.com/api/v4"

TASK = """Work fast: no exploring, no screenshots of other pages. Start a rental application at {name}.
Go to {url}. If that page isn't the application / account sign-up form, click the property's "Apply" or "Apply Now" entry once.

Fill in only these details, where the form asks for them:
- First name: {first}
- Last name: {last}
- Email: {email}
- Desired move-in date: {move_in}
- Bedrooms / floor plan: {beds} bedroom (pick the closest available option if a choice is required)

Hard rules:
- Never type any password, and never choose one. Leave password fields empty.
- Never click a final "Create account", "Sign up", "Register", "Submit", "Apply" (on the final step), "Pay" or "Continue to payment" button.
- Never accept terms or consent checkboxes, never enter phone numbers, SSN, birth date, payment or ID details.
- Stop when the form is filled up to the password / create-account / submit step.
Finally report: the page you stopped on, which fields you filled, and what the renter still has to do themselves."""


def available() -> bool:
    return bool(env("BROWSER_USE_API_KEY"))


async def start(building: dict, url: str, move_in: str, beds: int | None, first: str, last: str, email: str, task: str = "") -> dict:
    """Start the run and a public live view, with the renter's own details. Returns {"share_url", "run_id"} or {"error"}."""
    task = task or TASK.format(name=building["name"], url=url, first=first, last=last or "-", email=email,
                       move_in=move_in or "August 20", beds=1 if beds is None else beds)
    headers = {"X-Browser-Use-API-Key": env("BROWSER_USE_API_KEY")}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API}/runs", headers=headers, json={"task": task, "model": env("APPLY_MODEL", "gemini-3.6-flash"),
                                                                   "maxCostUsd": float(env("APPLY_MAX_USD", "2"))})
        if r.status_code >= 300:
            return {"error": r.text[:200]}
        run = r.json()
        share = (await client.post(f"{API}/sessions/{run['sessionId']}/share", headers=headers)).json()
        live = ""
        for _ in range(12):  # the agent's browser comes up a few seconds after the run starts
            listing = (await client.get(f"{API}/browsers", headers=headers, params={"pageSize": 10})).json()
            items = listing.get("items") or listing.get("sessions") or listing.get("data") or []
            live = next((b.get("liveUrl") for b in items if b.get("agentSessionId") == run["sessionId"] and b.get("liveUrl")), "")
            if live:
                break
            await asyncio.sleep(2)
    out = {"run_id": run["id"], "session_id": run["sessionId"], "share_url": share.get("shareUrl", ""), "live_url": live,
           "building": building["name"], "url": url}
    await hub.post("/api/apply", {**out, "status": "filling the application live"})
    asyncio.ensure_future(_watch(out))
    return out


async def _watch(job: dict) -> None:
    headers = {"X-Browser-Use-API-Key": env("BROWSER_USE_API_KEY")}
    for _ in range(240):  # up to 20 minutes
        await asyncio.sleep(5)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                run = (await client.get(f"{API}/runs/{job['run_id']}", headers=headers)).json()
        except Exception as e:
            log.warning("apply watch: %s", e)
            continue
        status = run.get("status")
        if status in ("completed", "failed", "stopped", "cancelled", "error"):
            result = str(run.get("output") or run.get("result") or run.get("error") or "")[:900]
            if job.get("sandbox") and status == "completed":
                await hub.post("/api/apply", {"result": result, "cost": run.get("totalCostUsd")})  # the portal already reported each step
            else:
                await hub.post("/api/apply", {**job, "status": "ready for your password and final click" if status == "completed" else status,
                                              "result": result, "cost": run.get("totalCostUsd")})
            await hub.log_event(f"🖥️ Papers finished the {job['building']} application form: {result[:160]}")
            return


LOGIN_TASK = """The renter already has an account at {name}. Go to {url} and open the "Log in" / "Sign in" / "Already have an account?" form.
Do NOT type any email or password and do NOT click Sign in yourself: the renter signs in themselves, in this same browser.
Wait (use the wait action about 10 seconds at a time, for up to 8 minutes) until the page shows they are signed in
(an application, dashboard or welcome page). Then continue their application, working fast:
- choose a {beds}-bedroom floor plan (the closest available one)
- move-in date: {move_in}
- name {first} {last} and email {email}, only where the form asks and they aren't already filled
Hard rules: never enter SSN, birth date, ID numbers, income, employer, bank or payment details; never upload files;
never accept terms or consent checkboxes; never click a final Submit / Pay / Sign / e-sign button.
Stop at the first step that needs any of those, and report the page you stopped on, what you filled, and what's left for the renter."""


async def continue_after_login(building: dict, url: str, move_in: str, beds: int | None, first: str, last: str, email: str) -> dict:
    """Papers waits for the renter to sign in themselves, then carries on with the application."""
    task = LOGIN_TASK.format(name=building["name"], url=url, first=first, last=last or "-", email=email,
                             move_in=move_in or "August 20", beds=1 if beds is None else beds)
    out = await start(building, url, move_in, beds, first, last, email, task=task)
    if out.get("run_id"):
        await hub.post("/api/apply", {**out, "status": "waiting for you to sign in"})
    return out


SANDBOX_TASK = """Work fast. Go to {url}. This is a sandbox leasing portal for a demo: nothing is real, nothing is charged.
Complete all three steps in order:
1. Create account: first name {first}, last name {last}, email {email}, phone {phone},
   tick the terms checkbox, click "Create Account" (no password: the portal emails a sign-in link).
2. Application: floor plan "1 Bedroom / 1 Bath", move-in date {move_in_iso}, lease term "12 months",
   applicant type "International student (no SSN)", click "Continue to application fee".
3. Fee: pay with "Homie sandbox wallet", click "Pay $50.00 and submit application".
Confirm the "Payment received" screen and report its reference number."""


async def sandbox_run(building: dict, move_in_iso: str, first: str, last: str, email: str) -> dict:
    """Demo mode: Papers creates the account, fills the application and pays the fee on Homie's sandbox portal, live."""
    from urllib.parse import quote

    url = f"{env('PUBLIC_URL')}/portal?b={quote(building['name'])}"
    task = SANDBOX_TASK.format(url=url, first=first, last=last or "-", email=email, phone=env("SANDBOX_PHONE", "4045550123"),
                               move_in_iso=move_in_iso or "2027-08-20")
    out = await start(building, url, "", None, first, last, email, task=task)
    out["sandbox"] = True  # same dict the watcher holds
    return out
