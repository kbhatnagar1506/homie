"""Video calls with a live 3D Homie: the avatar's face is the agent's camera, lip-synced to its voice.

    Relay call ─▶ speech-to-text ─▶ Gemini (tools: file_maintenance_request) ─▶ ElevenLabs voice
                                                                             └▶ word timings ─▶ CMU visemes ─▶ 3D mouth
    Jev classifies each sentence's emotion ─▶ face and body.  The 3D page renders in headless Chromium and
    its frames stream into the call.

With ELEVENLABS_API_KEY: ElevenLabs Scribe (STT) + ElevenLabs streaming TTS with exact word timestamps.
Without it: Gemini Live hears and speaks, and the mouth follows its transcript with dataset-estimated timing.
"""

import asyncio
import base64
import io
import logging
import re
import time

from PIL import Image
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
    OutputImageRawFrame,
    StartFrame,
    TTSTextFrame,
    UserImageRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner
from relaymessenger_pipecat import RelayParams, RelayTransport

from homie import jev
from homie.config import HUB_URL, VERTEX_LOCATION, env
from homie.lipsync import estimate_ms, timeline

log = logging.getLogger("homie.avatar")
W, H, FPS = 540, 960, 24


class AvatarRenderer:
    """The 3D avatar page in headless Chromium; its frames arrive through the DevTools screencast."""

    def __init__(self, role: str):
        self.role, self.frame, self._pw, self._browser, self.page = role, None, None, None, None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader",
                                                             "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                                                             "--autoplay-policy=no-user-gesture-required"])
        self.page = await self._browser.new_page(viewport={"width": W, "height": H})
        await self.page.goto(f"{HUB_URL}/avatar?role={self.role}", wait_until="networkidle")
        await self.page.wait_for_function("window.homieReady === true", timeout=20000)
        cdp = await self.page.context.new_cdp_session(self.page)

        async def on_frame(evt):
            img = Image.open(io.BytesIO(base64.b64decode(evt["data"]))).convert("RGB")
            if img.size != (W, H):
                img = img.resize((W, H))
            self.frame = img.tobytes()
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": evt["sessionId"]})
            except Exception:
                pass  # page closed at the end of the call

        cdp.on("Page.screencastFrame", lambda e: asyncio.ensure_future(on_frame(e)))
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 88, "maxWidth": W, "maxHeight": H, "everyNthFrame": 1})

    async def js(self, code: str, *args) -> None:
        if self.page:
            try:
                await self.page.evaluate(code, *args) if args else await self.page.evaluate(code)
            except Exception as e:
                log.debug("avatar js failed: %s", e)

    async def word(self, text: str, ms: float) -> None:
        await self.js("([w, f]) => homie.word(w, f)", [text, timeline(text, ms)])

    async def stop(self) -> None:
        await self.js("homie.stop()")

    async def emotion(self, name: str) -> None:
        await self.js("(n) => homie.emotion(n)", name)

    async def ticket(self, title: str, text: str, status: str = "") -> None:
        await self.js("([a, b, c]) => homie.ticket(a, b, c)", [title, text, status])

    async def reset(self) -> None:
        """Fresh face for the next call without relaunching the browser."""
        await self.page.reload(wait_until="networkidle")
        await self.page.wait_for_function("window.homieReady === true", timeout=20000)

    async def close(self) -> None:
        try:
            await self._browser.close()
            await self._pw.stop()
        except Exception:
            pass


class AvatarCamera(FrameProcessor):
    """Pushes the avatar's latest rendered frame into the call at FPS."""

    def __init__(self, renderer: AvatarRenderer):
        super().__init__()
        self.renderer, self._task = renderer, None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._task = self.create_task(self._loop())
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._task:
            await self.cancel_task(self._task)

    async def _loop(self):
        while True:
            if self.renderer.frame:
                await self.push_frame(OutputImageRawFrame(image=self.renderer.frame, size=(W, H), format="RGB"))
            await asyncio.sleep(1 / FPS)


class CameraTap(FrameProcessor):
    """Keeps the renter's latest camera frame so the agent can 'take a picture' when they show it something."""

    def __init__(self):
        super().__init__()
        self.latest = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserImageRawFrame):
            self.latest = frame
            return  # don't send raw video down the voice pipeline
        await self.push_frame(frame, direction)

    def snapshot(self) -> bytes | None:
        f = self.latest
        if not f:
            return None
        img = Image.frombytes("RGB" if f.format in (None, "RGB") else f.format, f.size, f.image).convert("RGB")
        img.thumbnail((1024, 1024))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()


from homie.vision import describe_photo  # noqa: E402,F401  (kept importable from here)


