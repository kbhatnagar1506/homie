"""Homie Pics' eyes: open a page in headless Chromium and screenshot it."""

import logging
import re
import uuid
from urllib.parse import urljoin

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
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)  # let images and hero sections paint; ad/tracker-heavy sites never go idle
            await page.screenshot(path=str(SHOTS / name), full_page=full_page)
            await browser.close()
        return name
    except Exception as e:
        log.warning("screenshot of %s failed: %s", url, e)
        return None


async def read_listing(url: str, max_chars: int = 14000) -> dict:
    """Open a building's website (and its floor-plans page if it has one); return visible text plus a screenshot."""
    from playwright.async_api import async_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.png"
    text = ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page(viewport={"width": 1280, "height": 960})
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            await page.screenshot(path=str(SHOTS / name))
            text = await page.inner_text("body")
            link = page.locator("a", has_text=re.compile(r"floor ?plans?|pricing|availability", re.I)).first
            if await link.count():
                href = await link.get_attribute("href")
                if href:
                    await page.goto(urljoin(url, href), wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2500)
                    text += "\n\n" + await page.inner_text("body")
            await browser.close()
    except Exception as e:
        log.warning("reading %s failed: %s", url, e)
    return {"text": text[:max_chars], "shot": name if (SHOTS / name).exists() else None}
