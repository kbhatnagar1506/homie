"""Papers opens the building's portal, waits for you to sign in yourself, then continues the application.

    python -m scripts.continue_application the_durant
"""

import agents  # noqa: F401
import asyncio
import sys

from homie import apply, cache
from homie.config import env


async def main() -> None:
    bid = sys.argv[1] if len(sys.argv) > 1 else "the_durant"
    facts = (cache.get("scout", bid) or {}).get("facts") or {}
    name = (cache.get("places", "UC Berkeley off campus, Berkeley, CA") and next(
        (b["name"] for b in cache.get("places", "UC Berkeley off campus, Berkeley, CA") if b["id"] == bid), None)) or bid.replace("_", " ").title()
    first, _, last = env("APPLICANT_NAME", "Krishna Bhatnagar").partition(" ")
    out = await apply.continue_after_login({"name": name}, facts.get("application_url") or "", "August 20", 1, first, last, env("APPLICANT_EMAIL", ""))
    print("\nSIGN IN HERE (interactive):", out.get("live_url") or "-")
    print("Watch / share:", out.get("share_url") or out.get("error"))
    await asyncio.sleep(600)  # keep the watcher alive so mission control gets the final report


if __name__ == "__main__":
    asyncio.run(main())