class LoopCamera(FrameProcessor):
    """The workshop approach: two looping clips as the agent's camera, talking while the bot speaks, listening otherwise.
    Frames come straight from ffmpeg (no browser); a ticket card can be drawn on top once a request is filed."""

    def __init__(self, talking: str, listening: str, w: int = 720, h: int = 1280, fps: int = 20):
        super().__init__()
        self._files, self.w, self.h, self.fps = (talking, listening), w, h, fps
        self._speaking, self._task, self.overlay = False, None, None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._task = self.create_task(self._play())
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._task:
            await self.cancel_task(self._task)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking = False

    def set_ticket(self, title: str, details: str, status: str = "sent to office") -> None:
        """Draw a maintenance-ticket card once; it's composited onto every frame after that."""
        from PIL import ImageDraw, ImageFont

        card = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        x0, y0, x1, y1 = 36, self.h - 420, self.w - 36, self.h - 160
        d.rounded_rectangle((x0, y0, x1, y1), 28, fill=(255, 255, 255, 240))
        def font(size, bold=False):
            for path in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
                         else "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
            return ImageFont.load_default(size=size)

        big, small = font(36, True), font(26)
        d.text((x0 + 26, y0 + 22), "MAINTENANCE REQUEST", font=small, fill=(110, 110, 110, 255))
        d.rounded_rectangle((x1 - 230, y0 + 18, x1 - 22, y0 + 58), 18, fill=(212, 245, 226, 255))
        d.text((x1 - 212, y0 + 24), status, font=small, fill=(19, 122, 61, 255))
        d.text((x0 + 26, y0 + 70), title[:28], font=big, fill=(20, 20, 20, 255))
        y, line = y0 + 126, ""
        for word in details.split():
            if d.textlength(line + word, font=small) > (x1 - x0 - 52):
                d.text((x0 + 26, y), line, font=small, fill=(50, 50, 50, 255)); y += 34; line = ""
                if y > y1 - 40:
                    break
            line += word + " "
        if y <= y1 - 40:
            d.text((x0 + 26, y), line, font=small, fill=(50, 50, 50, 255))
        self.overlay = card

    async def _play(self):
        import imageio_ffmpeg

        ff = imageio_ffmpeg.get_ffmpeg_exe()
        players = [await asyncio.create_subprocess_exec(
            ff, "-v", "error", "-stream_loop", "-1", "-re", "-i", f, "-an",
            "-vf", f"scale={self.w}:{self.h},fps={self.fps}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
            stdout=asyncio.subprocess.PIPE) for f in self._files]
        size = self.w * self.h * 3
        try:
            while True:
                talking, listening = [await p.stdout.readexactly(size) for p in players]
                image = talking if self._speaking else listening
                if self.overlay is not None:
                    image = Image.alpha_composite(Image.frombytes("RGB", (self.w, self.h), image).convert("RGBA"), self.overlay).convert("RGB").tobytes()
                await self.push_frame(OutputImageRawFrame(image=image, size=(self.w, self.h), format="RGB"))
        finally:
            for p in players:
                p.kill()


EMOTIONS = {"calm": "Calm, informative", "happy": "Warm and pleased", "excited": "Excited, celebrating good news",
            "concerned": "Sympathetic about a problem or bad news", "thinking": "Considering, asking a question or checking"}


class LipSync(FrameProcessor):
    """Sits after transport.output(), where spoken-text frames arrive in time with the audio being played.
    Each word becomes CMU-dictionary visemes over its duration; each sentence gets a Jev-classified emotion."""

    def __init__(self, renderer: AvatarRenderer, words_are_timed: bool):
        super().__init__()
        self.r, self.timed, self._sentence, self._last = renderer, words_are_timed, "", 0.0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and frame.text.strip():
            words = frame.text.split()
            if self.timed and len(words) == 1:
                await self.r.word(words[0], estimate_ms(words[0]))
            else:  # a phrase at once: play its words back to back
                asyncio.ensure_future(self._play(words))
        elif isinstance(frame, LLMTextFrame):
            self._sentence += frame.text
            if re.search(r"[.!?]\s*$", self._sentence):
                asyncio.ensure_future(self._feel(self._sentence))
                self._sentence = ""
        elif isinstance(frame, LLMFullResponseEndFrame) and self._sentence.strip():
            asyncio.ensure_future(self._feel(self._sentence))
            self._sentence = ""
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self.r.stop()
        await self.push_frame(frame, direction)

    async def _play(self, words: list[str]):
        for w in words:
            ms = estimate_ms(w)
            await self.r.word(w, ms)
            await asyncio.sleep(ms / 1000)

    async def _feel(self, sentence: str):
        a = await jev.ask({"sentence_the_avatar_is_saying": sentence},
                          {"emotion": __import__("typesafe_sdk").Choice(instructions="Which emotion should the avatar show while saying this?", criteria=EMOTIONS)},
                          label="avatar emotion")
        if a and a["emotion"].confidence >= 0.5:
            await self.r.emotion(a["emotion"].choice)


