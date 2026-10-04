"""Generate illustrative listing photos for the demo buildings (Gemini image on Vertex).

    python -m scripts.make_listing_photos
"""

import asyncio
import base64
import os

import google.auth
import google.auth.transport.requests
import httpx

from homie.config import ROOT, load_buildings

OUT = ROOT / "hub" / "static" / "photos"
SHOTS = {
    "exterior": "exterior of {style} apartment building in Ann Arbor, Michigan, trees, golden hour, real estate photography",
    "living": "living room of a one-bedroom apartment in a {style} building, natural light, staged, real estate photography",
    "bedroom": "bedroom of a one-bedroom apartment in a {style} building, clean, bright, real estate photography",
    "kitchen": "kitchen of a one-bedroom apartment in a {style} building, stainless appliances, real estate photography",
}
STYLES = {
    "maple_court": "a cozy red-brick three-story",
    "arbor_lofts": "a modern industrial loft",
    "kerrytown": "a historic converted brick",
    "state_street": "a new glass-and-steel student",
}


async def gen(client: httpx.AsyncClient, token: str, project: str, prompt: str) -> bytes:
    url = f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/gemini-2.5-flash-image:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
    r = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=180)
    r.raise_for_status()
    part = next(p for p in r.json()["candidates"][0]["content"]["parts"] if "inlineData" in p)
    return base64.b64decode(part["inlineData"]["data"])


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient() as client:
        async def one(bid: str, shot: str, template: str):
            path = OUT / f"{bid}_{shot}.jpg"
            if path.exists():
                return
            async with sem:
                path.write_bytes(await gen(client, creds.token, project, template.format(style=STYLES[bid])))
                print("saved", path.name)
        await asyncio.gather(*(one(b["id"], s, t) for b in load_buildings() for s, t in SHOTS.items()))


if __name__ == "__main__":
    asyncio.run(main())
