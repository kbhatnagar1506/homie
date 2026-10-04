"""Homie Pics' eyes: open a page in headless Chromium and screenshot it."""

import logging
import uuid

from homie.config import ROOT

log = logging.getLogger(__name__)
SHOTS = ROOT / "data" / "shots"


async def screenshot(url: str, full_page: bool = False, width: int = 1280, height: int = 960) -> str | None:
    """Returns the saved file name under data/shots, or None if the page could not be captured."""
    from playwright.async_api import async_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.png"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(SHOTS / name), full_page=full_page)
            await browser.close()
        return name
    except Exception as e:
        log.warning("screenshot of %s failed: %s", url, e)
        return None
