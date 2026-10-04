# Homie: the AI team that finds, calls and lands your first US apartment

> **This isn't a chatbot. It's a team.** One text from the other side of the world, and eleven AI agents read every listing, phone every leasing office, negotiate the best deal and start your application, while you sleep.

![Homie architecture](https://raw.githubusercontent.com/kbhatnagar1506/homie/main/docs/homie_architecture.png)

---

## 🔗 Try it live

| | |
|---|---|
| **ASI:One** | `@homie-usa` |
| **Relay** | Text `@homie` (or message the Homie Team group) |
| **Mission control** | https://homie-751583582765.us-central1.run.app |
| **Hear every call live** | https://homie-751583582765.us-central1.run.app/flow |
| **Demo video** | https://youtu.be/zl_fpA4hX9Q |
| **Code** | https://github.com/kbhatnagar1506/homie |

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3) ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

---

## 🤖 11 agents on Agentverse

| Agent | Job | Address |
|---|---|---|
| **Homie** `@homie-usa` | Orchestrator. Chat Protocol + Payment Protocol | `agent1qwcg6lkqt9pmll49h0qe3zy3ajlqy08qemrqe48k5nwknt7h6kyhyvs9rps` |
| **Scout** | Reads every building's website into prices, specials, hours, no-SSN rules | `agent1q0hk8gqpa5wvd8mcafyvdhppr9r8ae9jw3dss5swmd3afpspgse9utwv3sa` |
| **Vibecheck** | Swipe deck from Scout's facts; learns your taste and shortlists | `agent1qw997gd5awven0egprca6r62g04shle0vugpd467358jstvan6mfze37sha` |
| **Calls** | Calls every office you liked, in parallel, with an ElevenLabs voice | `agent1qfmq6lcrjag58fxxqq8gpmjkkl948p8998m8lr08sgmtepc6k5k4utdcztj` |
| **Later** | The waiting agent: books callbacks for the exact day a special kicks in | `agent1qdj7s537sarkkw0lcqpat5n6st5hmwyr4zqq0wqa6ac4drwsh2wyq3wlzvs` |
| **Negotiator** | Scores offers and plays the best one against the rest | `agent1qvyy8jffzqwz5mrldg5gmfk9xqr4qamny6hpmdnkqjjefzmwx3xkwhy6klg` |
| **Papers** | No-SSN document checklist, then your application in a live browser | `agent1qw3ft6ycanku4spz9ng8jj56upzmmqhzlspdrd5wnemnz35phsm7zqsl9m9` |
| **Policy** | Leases, deposits, fees and tenant law in plain English | `agent1qfr7q89z8hrkf8yh046ghxu0uk4m3pkmqaz3ngaznsqtt2qdyxr7ywygrv4` |
| **Pics** | Screenshots listings and reads live prices off websites | `agent1q077xn0rftvwdpx54qzqfce8v3u2e60ldl2wtfvfsckg6tyzt9hy240l06s` |
| **Memory** | Remembers you and everything the team did, per user (Mapi) | `agent1qff0lvefgk3etwvjp7h4a08725cc3yz0ftd0lrdf7p9kxw9zpa7v5nfsq80` |
| **Fix** | Repairs: sees the problem on a video call and chases the office | `agent1qwa6jlt60t3rqz2lr8rv2cj5e09u9lwtxp0zntc835g8hj3mfugl2zk7sz8` |

---

## 💡 Inspiration

International students rent their first American apartment from the other side of the world, and the system is built for someone already standing in the leasing office.

One of us rented from India. The office was open while he slept and couldn't call an Indian number back. The best prices only showed up on certain days. With no SSN, every form stopped at the same box. The deposit had to be a cashier's check, which cost a whole day in a bank line. And after moving in, getting the ice maker fixed meant explaining it to a website bot, again and again.

Hundreds of thousands of students go through some version of this every year *(swap in a cited number here, e.g. from the IIE Open Doors report)*. The pain is concentrated in five fixable places:

1. **Time zones and phones.** Offices don't call international numbers back.
2. **No SSN.** No background check, no standard application.
3. **No US guarantor.** Months of rent upfront.
4. **Payments.** Cashier's checks and US-bank-only deposits.
5. **After move-in.** Repairs go into a void.

Homie is built to take all five off your plate.

---

## ⚡ What it does

You text it once, on **Relay** or **ASI:One**:

> *"Find me a 1 bedroom off campus near UC Berkeley under $3,500. I have no SSN."*

Here's what happened in our live end-to-end test on the deployed system, triggered from a Relay group chat:

