import os

from crewai import LLM
from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("LEO_MODEL", "anthropic/claude-haiku-5-5")
MEMORY_FILE = os.getenv("LEO_MEMORY_FILE", ".leo_memory.json")
AGENT_TIMEOUT_SECONDS = int(os.getenv("LEO_AGENT_TIMEOUT", "180"))
MAX_RETEACH_ROUNDS = int(os.getenv("LEO_MAX_RETEACH_ROUNDS", "2"))
PASS_MARK = float(os.getenv("LEO_PASS_MARK", "0.7"))


def check_api_key():
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is empty. Open the .env file, paste your key after "
            "ANTHROPIC_API_KEY= and run again."
        )


def build_llm():
    return LLM(model=MODEL, max_tokens=4096)
