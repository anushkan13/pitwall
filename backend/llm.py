"""Model setup shared by the agent team: gpt-6-luna through Portkey.

Same .env handling as hw5: the key is looked for in the project root first,
which is where a fresh clone puts it after copying .env.example, and then one
level up, which is where this course's shared key lives.

No agents are defined yet. When the team is added, each PydanticAI Agent gets
`build_model()` and `MODEL_SETTINGS`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_CANDIDATES = (PROJECT_DIR / ".env", PROJECT_DIR.parent / ".env")


def env_file() -> Path:
    """The .env actually in use, preferring one inside the project."""
    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            return candidate
    return ENV_CANDIDATES[0]


load_dotenv(env_file())

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-6-luna")

# gpt-6-luna rejects function tools over /v1/chat/completions unless reasoning
# effort is switched off, so every agent runs with it set to "none".
MODEL_SETTINGS = OpenAIChatModelSettings(openai_reasoning_effort="none")


def has_key() -> bool:
    return bool(os.getenv("PORTKEY_API_KEY"))


def make_client() -> AsyncOpenAI:
    """OpenAI client pointed at Portkey, keyed from .env."""
    key = os.getenv("PORTKEY_API_KEY")
    if not key:
        raise RuntimeError(
            "PORTKEY_API_KEY is not set. Copy .env.example to .env in the project "
            f"root and put your key in it (looked in: "
            f"{', '.join(str(c) for c in ENV_CANDIDATES)})."
        )
    return AsyncOpenAI(
        api_key=key,
        base_url="https://api.portkey.ai/v1",
        default_headers={"x-portkey-api-key": key},
    )


def build_model() -> OpenAIChatModel:
    return OpenAIChatModel(MODEL_NAME, provider=OpenAIProvider(openai_client=make_client()))
