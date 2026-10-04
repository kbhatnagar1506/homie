"""Create the five Homie contacts on Relay and save their tokens to .env.

Sign in first (once, in your own terminal):   npx relaymessenger@latest login
Then:                                          python -m scripts.relay_setup

Tokens are written straight into .env and never printed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from homie.config import ROOT, env
from relay_app.team import TEAM

ASSETS = ROOT / "relay_app" / "avatars"
ENV = ROOT / ".env"
CLI = ["npx", "-y", "relaymessenger@latest"]
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"


def avatar(persona) -> Path:
    ASSETS.mkdir(exist_ok=True)
    path = ASSETS / f"{persona.role}.png"
    if path.exists():  # keep the themed avatar from scripts.make_avatars
        return path
    size = 1024
    img = Image.new("RGB", (size, size))
    top, bottom = persona.color, tuple(max(0, c - 70) for c in persona.color)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        draw.line([(0, y), (size, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    try:
        emoji = Image.new("RGBA", (200, 200))
        ImageDraw.Draw(emoji).text((20, 20), persona.emoji, font=ImageFont.truetype(EMOJI_FONT, 160), embedded_color=True)
        emoji = emoji.crop(emoji.getbbox()).resize((560, 560), Image.LANCZOS)
        img.paste(emoji, ((size - 560) // 2, (size - 560) // 2), emoji)
    except OSError:
        draw.text((size // 3, size // 3), persona.name[0], fill="white", font=ImageFont.load_default(size=400))
    img.save(path)
    return path


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(CLI + args, capture_output=True, text=True, cwd=ROOT)


def save_token(name: str, token: str) -> None:
    text = ENV.read_text() if ENV.exists() else ""
    line = f"{name}={token}"
    text = re.sub(rf"^{name}=.*$", line, text, flags=re.M) if re.search(rf"^{name}=", text, re.M) else text.rstrip("\n") + f"\n{line}\n"
    ENV.write_text(text)
    ENV.chmod(0o600)


def main() -> None:
    for role, p in TEAM.items():
        if env(p.token_env):
            print(f"{p.name}: already set up, skipping")
            continue
        image = avatar(p)
        base = ["agents", "create", "--name", p.name, "--subtitle", p.subtitle, "--description", p.description, "--image", str(image), "--json"]
        result = run(base + ["--handle", p.handle])
        if result.returncode != 0:  # handle taken or not allowed: let Relay pick one
            result = run(base)
        if result.returncode != 0:
            sys.exit(f"Could not create {p.name}:\n{result.stderr or result.stdout}\nDid you run `npx relaymessenger@latest login`?")
        try:
            info = json.loads(result.stdout[result.stdout.index("{"):])
        except ValueError:
            info = {}
        token = run(["auth", "token"]).stdout.strip().splitlines()[-1].strip()
        if not token or " " in token:
            sys.exit(f"Created {p.name} but could not read its token. Run: npx relaymessenger@latest auth token")
        save_token(p.token_env, token)
        print(f"{p.name}: created ({info.get('handle') or info.get('agent', {}).get('handle', '')}), token saved to .env")
    print("\nDone. On your phone: open each Homie contact in Relay and tap Add, then run: python -m agents.run")


if __name__ == "__main__":
    main()
