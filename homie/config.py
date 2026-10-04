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
PUBLIC_URL = env("PUBLIC_URL", HUB_URL)  # where phones and Agentverse can load images from
MOCK_CALLS = env("MOCK_CALLS", "1") == "1"

LLM_PROVIDER = env("LLM_PROVIDER", "vertex")  # "vertex" (Gemini, service-account auth) or "openai" (any OpenAI-compatible API)
VERTEX_LOCATION = env("VERTEX_LOCATION", "us-central1")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.asi1.ai/v1")
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "google/gemini-2.5-flash" if LLM_PROVIDER == "vertex" else "asi1-mini")

ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = env("ELEVENLABS_AGENT_ID")
ELEVENLABS_PHONE_NUMBER_ID = env("ELEVENLABS_PHONE_NUMBER_ID")


def load_buildings() -> list[dict]:
    buildings = json.loads((ROOT / "data" / "buildings.json").read_text())
    for b in buildings:
        b["phone"] = env(f"PHONE_{b['id'].upper()}")
        b["relay_handle"] = env(f"RELAY_OFFICE_{b['id'].upper()}")  # a teammate's Relay handle playing this office
    return buildings


# Every Homie agent is public on Agentverse and discoverable from ASI:One.
PUBLIC_METADATA = {"is_public": "True", "categories": ["housing", "real-estate", "students"],
                   "tags": ["apartment", "rent", "international-student", "no-ssn", "leasing", "innovationlab", "hackathon"]}