FIX_PROMPT = (
    "You are Homie Fix, a warm, dependable maintenance friend on a live video call with a renter. Speak in short, natural "
    "sentences, like a real person on FaceTime: friendly, a little playful, never robotic, no lists or markdown. "
    "The moment the renter says or hints that something is broken, leaking or not working: say one quick natural line "
    "like 'Oh no, okay, show it to me, I'm looking at it now' and immediately call look_at_problem. Then tell them in one "
    "sentence what you see. Ask at most one quick follow-up (since when, or okay to enter when they're out), then call "
    "file_maintenance_request with a short title and details that include what you saw, tell them it's filed and that "
    "you're calling the office now. If the camera is off, ask them to turn it on and point it at the problem. If it sounds dangerous (gas, fire, flooding, sparks), tell them to get "
    "safe and call 911 first. {memory}"
)


_warm: dict[str, AvatarRenderer] = {}


async def warm(role: str) -> AvatarRenderer:
    """Keep a rendered avatar ready per role, so answering a call is instant."""
    r = _warm.get(role)
    if r is None or r.page is None:
        r = AvatarRenderer(role)
        await r.start()
        _warm[role] = r
    return r


class _NoRenderer:
    """Stand-in when the call uses video loops: the browser-only effects become no-ops."""

    def __init__(self, loop: "LoopCamera"):
        self.loop = loop

    async def js(self, *a):
        pass

    async def emotion(self, *a):
        pass

    async def ticket(self, title, details, status=""):
        self.loop.set_ticket(title, details, status or "sent to office")

    async def reset(self):
        pass

    async def close(self):
        pass


