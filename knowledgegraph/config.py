"""Environment configuration.

Loads a project-local ``.env`` (if present) into ``os.environ`` without
overriding existing values, then exposes typed accessors. Only the agent and
semantic-extraction layers read these - the graph pipeline itself never needs
any key.
"""
from __future__ import annotations

import os
from pathlib import Path

_KNOWN_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "KG_SEMANTIC_MODEL",
    "KG_AGENT_MODEL",
)


def _load_dotenv() -> None:
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes.

    Existing environment variables always win over .env values.
    """
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if not candidate.is_file():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass
        break


_load_dotenv()


def api_key() -> str | None:
    """The API key to use: OpenRouter first, then a raw OpenAI key."""
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or None


def base_url() -> str | None:
    """Base URL for the OpenAI-compatible endpoint.

    Defaults to OpenRouter when an OpenRouter key is set, otherwise the
    official OpenAI endpoint (or an explicit OPENAI_BASE_URL override).
    """
    explicit = os.environ.get("OPENAI_BASE_URL")
    if explicit:
        return explicit
    if os.environ.get("OPENROUTER_API_KEY"):
        return "https://openrouter.ai/api/v1"
    return None


def semantic_model() -> str:
    return os.environ.get("KG_SEMANTIC_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"


def agent_model() -> str:
    return os.environ.get("KG_AGENT_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"


def llm_ready() -> bool:
    return api_key() is not None
