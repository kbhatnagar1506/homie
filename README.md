# Homie: your person in America

International students rent US apartments from the other side of the world. The leasing office is open while they sleep and can't call an Indian number back. Discounts only apply on certain days. There's no SSN, so the building wants a guarantor or "other proof" and won't say which. Rent is cashier's-check only. Repairs mean explaining the same problem to a website bot again and again.

**Homie does all of it for you.** Tell it what you need once in ASI:One. It calls every building at the same time, negotiates the best two, finds out what they accept instead of an SSN, holds the unit, and chases repairs after you move in. You only pay when you get your keys.

## How it works

Five Fetch.ai uAgents, each with its own address:

| Agent | Job |
|---|---|
| **Homie** | Talks to the student in ASI:One (Chat Protocol), turns the request into a plan, hands out the work, reports back. Payment Protocol for "pay on keys". |
| **Caller** | Phones leasing offices through ElevenLabs Agents + Twilio. Always says it's an AI assistant calling for a student. Many calls in parallel. |
| **Negotiator** | Uses the best competing offer as leverage and hires the Caller to call the top two back. |
| **Paperwork** | Turns "what do you accept instead of an SSN" into a document list, prepares the application, plans the cashier's check. |
| **Repairs** | Files a ticket with a photo, hires the Caller to call the office, retries, and emails if nobody answers. |

```
ASI:One ──chat──▶ Homie ──▶ Caller ──phone──▶ leasing offices
                    │  ├──▶ Negotiator ──▶ Caller
                    │  ├──▶ Paperwork
                    │  └──▶ Repairs ──▶ Caller
                    └──▶ Hub (live scoreboard + test apartment site)
```

Relay is the student's channel: approvals by text at 2am, and video calls to show documents or the broken ice maker.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in seeds (any long random phrases) and keys
.venv/bin/uvicorn hub.server:app --port 8080     # scoreboard at :8080, test site at :8080/site
.venv/bin/python -m agents.run                   # all five agents; open the Inspector link and Connect → Mailbox
```

Rehearse the whole flow locally without ASI:One:

```bash
.venv/bin/python -m scripts.simulate
.venv/bin/python -m scripts.simulate "my ice maker is broken"
```

`MOCK_CALLS=1` (the default) simulates the phone calls from `data/buildings.json`. Set it to `0` with ElevenLabs + Twilio configured to place real calls. See [docs/elevenlabs-setup.md](docs/elevenlabs-setup.md).

## Agent addresses

| Agent | Address |
|---|---|
| Homie | _fill in after first run_ |
| Caller | |
| Negotiator | |
| Paperwork | |
| Repairs | |

## Demo notes

In the demo, the leasing offices are played by our team, the apartment website is our own test site, the clock is sped up, and the payment is a test payment.

Built at MHacks 2026 with Fetch.ai (uAgents, Agentverse, ASI:One), ElevenLabs, and Relay.
