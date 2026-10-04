"""Homie Scout: reads a building's whole website (floor plans, specials, fees, policies, how to apply), not just the homepage."""

import logging
import re
import uuid
from urllib.parse import urljoin, urlparse

from homie.llm import complete_json
from homie.screenshots import SHOTS

log = logging.getLogger("homie.scout")
KEYWORDS = re.compile(r"floor ?plans?|apartments?/|availability|pricing|rates|amenit|gallery|photos|faq|fees?|pet|parking|special|apply|lease|resident|policy|international", re.I)

FACTS = """You read the pages of an apartment building's website. Extract JSON with keys:
name, address, phone, office_hours (string), floor_plans (list of {name, beds (int, 0 studio), baths, sqft (string),
price (int, lowest monthly rent shown), per_bed (bool, true if priced per bed), availability (string)}), specials (string),
fees (object: application, admin, deposit, other, as strings), utilities (string), pet_policy (string), parking (string),
lease_terms (string), application_url, tour_url, international_or_no_ssn (anything about international students, SSN,
guarantors, guarantor services, or proof of income; "" if nothing), student_housing (bool), notes (one line).
Only use facts that appear in the text. Use null or "" when not stated. JSON only."""


async def crawl(url: str, max_pages: int = 8) -> dict:
    from playwright.async_api import async_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).netloc
    texts, shots, seen = [], [], set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 960})
        queue = [url]
        while queue and len(seen) < max_pages:
            target = queue.pop(0)
            if target in seen:
                continue
            seen.add(target)
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2200)
                body = await page.inner_text("body")
                texts.append(f"=== {target}\n{body[:7000]}")
                if len(shots) < 5 and (target == url or re.search(r"floor|apartments?/|availability|pricing|gallery|photos|amenit|tour|interior", target, re.I)):
                    name = f"{uuid.uuid4().hex[:12]}.png"
                    await page.screenshot(path=str(SHOTS / name))
                    shots.append(name)
                if target == url:  # discover the useful pages from the homepage
                    links = await page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, e.innerText])")
                    for href, label in links:
                        href = urljoin(url, href).split("#")[0]
                        if urlparse(href).netloc.endswith(host.replace("www.", "")) and (KEYWORDS.search(href) or KEYWORDS.search(label or "")):
                            if href not in seen and href not in queue:
                                queue.append(href)
                        elif "prospectportal" in href or "/apply" in href.lower():
                            texts.append(f"=== application link: {href}")
            except Exception as e:
                log.warning("scout %s failed: %s", target, e)
        await browser.close()
    joined = "\n\n".join(texts)
    facts = await complete_json(FACTS, joined[:60000]) or {}
    _fill_prices(facts, joined)
    return {"facts": facts, "shots": shots, "pages": len(seen)}


WORDS = {"studio": 0, "one": 1, "two": 2, "three": 3, "four": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _fill_prices(facts: dict, text: str) -> None:
    """Deterministic backup: "One Bedroom ... From $1,735" lines on the page fill any price the model missed."""
    seen: dict[int, int] = {}
    for m in re.finditer(r"\b(studio|one|two|three|four|[1-4])[\s-]*(?:bed(?:room)?s?|br)\b[^$\n]{0,120}?\$\s?([\d,]{3,6})", text, re.I):
        beds, price = WORDS[m.group(1).lower()], int(m.group(2).replace(",", ""))
        if 300 < price < 10000:
            seen[beds] = min(price, seen.get(beds, price))
    plans = facts.setdefault("floor_plans", []) or []
    for beds, price in seen.items():
        priced = [p for p in plans if p.get("beds") == beds and p.get("price")]
        if not priced:
            open_ = next((p for p in plans if p.get("beds") == beds and "sold" not in str(p.get("availability", "")).lower()), None)
            if open_:
                open_["price"] = price
            else:
                plans.append({"name": f"{beds}-bed" if beds else "Studio", "beds": beds, "price": price, "per_bed": beds >= 3,
                              "availability": "from the website"})
    for p in plans:
        if p.get("beds") in (0, 1):
            p["per_bed"] = False
    facts["floor_plans"] = plans

