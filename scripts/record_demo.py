"""Screen-record the demo: mission control, /flow and the swipe page, while a demo-mode search runs locally.

    python -m scripts.record_demo "find me a 1 bedroom off campus near UC Berkeley under 3500, no SSN"

Videos land in demo_videos/ as .mp4 (no audio: browser recordings don't capture sound).
"""

import agents  # noqa: F401
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo_videos"
HUB = "http://localhost:8080"


async def main() -> None:
    ask = sys.argv[1] if len(sys.argv) > 1 else "find me a 1 bedroom off campus near UC Berkeley under 3500, no SSN"
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        desk = dict(viewport={"width": 1440, "height": 900}, record_video_dir=str(OUT / "raw"), record_video_size={"width": 1440, "height": 900})
        mc = await (await browser.new_context(**desk)).new_page()
        fl = await (await browser.new_context(**desk)).new_page()
        await mc.goto(HUB + "/")
        await fl.goto(HUB + "/flow")
        await asyncio.sleep(2)
        env = {**os.environ, "DEMO_MODE": "1", "MOCK_CALLS": "1", "AUTO_APPROVE": "1", "PUBLIC_URL": HUB, "VIBE_WAIT_SECONDS": "300", "BROWSER_USE_API_KEY": ""}
        log = open(OUT / "run.log", "w")
        sim = subprocess.Popen([sys.executable, "-m", "scripts.simulate", ask], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        t0 = time.time()
        deck = None
        while time.time() - t0 < 240 and not deck:
            m = re.search(r"/vibe/([a-f0-9]{10})", (OUT / "run.log").read_text(errors="ignore"))
            deck = m.group(1) if m else None
            await asyncio.sleep(1)
        print(f"deck {deck} at {time.time() - t0:.0f}s")
        if deck:
            phone = await (await browser.new_context(viewport={"width": 390, "height": 844}, record_video_dir=str(OUT / "raw"),
                                                      record_video_size={"width": 390, "height": 844})).new_page()
            await phone.goto(f"{HUB}/vibe/{deck}")
            await asyncio.sleep(2.5)
            cards = len((httpx.get(f"{HUB}/api/vibe/{deck}").json()).get("cards", []))
            for n in range(cards):
                await asyncio.sleep(1.6)
                await phone.keyboard.press("ArrowRight" if n % 2 == 0 else "ArrowLeft")
            await asyncio.sleep(3)
            await phone.context.close()
        while time.time() - t0 < 420 and "Held at" not in (OUT / "run.log").read_text(errors="ignore"):
            await asyncio.sleep(1)
        print(f"held at {time.time() - t0:.0f}s")
        await asyncio.sleep(8)
        sim.terminate()
        await mc.context.close()
        await fl.context.close()
        await browser.close()

    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    raw = sorted((OUT / "raw").glob("*.webm"), key=lambda f: f.stat().st_mtime)
    for name, f in zip(["mission_control", "flow", "swipe"], raw):
        dest = OUT / f"{name}.mp4"
        subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(f), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(dest)], check=True)
        print("saved", dest)


if __name__ == "__main__":
    asyncio.run(main())
