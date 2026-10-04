import json
import os
from pathlib import Path

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# python.org builds on macOS ship without CA certs; without this the Agentverse mailbox fails.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def seed(name: str) -> str:
    value = env(f"{name.upper()}_SEED")
    if not value:
        raise SystemExit(f"Set {name.upper()}_SEED in .env (any long random phrase).")
    return value


HUB_URL = env("HUB_URL", "http://localhost:8080")
MOCK_CALLS = env("MOCK_CALLS", "1") == "1"

LLM_BASE_URL = env("LLM_BASE_URL", "https://api.asi1.ai/v1")
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "asi1-mini")

ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = env("ELEVENLABS_AGENT_ID")
ELEVENLABS_PHONE_NUMBER_ID = env("ELEVENLABS_PHONE_NUMBER_ID")


def load_buildings() -> list[dict]:
    buildings = json.loads((ROOT / "data" / "buildings.json").read_text())
    for b in buildings:
        b["phone"] = env(f"PHONE_{b['id'].upper()}")
    return buildings
