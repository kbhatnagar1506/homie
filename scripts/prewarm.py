"""Pre-warm the demo cache: real Places results + Scout reads (facts and screenshots) for the demo cities.

    python -m scripts.prewarm
"""

import agents  # noqa: F401
import asyncio

from homie import cache
from homie.places import search_apartments
from homie.scout import crawl

AREAS = ["UC Berkeley off campus, Berkeley, CA", "San Francisco, CA", "near Stanford University, Palo Alto, CA",
         "downtown Atlanta, GA", "near Georgia Tech, Atlanta, GA", "near University of Michigan, Ann Arbor, MI"]
PER_AREA = 6


async def main() -> None:
    gate = asyncio.Semaphore(6)

    async def scout(b: dict) -> str:
        if cache.get("scout", b["id"]) or not b.get("website"):
            return "cached" if b.get("website") else "no site"
        async with gate:
            try:
                out = await asyncio.wait_for(crawl(b["website"], max_pages=3), 90)
            except Exception as e:
                return f"failed {e!r}"[:60]
        if out.get("pages"):
            cache.put("scout", b["id"], out)
        return f"{out.get('pages')} pages, {len(out.get('shots') or [])} shots"

    for area in AREAS:
        try:
            found = (await search_apartments(area, limit=PER_AREA))[:PER_AREA]
        except Exception as e:
            print(f"{area}: Places failed {e}")
            continue
        results = await asyncio.gather(*(scout(b) for b in found))
        print(f"\n{area}: {len(found)} buildings")
        for b, r in zip(found, results):
            print(f"  {b['name'][:36]:36} {r}")


if __name__ == "__main__":
    asyncio.run(main())
