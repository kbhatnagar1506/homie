"""Animate a figurine portrait into talking and listening loops with Veo on Vertex AI.

    python -m scripts.make_figurine_video fix
"""

import asyncio
import base64
import subprocess
import sys

import google.auth
import google.auth.transport.requests
import httpx

from homie.config import ROOT

MODELS = ["veo-3.0-fast-generate-001", "veo-3.1-fast-generate-001", "veo-2.0-generate-001"]
CLIPS = {
    "talking": ("The animated character talks warmly and naturally to the camera on a video call, mouth moving clearly as if speaking "
                "in a friendly, casual way, small natural head movements and nods, occasional blinks, light expressive eyebrows. "
                "The camera is completely still. Same lighting and background throughout. No text."),
    "listening": ("The animated character listens attentively to someone on a video call, mouth closed in a gentle friendly smile, "
                  "blinks naturally, small slow nods, slight head tilt of interest. The camera is completely still. Same lighting and "
                  "background throughout. No text."),
}


async def generate(client, token, project, model, image_b64, prompt) -> bytes:
    base = f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project}/locations/us-central1/publishers/google/models/{model}"
    H = {"Authorization": f"Bearer {token}"}
    body = {"instances": [{"prompt": prompt, "image": {"bytesBase64Encoded": image_b64, "mimeType": "image/jpeg"}}],
            "parameters": {"aspectRatio": "9:16", "durationSeconds": 8, "sampleCount": 1, "generateAudio": False, "resolution": "720p"}}
    r = await client.post(f"{base}:predictLongRunning", headers=H, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{model}: {r.status_code} {r.text[:300]}")
    op = r.json()["name"]
    for _ in range(80):
        await asyncio.sleep(6)
        p = await client.post(f"{base}:fetchPredictOperation", headers=H, json={"operationName": op}, timeout=60)
        d = p.json()
        if d.get("done"):
            if "error" in d:
                raise RuntimeError(f"{model}: {d['error']}")
            vids = d["response"].get("videos") or d["response"].get("generatedSamples") or []
            v = vids[0]
            data = v.get("bytesBase64Encoded") or (v.get("video") or {}).get("bytesBase64Encoded")
            return base64.b64decode(data)
    raise RuntimeError("Veo timed out")


def make_loop(src, dst):
    """Crossfade the clip's end into its start so it loops seamlessly, drop audio, web-friendly mp4."""
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                               capture_output=True, text=True).stdout or 8)
    fade = 0.6
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-i", src, "-filter_complex",
                    f"[0:v]trim=start={fade},setpts=PTS-STARTPTS[a];[1:v]trim=0:{fade},setpts=PTS-STARTPTS[b];"
                    f"[a][b]xfade=transition=fade:duration={fade}:offset={dur - 2 * fade}[v]",
                    "-map", "[v]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", dst], check=True)


async def main(role: str):
    folder = ROOT / "hub" / "static" / "figurines" / role
    image = base64.b64encode((folder / "portrait.jpg").read_bytes()).decode()
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    async with httpx.AsyncClient() as client:
        async def one(name, prompt):
            for model in MODELS:
                try:
                    raw = await generate(client, creds.token, project, model, image, prompt)
                    (folder / f"{name}_raw.mp4").write_bytes(raw)
                    make_loop(str(folder / f"{name}_raw.mp4"), str(folder / f"{name}.mp4"))
                    print(name, "done with", model)
                    return
                except Exception as e:
                    print(name, "failed:", str(e)[:200])
        await asyncio.gather(*(one(n, p) for n, p in CLIPS.items()))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fix"))
