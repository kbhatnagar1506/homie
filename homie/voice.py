"""Live voice (and video) on Relay calls, powered by Gemini Live on Vertex AI.

One function runs one call: Relay carries the audio and video, Gemini Live
listens, thinks and speaks, the agent's camera shows its avatar, and the
person's camera (when on) goes to the model so it can see what they show.
It returns the transcript when the call ends.
"""

import asyncio
import logging
from pathlib import Path

from PIL import Image
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    LLMRunFrame,
    LLMTextFrame,
    OutputImageRawFrame,
    StartFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService
from pipecat.workers.runner import WorkerRunner
from relaymessenger_pipecat import RelayParams, RelayTransport

from homie.config import ROOT, VERTEX_LOCATION, env

log = logging.getLogger("homie.voice")
W, H, FPS = 720, 1280, 2
BASE_URL = env("RELAY_BASE_URL", "https://api.relayapp.im")
VOICE_LOCATION = env("VOICE_LOCATION", VERTEX_LOCATION)


class AvatarCamera(FrameProcessor):
    """The agent's camera: its avatar, centered on a soft background, a couple of frames a second."""

    def __init__(self, avatar: Path):
        super().__init__()
        img = Image.open(avatar).convert("RGB")
        bg = Image.new("RGB", (W, H), img.getpixel((5, 5)))
        img = img.resize((W, W))
        bg.paste(img, (0, (H - W) // 2))
        self._frame = bg.tobytes()
        self._task = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._task = self.create_task(self._loop())
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._task:
            await self.cancel_task(self._task)

    async def _loop(self):
        while True:
            await self.push_frame(OutputImageRawFrame(image=self._frame, size=(W, H), format="RGB"))
            await asyncio.sleep(1 / FPS)


class Transcript(FrameProcessor):
    """Records what both sides said."""

    def __init__(self, lines: list[str], on_bot_line=None, on_line=None, office: bool = True, bot: bool = True):
        super().__init__()
        self.lines, self._bot, self._on_bot_line, self._on_line = lines, "", on_bot_line, on_line
        self.office, self.bot = office, bot

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not self.bot and isinstance(frame, (TTSTextFrame, LLMTextFrame)):
            pass
        elif not self.office and isinstance(frame, TranscriptionFrame):
            pass
        elif isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._flush()
            self.lines.append(f"office: {frame.text.strip()}")
            if self._on_line:
                self._on_line(self.lines[-1])
        elif isinstance(frame, (TTSTextFrame, LLMTextFrame)):
            self._bot += frame.text
            if self._bot.rstrip().endswith((".", "?", "!")):
                self._flush()
        await self.push_frame(frame, direction)

    def _flush(self):
        if self._bot.strip():
            line = self._bot.strip()
            self.lines.append(f"homie: {line}")
            if self._on_line:
                self._on_line(self.lines[-1])
            if self._on_bot_line:
                self._on_bot_line(line)
        self._bot = ""


async def run_call(token: str, call_id: str, instructions: str, greeting: str, avatar: Path,
                   see_camera: bool = False, max_seconds: int = 180, answer_timeout: int = 40, on_line=None) -> dict:
    """Join a Relay call and talk. Returns {"answered": bool, "transcript": str}."""
    lines: list[str] = []
    joined = asyncio.Event()
    done = asyncio.Event()

    transport = RelayTransport(
        api_key=token, call_id=call_id, base_url=BASE_URL,
        params=RelayParams(audio_in_enabled=True, audio_out_enabled=True,
                           video_out_enabled=True, video_out_is_live=True,
                           video_out_width=W, video_out_height=H, video_out_framerate=FPS,
                           video_in_enabled=see_camera),
    )
    llm = GeminiLiveVertexLLMService(
        credentials_path=env("GOOGLE_APPLICATION_CREDENTIALS") or None,
        location=VOICE_LOCATION,
        project_id=env("GOOGLE_CLOUD_PROJECT", "patchguard-reakon"),
        voice_id=env("VOICE_ID", "Puck"),
        system_instruction=instructions,
    )
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(context)

    def on_bot_line(line: str):
        if any(w in line.lower() for w in ("goodbye", "bye now", "have a great", "take care")):
            asyncio.get_running_loop().call_later(4, done.set)

    worker = PipelineWorker(
        Pipeline([transport.input(), aggregators.user(), llm, Transcript(lines, on_bot_line, on_line),
                  AvatarCamera(avatar), transport.output(), aggregators.assistant()]),
        params=PipelineParams(audio_in_sample_rate=16_000, audio_out_sample_rate=24_000),
        cancel_on_idle_timeout=False,
    )

    @transport.event_handler("on_first_participant_joined")
    async def _joined(transport, participant_id):
        joined.set()
        context.add_message({"role": "user", "content": greeting})
        await worker.queue_frame(LLMRunFrame())

    @transport.event_handler("on_participant_left")
    async def _left(transport, participant_id, reason):
        done.set()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(joined.wait(), answer_timeout)
    except asyncio.TimeoutError:
        await worker.cancel()
        await run_task
        return {"answered": False, "transcript": ""}
    try:
        await asyncio.wait_for(done.wait(), max_seconds)
    except asyncio.TimeoutError:
        pass
    await worker.cancel()
    try:
        await asyncio.wait_for(run_task, 10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return {"answered": True, "transcript": "\n".join(lines)}


AVATARS = ROOT / "relay_app" / "avatars"
