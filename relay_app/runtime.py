"""Runs the five Homie contacts on Relay.

Relay is the face, the Fetch agents are the brain: Homie forwards real work
(search, repairs, policy questions) to the Homie uAgent, and every specialist
agent's progress is posted by its own Relay contact into the "Homie Team"
group chat (or that contact's direct chat until the group exists).
"""

import asyncio
import json
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
from relay_app.team import GROUP_NAME, TEAM

log = logging.getLogger("homie.relay")
STATE = ROOT / "data" / "relay_state.json"
BASE_URL = env("RELAY_BASE_URL", "https://api.relayapp.im")
MENTIONS = {"calls": ("calls", "call"), "papers": ("papers", "paperwork", "documents"), "fix": ("fix", "repair"), "policy": ("policy", "rights", "lawyer"), "pics": ("pics", "pictures", "photos", "screenshot")}


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
        log.info("Relay contacts: %s, owner %s", self.handles, self.owner)
        for role in self.relays:
            if role not in self.state["hello"]:
                await self._hello(role)
        await self.ensure_team_chat()
        await self._ensure_members()
        events.subscribe(self.on_team_post)
        await asyncio.gather(*(self._listen(role) for role in self.relays))

    async def _hello(self, role: str) -> None:
        p = TEAM[role]
        text = await complete_text(p.voice, [{"role": "user", "content": "Introduce yourself to your owner in one or two short texts' worth of words. Say what you do for them."}],
                                   fallback=f"Hey, it's {p.name}. {p.subtitle}")
        sent = await self.relays[role].messages.create(
            to=[self.owner], message={"parts": [{"type": "text", "value": text}]}, idempotency_key=f"hello-{self.handles[role]}")
        self.state["direct"][role] = sent["chat_id"]
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
        return await complete_text(TEAM[role].voice, [{"role": "user", "content": f"Text the student this update in your own words. Keep every number, name and date exactly:\n{update}"}], fallback=update)

    async def on_team_post(self, role: str, text: str, images: list[str] | None = None) -> None:
        chat_id = self._chat_for(role)
        if chat_id:
            media = [{"type": "media", "url": u} for u in (images or [])[:4]]
            await self.send(role, chat_id, await self.in_voice(role, text), extra=media)

    async def on_homie_reply(self, text: str) -> None:
        role, chat_id = self.route or ("homie", self._chat_for("homie"))
        if not chat_id:
            return
        if text.rstrip().endswith("Approve?"):
            self.awaiting_approval = True
            await self.send("homie", chat_id, await self.in_voice("homie", text), buttons=["Approve", "Keep looking"])
            return
        await self.send(role, chat_id, await self.in_voice(role, text))
        if text.startswith("Offers so far"):
            carousel = await self._offers_carousel()
            if carousel:
                await self.send("homie", chat_id, "Here's who answered:", extra=[carousel])

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
            elif event["event_type"] == "contact.added":
                await self.ensure_team_chat()
            self.seen.add(event["event_id"])

        async def on_full_sync(context) -> None:
            pass

        await run_websocket(BASE_URL, env(TEAM[role].token_env), on_event=on_event, on_full_sync=on_full_sync,
                            on_error=lambda e: log.warning("%s websocket: %s", role, e))

    def _target_role(self, text: str, is_group: bool, receiver: str) -> str | None:
        if not is_group:
            return receiver
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

    async def _on_message(self, receiver: str, data: dict) -> None:
        if data.get("sender_handle") != self.owner:
            return  # ignore our own team's messages in the group
        text = "\n".join(p.get("value", "") for p in data.get("parts", []) if p.get("type") == "text").strip()
        if not text:
            return
        chat_id = data["chat"]["id"]
        role = self._target_role(text, bool(data["chat"].get("is_group")), receiver)
        if role != receiver:
            return  # another Homie contact answers this one
        relay = self.relays[role]
        try:
            await relay.chats.start_typing(chat_id)
        except Exception:
            pass

        if role == "homie" and self.awaiting_approval:
            self.awaiting_approval = False
            self.route = ("homie", chat_id)
            await self.send_to_homie(text)
            return

        if role == "pics":
            await self._pics(chat_id, text)
            return

        profile = await memory.update(self.owner, text)
        intent = await parse_intent(text)
        prefs = memory.summary(profile)

        if role in ("homie", "fix", "policy") and intent["intent"] in ("search", "repair", "policy"):
            if role == "fix":
                text = f"Repair needed: {text}"
            elif role == "policy" and intent["intent"] != "policy":
                text = f"Policy question: {text}"
            self.route = (role, chat_id)
            await self.send_to_homie(text + (f"\n\nKnown preferences: {prefs}" if prefs and intent["intent"] == "search" else ""))
            return

        status = await hub.get("/api/state")
        context = (f"What you remember about them: {prefs or 'nothing yet'}.\n"
                   f"Live status: {json.dumps({k: status.get(k) for k in ('checklist', 'offers', 'application', 'repairs')})[:3000]}")
        history = self.history.setdefault(f"{role}:{chat_id}", [])
        history.append({"role": "user", "content": text})
        reply = await complete_text(TEAM[role].voice + "\n" + context, history[-12:], fallback="On it.")
        history.append({"role": "assistant", "content": reply})
        await self.send(role, chat_id, reply)
