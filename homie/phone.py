"""Real phone calls: Twilio streams the call audio to this server and Gemini Live talks to the leasing office.

Runs inside the hub process (it owns the public URL Twilio connects to). Each live line of the
conversation is pushed to the scoreboard as it's spoken.
"""

import asyncio
import json
import logging
import uuid

from fastapi import WebSocket
from pipecat.frames.frames import EndFrame, LLMRunFrame
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


def register(building_id: str, prompt: str, greeting: str) -> str:
    key = uuid.uuid4().hex
    CALLS[key] = {"building_id": building_id, "prompt": prompt, "greeting": greeting,
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


async def stream(websocket: WebSocket, key: str, on_line) -> None:
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
    llm = GeminiLiveVertexLLMService(
        credentials_path=env("GOOGLE_APPLICATION_CREDENTIALS") or None, location=VOICE_LOCATION,
        project_id=env("GOOGLE_CLOUD_PROJECT", "patchguard-reakon"), voice_id=env("VOICE_ID", "Puck"),
        system_instruction=call["prompt"],
    )
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(context)
    lines: list[str] = []

    worker = PipelineWorker(
        Pipeline([transport.input(), aggregators.user(), llm,
                  Transcript(lines, on_bot_line=lambda line: _maybe_hang_up(line, worker),
                             on_line=lambda line: on_line(call["building_id"], line)),
                  transport.output(), aggregators.assistant()]),
        params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000),
        cancel_on_idle_timeout=False,
    )

    @transport.event_handler("on_client_connected")
    async def _connected(transport, client):
        context.add_message({"role": "user", "content": call["greeting"]})
        await worker.queue_frame(LLMRunFrame())

    @transport.event_handler("on_client_disconnected")
    async def _gone(transport, client):
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    try:
        await asyncio.wait_for(runner.run(), float(env("MAX_CALL_SECONDS", "240")))
    except asyncio.TimeoutError:
        await worker.cancel()
    finish(key, answered=any(l.startswith("office:") for l in lines), transcript="\n".join(lines))


def _maybe_hang_up(line: str, worker: PipelineWorker) -> None:
    if any(w in line.lower() for w in ("goodbye", "bye now", "have a great", "take care")):
        loop = asyncio.get_running_loop()
        loop.call_later(4, lambda: asyncio.ensure_future(worker.queue_frame(EndFrame())))  # EndFrame hangs up via Twilio
