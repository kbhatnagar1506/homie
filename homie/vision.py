"""Gemini looks at a renter's photo the way a maintenance tech would. Light module: safe to import anywhere."""

import base64
import io

from PIL import Image

from homie.config import env


def shrink(jpeg: bytes, side: int = 512) -> bytes:
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    im.thumbnail((side, side))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82)
    return out.getvalue()


async def describe_photo(jpeg: bytes, context: str) -> str:
    from homie.llm import _client

    client = _client()
    if not client:
        return ""
    b64 = base64.b64encode(shrink(jpeg)).decode()
    r = await client.chat.completions.create(model=env("VISION_MODEL", "google/gemini-2.5-flash-lite"), messages=[{"role": "user", "content": [
        {"type": "text", "text": "You're a building maintenance tech looking at a renter's video-call camera. In one or two short "
                                 "sentences: what appliance or fixture is this, what looks wrong, and anything worth noting for the "
                                 f"repair person. If you can't see the problem clearly, say what you'd need to see. Context: {context}"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}], temperature=0.2)
    return (r.choices[0].message.content or "").strip()