| Time | What the team did |
|---|---|
| **0s** | **Homie** reads the request (Jev decides route, bedrooms, no-SSN and urgency in one typed call) and recalls what **Memory** knows about you. |
| **33s** | **Scout** has found 6 real buildings (Google Places) and read every one of their websites. **Vibecheck** sends a numbered shortlist right in the chat (reply *"1 3"* in ASI:One), plus an optional swipe deck with photos, live price and the current special. |
| **35s** | You pick 3, by replying in ASI:One or by swiping. Vibecheck sums up your taste and saves it to memory. |
| **49s** | **Calls** dials every office you liked at once. You can listen to all of them live on `/flow`. Each call asks the rent, **when the special applies**, and what they accept instead of an SSN. |
| **121s** | **Later** books a callback for the exact day each special kicks in ("Wednesday at 10:15 AM ET"). |
| **153s** | **Negotiator** plays the offers against each other. |
| **156s** | **Homie** holds the best deal. |
| **160–185s** | **Papers** opens the building's portal in a live browser embedded in mission control: ✅ account created → ✅ application filled → ✅ $50 application fee paid *(sandbox portal in demo mode)*. |

**About 3 minutes from one text to a held apartment and a started application. Zero phone calls made by the student.**

Even on a Sunday, when every office is closed, Homie still decides: it ranks the buildings you liked on Scout's live website prices, picks a top pick, and Later calls them all the moment they open.

**After you move in:** say *"my ice maker is broken"* and video-call **Fix**. A Pixar-style figurine looks through your camera ("I'm looking at it…"), describes what it sees with Gemini vision, files the maintenance request, and chases the office until it's booked.

---

## 🏗 Architecture

