"""Real phone calls: Twilio streams the call audio to this server and Gemini Live talks to the leasing office.

Runs inside the hub process (it owns the public URL Twilio connects to). Each live line of the
conversation is pushed to the scoreboard as it's spoken.
"""

import asyncio
import json
import logging
import uuid

from fastapi import WebSocket
from pipecat.frames.frames import EndFrame, InputAudioRawFrame, LLMRunFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from homie.config import env
from homie.voice import VOICE_LOCATION, Transcript

log = logging.getLogger("homie.phone")
CALLS: dict[str, dict] = {}
LISTENERS: dict[str, set[asyncio.Queue]] = {}  # call key -> browsers listening in


def listen(key: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=400)
    LISTENERS.setdefault(key, set()).add(q)
    return q


def unlisten(key: str, q: asyncio.Queue) -> None:
    LISTENERS.get(key, set()).discard(q)


def live() -> list[dict]:
    return [{"key": k, "building_id": c["building_id"]} for k, c in CALLS.items() if c.get("live")]


def _broadcast(key: str, speaker: int, rate: int, pcm: bytes) -> None:
    """One packet: 1 byte speaker (0 office, 1 Homie), 4 bytes sample rate, then 16-bit PCM."""
    packet = bytes([speaker]) + rate.to_bytes(4, "little") + pcm
    for q in list(LISTENERS.get(key, ())):
        if not q.full():
            q.put_nowait(packet)


class AudioTap(FrameProcessor):
    """Copies call audio to anyone listening in from the dashboard. Office audio is tapped on the way in, Homie's on the way out."""

    def __init__(self, key: str, outbound: bool):
        super().__init__()
        self.key, self.outbound = key, outbound

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if LISTENERS.get(self.key):
            if self.outbound and isinstance(frame, OutputAudioRawFrame):
                _broadcast(self.key, 1, frame.sample_rate, frame.audio)
            elif not self.outbound and isinstance(frame, InputAudioRawFrame):
                _broadcast(self.key, 0, frame.sample_rate, frame.audio)
        await self.push_frame(frame, direction)


def register(building_id: str, prompt: str, greeting: str, first_message: str = "") -> str:
    key = uuid.uuid4().hex
    CALLS[key] = {"building_id": building_id, "prompt": prompt, "greeting": greeting, "first_message": first_message,
                  "done": asyncio.Event(), "result": {"answered": False, "transcript": ""}}
    return key


def finish(key: str, **result) -> None:
    call = CALLS.get(key)
    if call and not call["done"].is_set():
        call["result"].update(result)
        call["done"].set()


async def wait(key: str, timeout: float) -> dict:
    call = CALLS.get(key)
    if not call:
        return {"answered": False, "transcript": "", "error": "unknown call"}
    try:
        await asyncio.wait_for(call["done"].wait(), timeout)
    except asyncio.TimeoutError:
        pass
    return CALLS.pop(key, call)["result"]


def _ulaw_table() -> list[int]:
    out = []
    for b in range(256):
        u = ~b & 0xFF
        t = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
        out.append(0x84 - t if u & 0x80 else t - 0x84)
    return out


ULAW = _ulaw_table()


def _ulaw_to_pcm(data: bytes) -> bytes:
    import array

    return array.array("h", (ULAW[b] for b in data)).tobytes()


async def stream(websocket: WebSocket, key: str, on_line) -> None:
    if (env("ELEVENLABS_PHONE_AGENT_ID") or env("ELEVENLABS_BACKUP_PHONE_AGENT_ID")) and env("PHONE_VOICE", "elevenlabs") == "elevenlabs":
        return await stream_elevenlabs(websocket, key, on_line)
    return await stream_pipecat(websocket, key, on_line)


