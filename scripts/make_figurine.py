"""Generate a Pixar-style figurine and its mouth-shape set (Gemini image on Vertex) for live lip sync.

    python -m scripts.make_figurine fix
"""

import asyncio
import base64
import sys
from io import BytesIO

import google.auth
import google.auth.transport.requests
import httpx
from PIL import Image

from homie.config import ROOT

OUT = ROOT / "hub" / "static" / "figurines"
STYLE = ("Glossy 3D animated-film style figurine portrait, Pixar-like, soft studio lighting, smooth skin, big friendly expressive "
         "eyes, chest-up, facing the camera, centered, plain soft {bg} background, no text, 1:1 square. ")
CHARACTERS = {
    "fix": ("mint green", "A cheerful young maintenance technician with short curly dark hair, light stubble, a navy work cap worn "
                          "backwards, a navy work jacket over a white t-shirt, a small wrench clipped to the pocket. Mouth gently closed in a friendly smile."),
    "homie": ("warm peach", "A warm, friendly young South Asian man with neat dark wavy hair, thin round glasses, a cozy orange hoodie. "
                            "Mouth gently closed in a friendly smile."),
}
MOUTHS = {
    "aa": "mouth wide open as if saying 'ah', jaw dropped, top teeth and tongue visible",
    "E": "mouth open and stretched wide as if saying 'ee', teeth visible in a wide smile",
    "O": "lips rounded into an open 'oh' shape",
    "U": "lips pushed forward into a small round 'oo' pucker",
    "MBP": "lips pressed firmly together as if saying 'mm'",
    "FV": "top teeth resting on the lower lip as if saying 'f'",
    "blink": "eyes fully closed in a natural blink, mouth gently closed in a friendly smile",
}


def endpoint(project: str) -> str:
    return f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/gemini-2.5-flash-image:generateContent"


async def call(client, token, project, parts) -> bytes:
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}}
    for attempt in range(3):
        r = await client.post(endpoint(project), headers={"Authorization": f"Bearer {token}"}, json=body, timeout=180)
        if r.status_code == 200:
            part = next((p for p in r.json()["candidates"][0]["content"]["parts"] if "inlineData" in p), None)
            if part:
                return base64.b64decode(part["inlineData"]["data"])
        await asyncio.sleep(2)
    raise RuntimeError(f"image generation failed: {r.status_code} {r.text[:200]}")


def save(data: bytes, path):
    im = Image.open(BytesIO(data)).convert("RGB").resize((768, 768), Image.LANCZOS)
    im.save(path, "JPEG", quality=90)


async def main(role: str):
    bg, who = CHARACTERS[role]
    folder = OUT / role
    folder.mkdir(parents=True, exist_ok=True)
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    async with httpx.AsyncClient() as client:
        base_path = folder / "rest.jpg"
        if not base_path.exists():
            save(await call(client, creds.token, project, [{"text": STYLE.format(bg=bg) + who}]), base_path)
            print("rest")
        base = base64.b64encode(base_path.read_bytes()).decode()
        sem = asyncio.Semaphore(4)

        async def mouth(name, desc):
            path = folder / f"{name}.jpg"
            if path.exists():
                return
            async with sem:
                prompt = (f"Edit this exact image. Keep the character, face, hair, clothes, lighting, framing, camera angle and background "
                          f"identical. Change ONLY the mouth{' and eyes' if name == 'blink' else ''}: {desc}.")
                save(await call(client, creds.token, project, [{"inlineData": {"mimeType": "image/jpeg", "data": base}}, {"text": prompt}]), path)
                print(name)

        await asyncio.gather(*(mouth(n, d) for n, d in MOUTHS.items()))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fix"))
