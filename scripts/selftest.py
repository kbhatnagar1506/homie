"""Check every real integration Homie depends on, end to end.

    python -m scripts.selftest
"""

import agents  # noqa: F401  (SSL certs first)
import asyncio
import time

from homie import mapi
from homie.llm import complete_text, parse_intent
from homie.places import search_apartments
from homie.schedule import human, next_open
from homie.screenshots import read_listing

results: list[tuple[str, bool, str]] = []


async def check(name: str, coro):
    t = time.time()
    try:
        ok, detail = await coro
    except Exception as e:
        ok, detail = False, repr(e)[:160]
    results.append((name, ok, f"{detail} ({time.time() - t:.1f}s)"))


async def gemini():
    reply = await complete_text("Reply with exactly: ok", [{"role": "user", "content": "ping"}])
    return "ok" in reply.lower(), reply[:40]


async def intent():
    i = await parse_intent("moving to downtown Atlanta Aug 20, two bedrooms under $2,500, no SSN")
    return i["intent"] == "search" and i.get("beds") == 2 and i.get("max_rent") == 2500, str({k: i.get(k) for k in ("intent", "beds", "max_rent", "area")})


async def places():
    found = await search_apartments("downtown Atlanta, GA", limit=5)
    with_phone = [b for b in found if b.get("phone")]
    return len(with_phone) >= 3, f"{len(found)} buildings, e.g. {found[0]['name']} {found[0].get('phone_display')}"


async def website():
    found = await search_apartments("downtown Atlanta, GA", limit=5)
    site = next(b["website"] for b in found if b.get("website"))
    page = await read_listing(site)
    return len(page["text"]) > 200 and bool(page["shot"]), f"{len(page['text'])} chars from {site[:40]}"


async def memory_roundtrip():
    marker = f"selftest-{int(time.time())}"
    await mapi.remember(f"They ran a Homie self test with marker {marker}.", tags=["selftest"])
    hits = await mapi.recall(f"self test marker {marker}", limit=3)
    return any(marker in h["content"] for h in hits), f"{len(hits)} hits"


async def hours():
    at = next_open(["Monday: 10:00 AM – 6:00 PM", "Sunday: 1:00 – 5:00 PM", "Saturday: Closed"])
    return at.hour in (10, 13), human(at)


async def main():
    await asyncio.gather(check("Gemini on Vertex", gemini()), check("Intent parsing", intent()),
                         check("Google Places (real buildings)", places()), check("Mapi memory write+search", memory_roundtrip()),
                         check("Office hours", hours()))
    await check("Real website read + screenshot", website())
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} passed")


if __name__ == "__main__":
    asyncio.run(main())