async def stream_elevenlabs(websocket: WebSocket, key: str, on_line) -> None:
    """Twilio <-> ElevenLabs Agent, both in ulaw_8000, so audio passes straight through with no transcoding.

    ElevenLabs does the listening, thinking (its co-located LLM), turn-taking, barge-in and voice. This
    bridge only relays audio, taps it for anyone listening in, and records the transcript.
    """
    import base64

    import httpx
    import websockets

    call = CALLS.get(key)
    if not call:
        await websocket.close()
        return
    await websocket.accept()
    stream_sid = call_sid = ""
    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        if msg.get("event") == "start":
            stream_sid, call_sid = msg["start"]["streamSid"], msg["start"].get("callSid", "")
            break
    from homie import eleven

    signed = None
    first_pick = await eleven.best(need_phone_agent=True)
    for acct in [first_pick] + [a for a in eleven.accounts() if a is not first_pick and a["phone_agent"]]:
        if not acct:
            continue
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                                 params={"agent_id": acct["phone_agent"]}, headers={"xi-api-key": acct["key"]})
        if r.status_code == 200:
            signed = r.json()["signed_url"]
            break
        log.warning("ElevenLabs account refused the call (%s), trying the backup", r.status_code)
    if not signed:
        call["live"] = False
        finish(key, answered=False, status="voice unavailable")
        await websocket.close()
        return
    lines: list[str] = []
    call["live"] = True
    first = call.get("first_message") or "Hi, this is Homie, an AI assistant calling on behalf of a student. Do you have a quick minute?"

    def line(text: str) -> None:
        lines.append(text)
        on_line(call["building_id"], text)

    async with websockets.connect(signed, max_size=None) as el:
        await el.send(json.dumps({"type": "conversation_initiation_client_data",
                                  "dynamic_variables": {"instructions": call["prompt"], "first_message": first},
                                  "conversation_config_override": {"agent": {"first_message": first}}}))

        async def twilio_to_el():
            async for raw in websocket.iter_text():
                msg = json.loads(raw)
                if msg.get("event") == "media":
                    payload = msg["media"]["payload"]
                    await el.send(json.dumps({"user_audio_chunk": payload}))
                    if LISTENERS.get(key):
                        _broadcast(key, 0, 8000, _ulaw_to_pcm(base64.b64decode(payload)))
                elif msg.get("event") == "stop":
                    return

        async def el_to_twilio():
            async for raw in el:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "audio":
                    payload = msg["audio_event"]["audio_base_64"]
                    await websocket.send_text(json.dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}))
                    if LISTENERS.get(key):
                        _broadcast(key, 1, 8000, _ulaw_to_pcm(base64.b64decode(payload)))
                elif kind == "interruption":
                    await websocket.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
                elif kind == "ping":
                    await el.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))
                elif kind == "user_transcript":
                    text = msg["user_transcription_event"]["user_transcript"].strip()
                    if text:
                        line(f"office: {text}")
                elif kind == "agent_response":
                    text = msg["agent_response_event"]["agent_response"].strip()
                    if text:
                        line(f"homie: {text}")
                elif kind == "conversation_initiation_metadata":
                    call["conversation_id"] = msg["conversation_initiation_metadata_event"]["conversation_id"]

        tasks = [asyncio.ensure_future(twilio_to_el()), asyncio.ensure_future(el_to_twilio())]
        try:
            await asyncio.wait(tasks, timeout=float(env("MAX_CALL_SECONDS", "300")), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
    call["live"] = False
    await _hang_up(call_sid)
    try:
        await websocket.close()
    except Exception:
        pass
    finish(key, answered=any(l.startswith("office:") for l in lines), transcript="\n".join(lines),
           conversation_id=call.get("conversation_id", ""))


async def _hang_up(call_sid: str) -> None:
    sid, token = env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN")
    if not (call_sid.startswith("CA") and sid and token):
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls/{call_sid}.json",
                              auth=(sid, token), data={"Status": "completed"})
    except Exception as e:
        log.warning("hang up failed: %s", e)


