"""Runs the five Homie contacts on Relay.

Relay is the face, the Fetch agents are the brain: Homie forwards real work
(search, repairs, policy questions) to the Homie uAgent, and every specialist
agent's progress is posted by its own Relay contact into the "Homie Team"
group chat (or that contact's direct chat until the group exists).
"""

import asyncio
import json
import os
import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from relaymessenger import Relay
from relaymessenger.websocket import run_websocket

from homie import events, memory
from homie import hub_client as hub
from homie.buildings import BUILDINGS, listing_url
from homie.config import ROOT, env
from homie.screenshots import screenshot
from homie.llm import complete_text, parse_intent
from relay_app.team import AGENT_ROLE, DISPLAY, GROUP_NAME, STYLE, TEAM

log = logging.getLogger("homie.relay")
STATE = ROOT / "data" / "relay_state.json"
BASE_URL = env("RELAY_BASE_URL", "https://api.relayapp.im")
MENTIONS = {"calls": ("calls", "call"), "papers": ("papers", "paperwork", "documents"), "fix": ("fix", "repair"), "policy": ("policy", "rights", "lawyer"), "pics": ("pics", "pictures", "photos", "screenshot")}


ACTIONS = {"search", "repair", "policy", "schedule", "keys", "pictures", "status"}


class RelayTeam:
    def __init__(self, send_to_homie: Callable[[str], Awaitable[None]]):
        self.send_to_homie = send_to_homie
        self.relays: dict[str, Relay] = {r: Relay(env(p.token_env), base_url=BASE_URL) for r, p in TEAM.items() if env(p.token_env)}
        self.handles: dict[str, str] = {}
        self.owner: str | None = None
        self.state = self._load()
        self.route: tuple[str, str] | None = None  # where the Homie uAgent's next reply goes
        self.awaiting_approval = False
        self.seen: set[str] = set()
        self.history: dict[str, list[dict]] = {}
        self.recent: list[str] = []  # last few team-chat lines, so teammates react to each other
        self._handoffs: dict[tuple[str, str], list[str]] = {}
        self.calls: set[asyncio.Task] = set()
        self.active_calls: set[str] = set()

    # ---------- setup ----------

    def _load(self) -> dict:
        try:
            return json.loads(STATE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            # On a fresh server, reuse the chats created on the first run instead of texting hello again.
            return json.loads(env("RELAY_STATE_JSON") or '{"direct": {}, "team_chat": null, "hello": []}')

    def _save(self) -> None:
        STATE.write_text(json.dumps(self.state, indent=2))

    async def start(self) -> None:
        if "homie" not in self.relays:
            log.warning("RELAY_TOKEN_HOMIE missing: Relay contacts are off")
            return
        for role, relay in self.relays.items():
            me = await relay.me.retrieve()
            self.handles[role] = me["handle"]
            if role == "homie":
                self.owner = me["owner_people"][0]["handle"]
                os.environ["RELAY_OWNER"] = self.owner
        log.info("Relay contacts: %s, owner %s", self.handles, self.owner)
        for role in self.relays:
            if role not in self.state["hello"]:
                await self._hello(role)
        for step in (self.ensure_team_chat, self._ensure_members):
            try:
                await step()
            except Exception as e:
                log.warning("Relay setup step %s failed: %s", step.__name__, e)
        events.subscribe(self.on_team_post)
        events.subscribe_handoffs(self.on_handoff)
        asyncio.ensure_future(self._prewarm())
        asyncio.ensure_future(self._live_requests())
        await asyncio.gather(*(self._listen(role) for role in self.relays))

    async def _hello(self, role: str) -> None:
        p = TEAM[role]
        text = await complete_text(p.voice, [{"role": "user", "content": "Introduce yourself to your owner in one or two short texts' worth of words. Say what you do for them."}],
                                   fallback=f"Hey, it's {p.name}. {p.subtitle}")
        try:
            sent = await self.relays[role].messages.create(
                to=[self.owner], message={"parts": [{"type": "text", "value": text}]}, idempotency_key=f"hello-{self.handles[role]}-{uuid.uuid4().hex[:8]}")
            self.state["direct"][role] = sent["chat_id"]
        except Exception as e:  # a failed hello must never stop the team
            log.warning("hello from %s failed: %s", role, e)
        self.state["hello"].append(role)
        self._save()

    async def ensure_team_chat(self) -> bool:
        if self.state.get("team_chat"):
            return True
        helpers = [self.handles[r] for r in ("calls", "papers", "fix", "policy") if r in self.handles]
        if not helpers:
            return False
        text = await complete_text(TEAM["homie"].voice, [{"role": "user", "content": "You just made a group chat with the student and your four teammates. Welcome them in one short text and say everyone will post updates here."}],
                                   fallback="This is your Homie Team. Everyone posts updates here.")
        try:
            sent = await self.relays["homie"].messages.create(
                to=[self.owner, *helpers], message={"parts": [{"type": "text", "value": text}]}, idempotency_key=f"team-{uuid.uuid4().hex}")
        except Exception as e:
            log.warning("Team chat not created yet (add all five Homie contacts in Relay first): %s", e)
            return False
        self.state["team_chat"] = sent["chat_id"]
        self._save()
        try:
            await self.relays["homie"].chats.update(sent["chat_id"], display_name=GROUP_NAME)
        except Exception as e:
            log.info("Could not name the team chat: %s", e)
        return True

    async def _ensure_members(self) -> None:
        chat_id = self.state.get("team_chat")
        for role, handle in self.handles.items():
            if not chat_id or role == "homie" or role in self.state.setdefault("members", []):
                continue
            try:
                await self.relays["homie"].chats.participants.add(chat_id, handle=handle)
            except Exception as e:
                log.info("add %s to team chat: %s", handle, e)  # already a member, or not yet allowed
            self.state["members"].append(role)
            self._save()

    # ---------- sending ----------

    def _chat_for(self, role: str) -> str | None:
        return self.state.get("team_chat") or self.state["direct"].get(role) or self.state["direct"].get("homie")

    async def send(self, role: str, chat_id: str, text: str, buttons: list[str] | None = None, extra: list[dict] | None = None) -> None:
        relay = self.relays.get(role) or self.relays["homie"]
        parts: list[dict] = [{"type": "text", "value": text[:4000]}]
        if buttons:
            parts.append({"type": "buttons", "items": [{"label": b} for b in buttons]})
        for part in extra or []:
            parts.append(part)
        try:
            await relay.chats.messages.send(chat_id, {"message": {"parts": parts, "idempotency_key": uuid.uuid4().hex}})
        except Exception as e:
            log.warning("Relay send from %s failed: %s", role, e)

    async def in_voice(self, role: str, update: str) -> str:
        """The orchestrator: every message is written with the renter's memory (Mapi), the live state and the team's recent chat."""
        from homie import mapi

        known = "; ".join(m["content"] for m in await mapi.recall(update, limit=5, tags=["profile"]))
        system = (
            f"{TEAM[role].voice}\nStyle: {STYLE.get(role, '')}\n"
            "You're texting in the 'Homie Team' group chat with the renter and your teammates (Homie, Calls, Papers, Fix, Policy, Pics). "
            "Write ONE short, human message (1-2 sentences) with real emotion and 1-2 fitting emoji, like a teammate texting a friend. "
            "React to teammates by name when it fits, never repeat what someone just said, no markdown, no lists unless it's prices. "
            "Keep every number, name and date exactly as given. Never invent facts."
            + (f"\nWhat you remember about the renter (use only if relevant): {known}" if known else "")
            + ("\nRecent team chat:\n" + "\n".join(self.recent[-8:]) if self.recent else "")
        )
        text = await complete_text(system, [{"role": "user", "content": f"Your update to share:\n{update}"}], fallback=update)
        self.recent.append(f"{DISPLAY.get(role, role)}: {text}")
        self.recent = self.recent[-12:]
        return text

    async def on_handoff(self, sender: str, receiver: str, summary: str) -> None:
        """Homie handing work to a teammate shows up in the team chat; bursts (six calls at once) become one message."""
        frm, to = AGENT_ROLE.get(sender), AGENT_ROLE.get(receiver)
        if not frm or not to or frm == to or frm != "homie":
            return
        key = (frm, to)
        first = key not in self._handoffs
        self._handoffs.setdefault(key, []).append(summary)
        if not first:
            return
        await asyncio.sleep(1.5)
        items = self._handoffs.pop(key, [])
        chat_id = self._chat_for(frm)
        if chat_id:
            ask = f"Hand this to {DISPLAY.get(to, to)} by name: " + ("; ".join(items[:6]) + (f" (+{len(items) - 6} more)" if len(items) > 6 else ""))
            await self.send(frm, chat_id, await self.in_voice(frm, ask))

    async def on_team_post(self, role: str, text: str, images: list[str] | None = None) -> None:
        chat_id = self._chat_for(role)
        if chat_id:
            media = [{"type": "media", "url": u} for u in (images or [])[:4]]
            await self.send(role, chat_id, await self.in_voice(role, text), extra=media)

    async def on_homie_reply(self, text: str) -> None:
        role, chat_id = self.route or ("homie", self._chat_for("homie"))
        if not chat_id:
            return
        if text.startswith("[[PAY]]"):
            from homie import payments

            await self.send(role, chat_id, await self.in_voice(role, text[7:].strip()))
            result = await payments.relay_card(self.relays[role], chat_id, "Homie fee: keys in hand")
            if result != "sent":
                await self.send(role, chat_id, "Payments aren't switched on in Relay yet, so this one's on the house. 🏡")
            return
        if text.rstrip().endswith("Approve?"):
            self.awaiting_approval = True
            await self.send(role, chat_id, await self.in_voice(role, text), buttons=["Approve", "Keep looking"])
            return
        if text.rstrip().endswith("Studio, 1, 2 or 3?"):
            self.awaiting_approval = True  # their next message answers Homie directly
            await self.send(role, chat_id, await self.in_voice(role, text), buttons=["Studio", "1 bedroom", "2 bedrooms", "3 bedrooms"])
            return
        await self.send(role, chat_id, await self.in_voice(role, text))
        if text.startswith("Offers so far"):
            carousel = await self._offers_carousel()
            if carousel:
                await self.send(role, chat_id, "Here's who answered:", extra=[carousel])

    async def _offers_carousel(self) -> dict | None:
        state = await hub.get("/api/state")
        cards = []
        for o in (state.get("offers") or {}).values():
            if not o.get("price"):
                continue
            cards.append({
                "title": f"{o['name']} · ${o['price']}/mo",
                "description": f"{o.get('discount') or 'No discount'}. No SSN: {o.get('ssn_alternative') or 'asking'}.",
                "suggestions": [{"type": "view_location", "label": "Map", "query": o.get("address") or o["name"]}],
            })
        return {"type": "carousel", "cards": cards[:10]} if len(cards) >= 2 else None

    # ---------- receiving ----------

    async def _listen(self, role: str) -> None:
        async def on_event(event, context) -> None:
            if event["event_id"] in self.seen:
                return
            data = event["data"]
            if event["event_type"] == "message.received" and data.get("direction") == "inbound":
                await self._on_message(role, data)
            elif event["event_type"] == "payment.succeeded" and role == "homie":
                await hub.step("keys", "done", "Paid on keys (Relay card)")
                events.team_post("homie", "💸 Payment received for Homie's fee. Thank you, and welcome home!")
            elif event["event_type"] == "contact.added":
                await self.ensure_team_chat()
            elif event["event_type"] == "call.created" and data["call"].get("status") == "ringing" \
                    and (data["call"].get("from") or {}).get("kind") != "agent" and role in ("fix", "homie") \
                    and data["call"]["id"] not in self.active_calls:
                self.active_calls.add(data["call"]["id"])  # Relay can deliver the same ring twice: answer once
                task = asyncio.ensure_future(self._video_call(role, data["call"]["id"], data["call"].get("chat_id")))
                self.calls.add(task)
                task.add_done_callback(self.calls.discard)
            self.seen.add(event["event_id"])

        async def on_full_sync(context) -> None:
            pass

        await run_websocket(BASE_URL, env(TEAM[role].token_env), on_event=on_event, on_full_sync=on_full_sync,
                            on_error=lambda e: log.warning("%s websocket: %s", role, e))

    async def _target_role(self, text: str, is_group: bool, receiver: str) -> str | None:
        """In the group chat Jev picks which teammate answers; in a direct chat it's whoever you texted."""
        if not is_group:
            return receiver
        from homie import jev

        who = await jev.teammate_for(text)
        if who and who in self.relays:
            return who
        t = text.lower()
        for role, words in MENTIONS.items():
            if any(w in t for w in words) and role in self.relays and ("@" in t or t.startswith(words)):
                return role
        return "homie"

    async def _pics(self, chat_id: str, text: str) -> None:
        url = re.search(r"https?://\S+", text)
        lowered = text.lower()
        targets = [(url.group(0), "that listing")] if url else [
            (listing_url(b), b["name"]) for b in BUILDINGS.values()
            if b["name"].lower().split()[0] in lowered or b["id"].replace("_", " ") in lowered]
        if not targets:
            reply = await complete_text(TEAM["pics"].voice, [{"role": "user", "content": text + "\n(You can screenshot any listing link they send, or any building from the current search.)"}],
                                        fallback="Send me a listing link or a building name and I'll screenshot it.")
            await self.send("pics", chat_id, reply)
            return
        try:
            await self.relays["pics"].chats.set_activity(chat_id, text="Taking screenshots", emoji="📸")
        except Exception:
            pass
        images = [f"{env('PUBLIC_URL')}/shots/{n}" for n in [await screenshot(u) for u, _ in targets[:3]] if n]
        caption = await self.in_voice("pics", f"Screenshots of {', '.join(label for _, label in targets[:3])}." if images else "That page wouldn't load for me. Try another link?")
        await self.send("pics", chat_id, caption, extra=[{"type": "media", "url": u} for u in images])

    async def _live_requests(self) -> None:
        """Maintenance requests filed on the live web call go to the Repairs agent like any other."""
        while True:
            await asyncio.sleep(2)
            for item in (await hub.post("/api/live/claim", {})).get("items", []):
                if item.get("type") == "message":  # the owner's request handed in from outside Relay
                    self.route = ("homie", self._chat_for("homie"))
                    await self.send("homie", self._chat_for("homie"), await self.in_voice("homie", f"Got your request: {item['text']}"))
                    await self.send_to_homie(item["text"])
                    continue
                self.route = ("fix", self.state["direct"].get("fix") or self._chat_for("fix"))
                photo = item.get("photo")
                if photo and photo.startswith("/"):
                    photo = env("PUBLIC_URL") + photo
                await self.send_to_homie(f"Repair needed: {item.get('title')}. {item.get('details')} "
                                         f"(urgency: {item.get('urgency', 'normal')}, reported on a live video call)"
                                         + (f" photo: {photo}" if photo else ""))

    async def live_card(self, role: str, chat_id: str) -> None:
        url = f"{env('PUBLIC_URL')}/live?role={role}&live=1"
        await self.send(role, chat_id, await self.in_voice(role, "Tap to video chat with me live, I'll see what you show me."),
                        extra=[{"type": "rich_card", "title": f"📹 Talk to {TEAM[role].name} live",
                                "description": "Live 3D video chat. Show me what's broken and I'll file it.",
                                "suggestions": [{"type": "open_url", "label": "Start live call", "url": url, "application": "webview"}]}])

    async def _prewarm(self) -> None:
        from homie.avatar_call import warm

        for role in ("fix", "homie"):
            if (ROOT / "hub" / "static" / "figurines" / role / "talking.mp4").exists():
                continue  # this role uses video loops: nothing to warm
            try:
                await warm(role)
                log.info("avatar for %s is warm", role)
            except Exception as e:
                log.warning("couldn't pre-warm %s avatar: %s", role, e)

    async def _video_call(self, role: str, call_id: str, chat_id: str | None) -> None:
        """Answer with the live 3D avatar; a maintenance request filed on the call goes to the Repairs agent."""
        from homie.avatar_call import run_avatar_call

        async def on_request(title: str, details: str, urgency: str, photo: str | None = None) -> None:
            self.route = ("fix", self.state["direct"].get("fix") or chat_id)
            await self.send_to_homie(f"Repair needed: {title}. {details} (urgency: {urgency}, reported on a video call)"
                                     + (f" photo: {photo}" if photo else ""))

        try:
            await run_avatar_call(env(TEAM[role].token_env), call_id, role, on_request)
        except Exception as e:
            log.exception("video call failed: %s", e)
        finally:
            self.active_calls.discard(call_id)

    async def _memory(self, chat_id: str, text: str) -> None:
        from homie import mapi

        from homie import jev

        move = await jev.memory_action(text)
        remember = move == "remember" if move else ("?" not in text)
        if remember:
            await memory.update(self.owner, text)
            await mapi.remember(text, tags=["profile", "told-directly"], source="relay")
            reply = await self.in_voice("memory", f"Saved. I'll remember: {text}")
        else:
            facts = [m["content"] for m in await mapi.recall(text, limit=10)]
            reply = await complete_text(
                TEAM["memory"].voice + "\nAnswer from these memories only:\n" + ("\n".join(f"- {f}" for f in facts) or "(nothing yet)"),
                [{"role": "user", "content": text}], fallback="; ".join(facts[:3]) or "I don't know that yet.")
        await self.send("memory", chat_id, reply)

    async def _on_message(self, receiver: str, data: dict) -> None:
        if data.get("sender_handle") != self.owner:
            return  # ignore our own team's messages in the group
        text = "\n".join(p.get("value", "") for p in data.get("parts", []) if p.get("type") == "text").strip()
        if not text:
            return
        chat_id = data["chat"]["id"]
        role = await self._target_role(text, bool(data["chat"].get("is_group")), receiver)
        if role != receiver:
            return  # another Homie contact answers this one
        relay = self.relays[role]
        try:
            await relay.chats.start_typing(chat_id)
        except Exception:
            pass

        if role in ("homie", "memory") and self.awaiting_approval:
            self.awaiting_approval = False
            self.route = (role, chat_id)
            await self.send_to_homie(text)
            return

        if role == "pics":
            await self._pics(chat_id, text)
            return
        if role == "fix" and not data["chat"].get("is_group"):
            await self.live_card("fix", chat_id)
        profile = await memory.update(self.owner, text)
        intent = await parse_intent(text)
        prefs = memory.summary(profile)

        if role == "memory":
            if intent.get("intent") in ACTIONS:
                # "get me an apartment" in the Memory chat: Memory hands it to Homie with what it knows, and reports back here.
                self.route = ("memory", chat_id)
                await self.send("memory", chat_id, "On it 🧠 Handing this to Homie and the team with everything I know about you. I'll keep you posted right here.")
                await self.send_to_homie(text + (f"\n\nKnown preferences: {prefs}" if prefs else ""))
                return
            await self._memory(chat_id, text)
            return

        if role == "homie":
            # Everything you text Homie goes to the real Homie agent, which routes it (search, repair, schedule, keys...).
            self.route = ("homie", chat_id)
            await self.send_to_homie(text + (f"\n\nKnown preferences: {prefs}" if prefs and intent.get("intent") == "search" else ""))
            return

        if role in ("fix", "policy") and intent["intent"] in ("search", "repair", "policy"):
            if role == "fix":
                text = f"Repair needed: {text}"
            elif role == "policy" and intent["intent"] != "policy":
                text = f"Policy question: {text}"
            self.route = (role, chat_id)
            await self.send_to_homie(text + (f"\n\nKnown preferences: {prefs}" if prefs and intent["intent"] == "search" else ""))
            return

        from homie import mapi

        recalled = "; ".join(m["content"] for m in await mapi.recall(text, limit=6))
        status = await hub.get("/api/state")
        context = (f"What you remember about them: {prefs or 'nothing yet'}. Related memories: {recalled or 'none'}.\n"
                   f"Live status: {json.dumps({k: status.get(k) for k in ('checklist', 'offers', 'application', 'repairs')})[:3000]}")
        history = self.history.setdefault(f"{role}:{chat_id}", [])
        history.append({"role": "user", "content": text})
        reply = await complete_text(TEAM[role].voice + "\n" + context, history[-12:], fallback="On it.")
        history.append({"role": "assistant", "content": reply})
        await self.send(role, chat_id, reply)
