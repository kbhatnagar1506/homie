<p align="center">
  <img src="relay_app/avatars/homie.png" width="120" alt="Homie" />
</p>

<h1 align="center">Homie</h1>
<p align="center"><b>An AI team that's your person in America.</b><br/>
It finds, calls and negotiates your apartment, handles the no-SSN paperwork, chases repairs, remembers you, and picks up the work again on Monday.</p>

<p align="center">
  <a href="https://homie-751583582765.us-central1.run.app">Mission control</a> ·
  <a href="https://homie-751583582765.us-central1.run.app/about">Landing</a> ·
  <a href="https://homie-751583582765.us-central1.run.app/live?role=fix&live=1">Live video call</a> ·
  <a href="https://asi1.ai">Talk to @homie-usa on ASI:One</a>
</p>

<p align="center">
  <img src="relay_app/avatars/calls.png" width="52" />
  <img src="relay_app/avatars/negotiator.png" width="52" />
  <img src="relay_app/avatars/papers.png" width="52" />
  <img src="relay_app/avatars/fix.png" width="52" />
  <img src="relay_app/avatars/policy.png" width="52" />
  <img src="relay_app/avatars/pics.png" width="52" />
  <img src="relay_app/avatars/memory.png" width="52" />
  <img src="relay_app/avatars/later.png" width="52" />
  <img src="relay_app/avatars/scout.png" width="52" />
</p>

---

## The problem

If you're an international student, you rent your first US apartment from the other side of the world.

- The leasing office is open while you sleep, and it can't call an Indian number back.
- Discounts only show up on certain days, so you have to keep checking.
- You have no SSN, so they want a guarantor or "other proof" and won't say what counts.
- Rent is cashier's check only, which cost one of us a whole day.
- Once you move in, getting the ice maker fixed means explaining it to a website bot again and again.

## What Homie does

Text it once, on **Relay** or **ASI:One**:

> *"Get me a one bedroom at The Mix, jointhemix.com, Aug 20. I have no SSN."*

And the team gets to work, posting in your Relay group chat like real people with real emoji:

1. **Homie** reads the request (Jev decides the route, bedrooms, no-SSN, urgency in one call) and pulls what it already knows about you from memory.
2. **Scout** reads the building's entire website: floor plans, live prices, fees, office hours, application link, and its rules for international applicants.
3. **Pics** screenshots the floor plans into the chat.
4. **Calls** posts the live prices and specials, and phones the office if it's open. It always says it's an AI assistant calling for a student.
5. **Papers** works out exactly what to send instead of an SSN.
6. **Policy** flags fees and Georgia tenant law to watch.
7. **Later** books the office call for when they open (*"today at 1:15 PM ET"*) and runs it on its own.
8. Homie sends back one plan, ready for one yes. You pay when you get your keys.

Real output from a run against The Mix (Atlanta):

```
Here's the plan for The Mix Apartments: A1 at $1735/mo.
No SSN: Passport, Guarantor service (e.g. TheGuarantors) instead of a US cosigner
Heads-up: In Georgia, landlords must return your deposit within one month of move-out with an itemized list...
Office call: today at 1:15 PM ET (Homie Later)
Application: https://themix.prospectportal.com/atlanta/the-mix-apartments/student/
Want me to prep the application? I'll fill in everything except your personal details, and you hit submit.
```

You can also say *"find me something downtown under $2500"*. Homie searches real buildings (Google Places), calls up to 10 at once, uses the best offer as leverage to negotiate the top two, and asks for your approval before holding anything. If nobody picks up, it reads prices off their websites, locks in the best one as the price to beat, and Later calls everyone back Monday.

After you move in: *"my ice maker is broken"*. Start a **video call** with Fix. It looks through your camera ("I'm looking at it..."), files the maintenance request with what it saw, and chases the office until it's fixed.

## The team