async def run_avatar_call(token: str, call_id: str, role: str, on_request) -> None:
    """Answer a Relay video call as the figurine (Veo loops, like the Relay workshop) or the 3D avatar as a fallback."""
    from homie import mapi
    from homie.config import ROOT

    camera = CameraTap()
    seen = {"photo": None, "description": ""}
    clips = ROOT / "hub" / "static" / "figurines" / role
    loops = None
    if (clips / "talking.mp4").exists() and (clips / "listening.mp4").exists():
        loops = LoopCamera(str(clips / "talking.mp4"), str(clips / "listening.mp4"))
        renderer = _NoRenderer(loops)
        recalled = await asyncio.wait_for(mapi.recall("renter name apartment building unit preferences", limit=5), 1.5) if True else []
    else:
        renderer, recalled = await asyncio.gather(warm(role), asyncio.wait_for(
            mapi.recall("renter name apartment building unit preferences", limit=5), 1.5), return_exceptions=True)
        if isinstance(renderer, Exception):
            raise renderer
    known = "; ".join(m["content"] for m in recalled) if isinstance(recalled, list) else ""
    instructions = FIX_PROMPT.format(memory=f"What you remember about them: {known}" if known else "")

    vw, vh, vfps = (loops.w, loops.h, loops.fps) if loops else (W, H, FPS)
    transport = RelayTransport(api_key=token, call_id=call_id, base_url=env("RELAY_BASE_URL", "https://api.relayapp.im"),
                               params=RelayParams(audio_in_enabled=True, audio_out_enabled=True, video_out_enabled=True,
                                                  video_out_is_live=True, video_out_width=vw, video_out_height=vh,
                                                  video_out_framerate=vfps, video_in_enabled=True))

    async def look(params):
        """Take a picture from the renter's camera and analyse it."""
        jpeg = camera.snapshot()
        if not jpeg:
            await params.result_callback({"camera": "off", "say": "Ask them to turn on their camera and point it at the problem."})
            return
        import uuid as _uuid

        from homie.config import PUBLIC_URL, ROOT

        name = f"{_uuid.uuid4().hex[:12]}.jpg"
        (ROOT / "data" / "shots").mkdir(parents=True, exist_ok=True)
        (ROOT / "data" / "shots" / name).write_bytes(jpeg)
        seen["photo"] = f"{PUBLIC_URL}/shots/{name}"
        await renderer.js("(u) => homie.snapshot(u)", "data:image/jpeg;base64," + base64.b64encode(jpeg).decode())
        await renderer.emotion("thinking")
        seen["description"] = await describe_photo(jpeg, params.arguments.get("what_they_said", ""))
        await params.result_callback({"what_i_see": seen["description"] or "The picture is unclear.", "photo_saved": bool(seen["photo"])})

    async def file_request(params):
        args = params.arguments
        title, details, urgency = args.get("title", "Maintenance request"), args.get("details", ""), args.get("urgency", "normal")
        if seen["description"] and seen["description"][:40] not in details:
            details = f"{details}\nSeen on camera: {seen['description']}"
        await renderer.ticket(title, details, "sent to office")
        await renderer.emotion("happy")
        await on_request(title, details, urgency, seen["photo"])
        await params.result_callback({"status": "filed", "next": "Homie Fix is calling the office to book a repair slot"})

    tool = {"name": "file_maintenance_request", "description": "File the renter's maintenance request and start calling the office.",
            "properties": {"title": {"type": "string", "description": "Short title, e.g. 'Ice maker not working'"},
                           "details": {"type": "string", "description": "Where, what happens, since when, access notes"},
                           "urgency": {"type": "string", "enum": ["normal", "urgent", "emergency"]}},
            "required": ["title", "details"]}
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    tools = ToolsSchema(standard_tools=[
        FunctionSchema(name="look_at_problem", description="Take a picture from the renter's camera and analyse what's broken. Call it as soon as they say something is broken.",
                       properties={"what_they_said": {"type": "string", "description": "What the renter said is wrong"}}, required=[]),
        FunctionSchema(name=tool["name"], description=tool["description"], properties=tool["properties"], required=tool["required"])])
    context = LLMContext(tools=tools)
    aggregators = LLMContextAggregatorPair(context)
    project, creds = env("GOOGLE_CLOUD_PROJECT", "patchguard-reakon"), env("GOOGLE_APPLICATION_CREDENTIALS") or None

    if env("ELEVENLABS_API_KEY"):
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        from pipecat.services.google.vertex.llm import GoogleVertexLLMService

        stt = ElevenLabsRealtimeSTTService(api_key=env("ELEVENLABS_API_KEY"))
        llm = GoogleVertexLLMService(credentials_path=creds, project_id=project, location=VERTEX_LOCATION,
                                     settings=GoogleVertexLLMService.Settings(model=env("CALL_MODEL", "gemini-2.5-flash-lite"),
                                                                              system_instruction=instructions))
        tts = ElevenLabsTTSService(api_key=env("ELEVENLABS_API_KEY"), voice_id=env("ELEVENLABS_VOICE_ID", "SOYHLrjzK2X1ezoPC6cr"),
                                   model="eleven_flash_v2_5")
        llm.register_function("file_maintenance_request", file_request)
        llm.register_function("look_at_problem", look)
        stages = [transport.input(), camera, stt, aggregators.user(), llm, tts, loops or AvatarCamera(renderer), transport.output(),
                  *([] if loops else [LipSync(renderer, words_are_timed=True)]), aggregators.assistant()]
        rates = PipelineParams(audio_in_sample_rate=16_000, audio_out_sample_rate=24_000)
    else:
        from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService

        llm = GeminiLiveVertexLLMService(credentials_path=creds, location=VERTEX_LOCATION, project_id=project,
                                         voice_id=env("VOICE_ID", "Puck"), system_instruction=instructions, tools=tools)
        llm.register_function("file_maintenance_request", file_request)
        llm.register_function("look_at_problem", look)
        stages = [transport.input(), camera, aggregators.user(), llm, loops or AvatarCamera(renderer), transport.output(),
                  *([] if loops else [LipSync(renderer, words_are_timed=False)]), aggregators.assistant()]
        rates = PipelineParams(audio_in_sample_rate=16_000, audio_out_sample_rate=24_000)

    worker = PipelineWorker(Pipeline(stages), params=rates, cancel_on_idle_timeout=False)

    greeted = {"done": False}

    async def greet(*_):
        """They called us, so speak the moment media connects instead of waiting for their video."""
        if greeted["done"]:
            return
        greeted["done"] = True
        context.add_message({"role": "user", "content": "The renter just called you on video. Greet them warmly by name if you know it, in one short sentence, and ask what's broken."})
        await worker.queue_frame(LLMRunFrame())

    transport.event_handler("on_connected")(greet)
    transport.event_handler("on_first_participant_joined")(greet)

    @transport.event_handler("on_participant_left")
    async def _left(transport, participant_id, reason):
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    started = time.time()
    try:
        await asyncio.wait_for(runner.run(), float(env("MAX_VIDEO_CALL_SECONDS", "900")))
    except asyncio.TimeoutError:
        await worker.cancel()
    finally:
        try:
            await renderer.reset()
        except Exception:
            await renderer.close()
            _warm.pop(role, None)
        log.info("avatar call %s ended after %.0fs", call_id, time.time() - started)