async def stream_pipecat(websocket: WebSocket, key: str, on_line) -> None:
    call = CALLS.get(key)
    if not call:
        await websocket.close()
        return
    await websocket.accept()
    messages = websocket.iter_text()
    await messages.__anext__()                              # "connected"
    start = json.loads(await messages.__anext__())["start"]  # "start": stream and call ids
    sid, token = env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN")
    serializer = TwilioFrameSerializer(stream_sid=start["streamSid"], call_sid=start["callSid"],
                                       account_sid=sid or None, auth_token=token or None,
                                       params=TwilioFrameSerializer.InputParams(auto_hang_up=bool(sid and token)))
    transport = FastAPIWebsocketTransport(websocket, params=FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer))
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(context)
    lines: list[str] = []
    worker: PipelineWorker | None = None
    say_line = lambda line: on_line(call["building_id"], line)  # noqa: E731
    hang_up = lambda line: _maybe_hang_up(line, worker)  # noqa: E731
    project, creds = env("GOOGLE_CLOUD_PROJECT", "patchguard-reakon"), env("GOOGLE_APPLICATION_CREDENTIALS") or None

    if env("ELEVENLABS_API_KEY") and env("PHONE_VOICE", "elevenlabs") == "elevenlabs":
        # ElevenLabs hears and speaks (Scribe realtime + Flash v2.5); Gemini on Vertex does the thinking.
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        from pipecat.services.google.vertex.llm import GoogleVertexLLMService

        from homie.config import VERTEX_LOCATION

        stt = ElevenLabsRealtimeSTTService(api_key=env("ELEVENLABS_API_KEY"))
        llm = GoogleVertexLLMService(credentials_path=creds, project_id=project, location=VERTEX_LOCATION,
                                     settings=GoogleVertexLLMService.Settings(model=env("CALL_MODEL", "gemini-2.5-flash-lite"),
                                                                              system_instruction=call["prompt"]))
        tts = ElevenLabsTTSService(api_key=env("ELEVENLABS_API_KEY"), voice_id=env("PHONE_VOICE_ID", env("ELEVENLABS_VOICE_ID", "iP95p4xoKVk53GoZ742B")),
                                   model="eleven_flash_v2_5")
        stages = [transport.input(), AudioTap(key, outbound=False), stt,
                  Transcript(lines, on_line=say_line, bot=False), aggregators.user(), llm, tts,
                  Transcript(lines, on_bot_line=hang_up, on_line=say_line, office=False),
                  AudioTap(key, outbound=True), transport.output(), aggregators.assistant()]
    else:
        llm = GeminiLiveVertexLLMService(
            credentials_path=creds, location=VOICE_LOCATION, project_id=project, voice_id=env("VOICE_ID", "Puck"),
            system_instruction=call["prompt"],
        )
        stages = [transport.input(), AudioTap(key, outbound=False), aggregators.user(), llm,
                  Transcript(lines, on_bot_line=hang_up, on_line=say_line),
                  AudioTap(key, outbound=True), transport.output(), aggregators.assistant()]

    worker = PipelineWorker(Pipeline(stages), params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000),
                            cancel_on_idle_timeout=False)

    @transport.event_handler("on_client_connected")
    async def _connected(transport, client):
        context.add_message({"role": "user", "content": call["greeting"]})
        await worker.queue_frame(LLMRunFrame())

    @transport.event_handler("on_client_disconnected")
    async def _gone(transport, client):
        await worker.cancel()

    call["live"] = True
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    try:
        await asyncio.wait_for(runner.run(), float(env("MAX_CALL_SECONDS", "240")))
    except asyncio.TimeoutError:
        await worker.cancel()
    call["live"] = False
    finish(key, answered=any(l.startswith("office:") for l in lines), transcript="\n".join(lines))


def _maybe_hang_up(line: str, worker: PipelineWorker) -> None:
    if any(w in line.lower() for w in ("goodbye", "bye now", "have a great", "take care")):
        loop = asyncio.get_running_loop()
        loop.call_later(4, lambda: asyncio.ensure_future(worker.queue_frame(EndFrame())))  # EndFrame hangs up via Twilio
