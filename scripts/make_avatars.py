"""Generate one themed set of cute avatars for the Homie team (Gemini image on Vertex) and set them on Relay.

    python -m scripts.make_avatars          # generate + upload
    python -m scripts.make_avatars --only-generate
"""

import asyncio
import base64
import sys

import google.auth
import google.auth.transport.requests
import httpx
from relaymessenger import Relay

from homie.config import ROOT, env
from relay_app.team import TEAM

OUT = ROOT / "relay_app" / "avatars"
THEME = ("Cute 3D clay-style mascot, a tiny friendly house character with a little face (two dot eyes, rosy cheeks, small smile), "
         "soft pastel colors, studio lighting, centered, square, plain solid {bg} background, Pixar-like, no text, no letters. ")
ROLES = {
    "homie": ("warm orange", "the leader: a cozy house wearing a tiny backpack and holding a house key, waving"),
    "calls": ("sky blue", "the same little house character wearing a headset, holding a vintage phone, chatty and excited"),
    "papers": ("lavender", "the same little house character wearing round glasses, holding a clipboard with a passport on it"),
    "fix": ("mint green", "the same little house character wearing a yellow hard hat, holding a wrench"),
    "policy": ("butter yellow", "the same little house character wearing a tiny judge's wig, holding a small balance scale"),
    "pics": ("pink", "the same little house character holding a camera up to its face, taking a photo"),
    "memory": ("soft cyan", "the same little house character with a glowing pastel brain peeking out of its open roof, hugging a small notebook, thoughtful and kind"),
}


async def gen(client, token, project, prompt) -> bytes:
    url = f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/gemini-2.5-flash-image:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
    r = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=180)
    r.raise_for_status()
    part = next(p for p in r.json()["candidates"][0]["content"]["parts"] if "inlineData" in p)
    return base64.b64decode(part["inlineData"]["data"])


async def upload(role: str, data: bytes) -> None:
    token = env(TEAM[role].token_env) if role in TEAM else ""
    if not token:
        return
    relay = Relay(token)
    att = await relay.attachments.create(filename=f"{role}.png", content_type="image/png", size_bytes=len(data))
    await relay.attachments.upload(att, data)
    card = (await relay.contact_card.retrieve())["contact_cards"][0]
    await relay.contact_card.update(card["handle"], attachment_id=att["attachment_id"])
    print(f"{role}: avatar set on Relay")


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    async with httpx.AsyncClient() as client:
        async def one(role, bg, pose):
            data = await gen(client, creds.token, project, THEME.format(bg=bg) + pose)
            (OUT / f"{role}.png").write_bytes(data)
            print(f"{role}: generated")
            if "--only-generate" not in sys.argv:
                await upload(role, data)
        await asyncio.gather(*(one(r, bg, pose) for r, (bg, pose) in ROLES.items()))


if __name__ == "__main__":
    asyncio.run(main())