Ten [Fetch.ai uAgents](https://fetch.ai), each with its own address, all published on Agentverse:

| | Agent | Job | Address |
|---|---|---|---|
| <img src="relay_app/avatars/homie.png" width="28"/> | **Homie** `@homie-usa` | Orchestrator. Chat Protocol + Payment Protocol. Plans, delegates, reports back. | `agent1qwcg6lkqt9pmll49h0qe3zy3ajlqy08qemrqe48k5nwknt7h6kyhyvs9rps` |
| <img src="relay_app/avatars/calls.png" width="28"/> | **Calls** | Phones leasing offices in parallel (Pipecat + Gemini Live + Twilio). | `agent1qfmq6lcrjag58fxxqq8gpmjkkl948p8998m8lr08sgmtepc6k5k4utdcztj` |
| <img src="relay_app/avatars/negotiator.png" width="28"/> | **Negotiator** | Scores offers, uses the best as leverage, calls the top two back. | `agent1qvyy8jffzqwz5mrldg5gmfk9xqr4qamny6hpmdnkqjjefzmwx3xkwhy6klg` |
| <img src="relay_app/avatars/papers.png" width="28"/> | **Papers** | No-SSN document lists, application prep, cashier's check plans. | `agent1qw3ft6ycanku4spz9ng8jj56upzmmqhzlspdrd5wnemnz35phsm7zqsl9m9` |
| <img src="relay_app/avatars/fix.png" width="28"/> | **Fix** | Repairs: triage, tickets with photos, chasing the office. | `agent1qwa6jlt60t3rqz2lr8rv2cj5e09u9lwtxp0zntc835g8hj3mfugl2zk7sz8` |
| <img src="relay_app/avatars/policy.png" width="28"/> | **Policy** | Leases, deposits, fees, Georgia tenant law. | `agent1qfr7q89z8hrkf8yh046ghxu0uk4m3pkmqaz3ngaznsqtt2qdyxr7ywygrv4` |
| <img src="relay_app/avatars/pics.png" width="28"/> | **Pics** | Screenshots listings, reads live prices off websites. | `agent1q077xn0rftvwdpx54qzqfce8v3u2e60ldl2wtfvfsckg6tyzt9hy240l06s` |
| <img src="relay_app/avatars/memory.png" width="28"/> | **Memory** | Remembers you and everything the team did, per user (Mapi). | `agent1qff0lvefgk3etwvjp7h4a08725cc3yz0ftd0lrdf7p9kxw9zpa7v5nfsq80` |
| <img src="relay_app/avatars/later.png" width="28"/> | **Later** | The waiting agent. Schedules future work and runs it on time. | `agent1qdj7s537sarkkw0lcqpat5n6st5hmwyr4zqq0wqa6ac4drwsh2wyq3wlzvs` |
| <img src="relay_app/avatars/scout.png" width="28"/> | **Scout** | Reads a building's whole website into structured facts. | `agent1q0hk8gqpa5wvd8mcafyvdhppr9r8ae9jw3dss5swmd3afpspgse9utwv3sa` |

Scout exists because of a rule we gave the team: **when it can't find something, make an agent for it.** Homie couldn't get prices for one specific building, so Scout was born.

## Architecture

```
  Relay app (group chat, DMs,          ASI:One
  video calls, payment cards)            │  Chat + Payment Protocol
            │                            ▼
            └────── Relay bridge ─────▶ Homie ◀──── Jev (System One): every decision,
                                        │  │        typed, with a confidence gate
             ┌──────────┬──────────┬────┴──┴───┬──────────┬──────────┬─────────┐
             ▼          ▼          ▼           ▼          ▼          ▼         ▼
           Calls   Negotiator   Papers        Fix      Policy      Pics     Scout
             │                                                                 │
     Twilio / Gemini Live                                         Playwright + Gemini
             │
         Later (waiting agent) ── wakes up ──▶ Homie        Memory (Mapi), per user
                                        │
                          Hub (FastAPI): mission control, SSE, live call, Twilio stream
```

- **Orchestration.** Homie is the only agent the student talks to. Specialists talk to each other through a request-id RPC (`homie/rpc.py`), so many requests can be in flight at once without the session collisions of `send_and_receive`.
- **Jev as the brain stem.** Every decision is a typed Jev question (Choice, Noul, Score), fanned out in one call: route, bedrooms, no-SSN, approvals, which teammate answers in the group, offer scoring, call outcomes, repair emergencies, avatar emotion, task kind. Homie acts only above a confidence gate, and Gemini writes every word. Jev never appears as an agent: its decisions show up as "Homie decided".
- **Gemini on Vertex** for everything else: `gemini-2.5-flash` for writing, flash-lite for calls and vision, `gemini-2.5-flash-image` for the avatars, **Veo 3** for the video-call figurine's talking and listening loops.
- **Memory.** Every agent reads from and writes to Mapi, scoped per user, before it says anything.
- **Payments.** Fetch Payment Protocol (FET, verified on-chain), plus a Relay Stripe card at key handoff. Nothing is charged until you get your keys.

## Video calls

Call any agent on Relay and a Pixar-style figurine answers. Pipecat runs ElevenLabs STT, Gemini flash-lite with tools (`look_at_problem`, `file_maintenance_request`), and ElevenLabs TTS. The figurine swaps between seamless Veo loops for talking and listening. It sees your camera, so you can point it at the broken thing. There's a browser version too: `/live`, running on ElevenLabs Agents.

## Mission control

`/` shows the whole team working live: the agent network, a talk show where agents speak to each other in turn, the buildings board, steps, the "Up next" queue from Later, and per-user history. `/about` is the three.js landing page.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env                                  # seeds, Relay, Vertex, Mapi, Jev, ElevenLabs
.venv/bin/uvicorn hub.server:app --port 8080          # mission control
.venv/bin/python -m agents.run                        # all 10 agents + the Relay team
```

Try it without Relay or ASI:One:

```bash
.venv/bin/python -m scripts.simulate "get me a one bedroom at The Mix https://www.jointhemix.com/ for Aug 20, I have no SSN"
.venv/bin/python -m scripts.e2e        # every scenario, the phone stream, and the team voice
.venv/bin/python -m scripts.selftest
```

Deploy: a single Cloud Run container (`Dockerfile`) runs the hub and every agent. Use one instance with CPU always on, since the agents hold websockets and timers.

## Honest notes

- Homie always says it's an AI assistant on calls. It never submits an application, enters your personal details, or pays without your explicit yes.
- Real phone calls need a Twilio number. Without one, Homie says no line is connected and Later books the call, and simulated runs are labeled as simulated.

Built at MHacks 2026.