![Homie architecture](https://raw.githubusercontent.com/kbhatnagar1506/homie/main/docs/homie_architecture.png)

```
  YOU                         ORCHESTRATOR                       SPECIALISTS (in order)              UNDER THE HOOD
  ───                         ────────────                       ─────────────────────               ──────────────
  Relay app ──────┐                                         ┌──▶ ① Scout ───────────────────▶ Google Places + Playwright
  (group chat,    │                                         ├──▶ ② Vibecheck (shortlist + swipe deck)
   7 contacts,    ├──▶  Homie (uAgent, @homie-usa) ─────────┼──▶ ③ Calls ────────────────────▶ ElevenLabs Agent ⇄ Twilio
   video calls,   │     · Chat Protocol + Payment Protocol  │                                 (μ-law 8 kHz passthrough,
   rich cards)    │     · Jev: typed decisions + gates      ├──▶ ④ Later (callbacks on special days)   tapped → /flow live audio)
  ASI:One ────────┘     · Gemini writes every word          ├──▶ ⑤ Negotiator
  (Chat Protocol)       · request-id RPC to 10 specialists  ├──▶ ⑥ Papers ───────────────────▶ Browser Use Cloud (live view)
                               │                            └──▶ Fix · Policy · Pics ────────▶ Pipecat + Gemini vision + Veo
                               ▼
                        Memory (Mapi, per user)       Hub (FastAPI): mission control · /flow · /vibe · /portal · /live · SSE
                        All 11 agents + hub in one Cloud Run container · every agent registered on Agentverse
```

**How a message moves:**
1. Your text arrives from ASI:One (Chat Protocol) or Relay (through our Relay bridge agent) at **Homie**.
2. **Jev** answers typed questions about it in one call (route, bedrooms, no-SSN, urgency). Homie acts only above a confidence gate.
3. Homie fans out over a **request-id RPC**: Scout reads 6 sites in parallel, Calls opens up to 10 calls at once, and every reply comes back matched by ID.
4. Every hop is posted to the hub, so **mission control** and `/flow` show agents working and calls happening in real time over SSE and WebSockets.
5. **Later** persists future tasks in agent storage and wakes Homie up when they're due, even after a restart.

---

## 🏁 How Homie meets the Fetch.ai judging criteria

| Criterion | What Homie does |
|---|---|
| **Functionality & technical implementation (25%)** | 11 uAgents running live on Cloud Run. Request-id RPC keeps 10 calls and 6 website reads in flight at once. A real end-to-end run takes 185 seconds from one message to a held apartment. |
| **Use of Fetch.ai technology (20%)** | Every agent registered on **Agentverse** with the Innovation Lab badge. Homie speaks the **Agent Chat Protocol** and is usable from **ASI:One** as `@homie-usa`. It also implements the **Payment Protocol**: a `RequestPayment` for the fee in FET, verified on-chain before `CompletePayment`. |
| **Innovation & creativity (20%)** | An agent that *waits* for you (Later), a swipe deck built from scraped facts (Vibecheck), calls you can listen to live in stereo, and a figurine you can video-call that sees through your camera. |
| **Real-world impact & usefulness (20%)** | It takes the exact five barriers international students hit (time zones, phone callbacks, no SSN, moving prices, US-only payments) and turns them into one message. |
| **User experience & presentation (15%)** | The whole flow runs inside one ASI:One conversation: pick buildings by replying "1 3". Plus mission control, `/flow` with "Hear all calls", and Relay cards. |

**Bonus points Homie covers:**
- **Multi-agent collaboration:** 11 agents working together.
- **Payment Protocol:** implemented for the Homie fee.
- **Real-time data:** Google Places, live websites and live calls.
- **Error handling:** retries, a cache fallback, a demo-call fallback, and confidence gates on every decision.
- **Long-term viability:** Later's tasks survive restarts.

## 💬 Built for Relay

Relay is where Homie stops being a bot and becomes **a team you can text, watch and video-call.**

```
                        ┌──────────── Relay ─────────────┐
   you ──text/call──▶   │  Homie Team group chat         │   ◀── agents post in their own voice + emoji
                        │  @homie @homiecalls @homiepapers│
                        │  @homiefix @homiepolicy         │   ◀── rich cards: swipe deck · Approve ·
                        │  @homiepics @homiememory        │       "watch your application live" · live call
                        └──────────────┬─────────────────┘
                                       │ relaymessenger SDK (websocket per agent)
                                       ▼
                         Relay bridge (uAgent) ⇄ Homie + 10 specialists
                                       │
              video call ─────────────▶ Pipecat RelayTransport ─▶ ElevenLabs STT → Gemini (tools + vision) → ElevenLabs TTS
                                                                   └▶ Veo talking/listening figurine as the video track
```

**What we built on Relay:**
- **Seven agent contacts, one team chat.** Every Homie agent is a real Relay account with its own Gemini-made avatar, persona and bio. They share a **Homie Team** group chat, and in it Jev decides which teammate should answer each message.
- **Agents that talk like people.** Every update goes through a persona pass: Calls is the upbeat negotiator, Papers the calm precise one, Memory answers only from what it remembers. All in short texts with emoji, and every number kept exactly.
- **Every chat is a front door.** Text Homie, the group, or even **Memory** "get me an apartment" and the full 11-agent pipeline runs, reporting back in that same thread. Fix starts repairs, and Policy answers lease questions.
- **Rich cards for every big moment:**
  - the **vibe-check swipe deck**
  - **Approve / Keep looking** buttons
  - a **"watch your application live"** card that opens the Browser Use session
  - a **video-call card**
- **📹 Video calls with a figurine that can see.**
  - Call any agent and a Pixar-style 3D figurine answers. Its talking and listening loops are generated with **Veo 3** and switched in real time, and the agent always greets you first.
  - **Pipecat runs the call over Relay's call transport:** ElevenLabs speech-to-text, then Gemini Flash-Lite with tools, then ElevenLabs voice.
  - **It sees your camera.** `look_at_problem` grabs a frame from your video and Gemini vision describes the issue; `file_maintenance_request` files the ticket with the photo and hands it to Fix, which chases the office.
- **Proactive, not reactive.** Homie messages *you*: when the deck is ready, when a call ends, when Later wakes up on Monday, when your application is filling.
- **Payments in the chat.** Homie's fee request is built as a Relay payment card at key handoff. *(It needs Stripe connected in the Relay console.)*

---

## 🛠 How we built it

### Fetch.ai: 11 uAgents, one team
- **The whole workflow runs inside an ASI:One conversation:** request, pick buildings by replying with numbers, live progress, the held deal, the application, and the Payment Protocol fee.
- **Every agent is a real uAgent with its own address**, registered as a mailbox agent on **Agentverse** with a readme, avatar and Innovation Lab badge.
- **Homie speaks the Agent Chat Protocol**, so it's discoverable and usable from **ASI:One** as `@homie-usa`.
- **Payment Protocol:** Homie is a seller. When you get your keys, it sends a `RequestPayment` for its fee in FET and verifies the transaction on-chain before sending `CompletePayment`.
- **Agent-to-agent messaging:** specialists talk through our own **request-id RPC** (`homie/rpc.py`) instead of `send_and_receive`. That lets dozens of requests be in flight at once (10 parallel calls, 6 parallel website reads) with no session collisions.
- **Later is a true long-running agent.** It holds future tasks in agent storage, survives restarts, wakes up on time, and hands work back to Homie.

### Relay
See **💬 Built for Relay** above: seven agent contacts, a team group chat, rich cards, and video calls with a Veo figurine that sees through your camera.

### Calls you can hear: ElevenLabs + Twilio
- **Each call runs on an ElevenLabs Agent**, bridged from Twilio media streams in **μ-law 8 kHz with zero transcoding**. Audio passes straight through.
- **Result: about 1 second from the office going quiet to Homie answering** (measured: LLM 0.47s, TTS 0.12s, ASR 0.05s), with natural turn-taking and barge-in.
- **The bridge taps both sides of every call.** `/flow` can play any call live, or **all of them at once, panned in stereo**.
- **Honest by design:** the agent always opens by saying it's an AI assistant calling for a student.

### Decisions: Jev as the brain stem
- **Every decision is a typed Jev question:** `Choice` for routing, `Noul` for yes/no facts like *"did a person answer?"* or *"is no-SSN OK?"*, and `Score` for offer quality.
- **They're fanned out in one call,** and Homie acts only above a confidence gate.
- **Gemini on Vertex writes every word;** Jev never writes text. In the UI, Jev's decisions show up as *"Homie decided"*.

### Browser Use: the application, live
- **Papers drives a Browser Use Cloud browser,** and the live view is embedded right in mission control.
- **On real leasing portals,** it fills your name, email and move-in, then **stops before the password and submit**. That click is yours.
- **In demo mode,** it runs the full account → application → fee flow on our sandbox portal.

### The rest of the stack
- **Gemini 2.5 on Vertex AI:** Flash for writing, Flash-Lite for vision and calls, Flash Image for avatars.
- **Veo 3:** the figurine loops.
- **Playwright:** Scout's crawls and screenshots.
- **Google Places:** real buildings.
- **Mapi:** per-user memory.
- **Cloud Run:** a single container running the hub and all 11 agents.
- **Mission control and `/flow`:** server-sent events and WebSockets.

---

## ✏️ Best Use of Notability

**Notability was our product-design workspace, not something we added after the hack.**

Homie started as a handwritten page about one of us finding housing in the US as an international student: no SSN, unfamiliar rental systems, guarantor requirements, unanswered calls, and uncertainty even after getting "confirmation." We then imported and annotated housing research directly in Notability, to test whether that personal experience reflected a larger problem. From there, the pages evolved into product sketches, agent-architecture diagrams, feature decisions, Homie's branding, and finally our pitch and demo storyboard.

We used **handwriting, diagrams, PDF annotation, images and mind maps across 9 pages** to keep the entire chain of reasoning in one place. You can follow the product evolving page by page:

**personal pain → research → idea selection → agent architecture → product design → demo → pitch.**

That's what made Notability valuable: it captured the messy thinking between "we have a problem" and "we built Homie."

---

## 🧗 Challenges we ran into

- **Voice latency.** Our first pipeline chained speech-to-text, an LLM and text-to-speech, and it felt robotic. We moved to an ElevenLabs Agent with a pass-through μ-law bridge, so the bridge only relays audio and taps it for listeners. That got replies to about 1 second.
- **Parallel agents colliding.** `send_and_receive` matches replies by session, so 10 simultaneous calls stepped on each other. We built a request-id RPC layer that every agent shares.
- **Reading real leasing sites.** Every building's site is different, and many are JS-heavy. Scout follows floor-plan, pricing and FAQ pages, has Gemini extract structured facts, and backs that up with a deterministic price parser.
- **Production surprises during live testing:**
  - Google Places returned 500s, so we added retry plus a cache.
  - Cloud Run's default 80-request concurrency choked on live audio streams, so we raised it to 1,000.
  - A memory bug swapped a city the user named for an old one, so memory now never overrides a stated place.
- **Doing the right thing on real calls and real sites.** Homie always discloses that it's an AI. It never types a password or submits an application on a real portal for you. Building that line in mattered more to us than a flashier demo.

---

## 🏆 Accomplishments we're proud of

- **11 Fetch.ai agents** live on Agentverse, discoverable on ASI:One, with the Chat Protocol and Payment Protocol.
- **A full live run in 185 seconds**, from one Relay text to a held apartment and a started application.
- **Calls you can actually hear:** live listening and "hear all calls" in stereo.
- **Real data everywhere:** real buildings, real website prices and screenshots, and real leasing portals filled live.
- **A team that feels like people:** agents post in Relay in their own voices, and you can video-call them.

---

## 📚 What we learned

- Voice quality is mostly a latency problem, and latency is mostly an architecture problem.
- Multi-agent systems need boring infrastructure (request IDs, caching, retries) far more than clever prompts.
- The most valuable thing an agent can do for someone abroad is *wait for them*. That's why Later became the agent we didn't know we needed.

---

## 🚀 What's next

- Launch for incoming students in the cities they actually move to: Ann Arbor, Berkeley, Atlanta, Boston, New York, Champaign.
- Partner with leasing offices directly. If an office runs its own Agentverse agent, Homie can negotiate agent-to-agent, with no phone call at all.
- Guarantor and deposit partners integrated into Papers.
- A free tier for international student offices at universities.

---

**Built with:** Fetch.ai uAgents · Agentverse · ASI:One · Relay · ElevenLabs · Twilio · Gemini · Veo · Vertex AI · Jev (TypeSafe) · Browser Use · Playwright · Google Places · Mapi · Pipecat · FastAPI · Cloud Run · Notability
