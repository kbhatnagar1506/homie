"""Run Browser Use against Homie's own sandbox portal: fills everything and clicks Create Account, live.

    python -m scripts.sandbox_signup "The Durant"

The sandbox stores nothing and creates no account anywhere. Watch it in mission control or the printed link.
"""

import agents  # noqa: F401
import asyncio
import secrets
import sys
from urllib.parse import quote

from homie import apply
from homie.config import env

TASK = """Go to {url}. Fill in the sign-up form: first name {first}, last name {last}, email {email}, phone {phone},
password and confirm password both {pw}. Tick the terms checkbox, click "Create Account", and confirm the "Account created" screen appears."""


async def main() -> None:
    building = sys.argv[1] if len(sys.argv) > 1 else "The Durant"
    base = env("SANDBOX_URL", env("PUBLIC_URL"))
    url = f"{base}/portal?b={quote(building)}"
    first, _, last = env("APPLICANT_NAME", "Krishna Bhatnagar").partition(" ")
    task = TASK.format(url=url, first=first, last=last, email=env("APPLICANT_EMAIL", "krishna@vfcloans.com"),
                       phone=env("SANDBOX_PHONE", "4045550123"), pw="Homie-" + secrets.token_hex(4))
    out = await apply.start({"name": building}, url, "", None, first, last, env("APPLICANT_EMAIL", ""), task=task)
    print("live:", out.get("share_url") or out.get("error"))
    await asyncio.sleep(90)


if __name__ == "__main__":
    asyncio.run(main())
