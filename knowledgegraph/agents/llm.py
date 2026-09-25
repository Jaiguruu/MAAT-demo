"""OpenAI-compatible model wrapper used by every agent node.

Routes through ``knowledgegraph.config``: an ``OPENROUTER_API_KEY`` targets
OpenRouter's OpenAI-compatible endpoint, an ``OPENAI_API_KEY`` targets OpenAI
(or any explicit ``OPENAI_BASE_URL``). When no key is present the wrapper
reports itself unavailable so the agent graph falls back to the deterministic
extractive answer - the pipeline and tools always work offline.
"""
from __future__ import annotations

from dataclasses import dataclass

from knowledgegraph import config


@dataclass
class LLMConfig:
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1200


def _client():
    try:
        from openai import OpenAI
    except ImportError:
        return None
    key = config.api_key()
    if not key:
        return None
    return OpenAI(api_key=key, base_url=config.base_url())


def llm_available() -> bool:
    return _client() is not None


def chat(system: str, user: str, config_: LLMConfig | None = None) -> str:
    """One completion call. Raises RuntimeError when no API key is set."""
    client = _client()
    if client is None:
        raise RuntimeError(
            "no LLM API key configured (set OPENROUTER_API_KEY or OPENAI_API_KEY). "
            "The `kg query/path/explain/affected` tools work without any key."
        )
    cfg = config_ or LLMConfig()
    model = cfg.model or config.agent_model()
    resp = client.chat.completions.create(
        model=model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content
    return content or ""
