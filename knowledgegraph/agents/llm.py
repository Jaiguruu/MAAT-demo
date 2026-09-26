"""OpenAI-compatible model wrapper used by every agent node.

Routes through ``knowledgegraph.config``: an ``OPENROUTER_API_KEY`` targets
OpenRouter's OpenAI-compatible endpoint, an ``OPENAI_API_KEY`` targets OpenAI
(or any explicit ``OPENAI_BASE_URL``). When no key is present the wrapper
reports itself unavailable so the agent graph falls back to the deterministic
extractive answer - the pipeline and tools always work offline.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from knowledgegraph import config
from knowledgegraph import metrics as kg_metrics


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


def chat_full(system: str, user: str, config_: LLMConfig | None = None,
              purpose: str = "answer"):
    """One completion call, returning ``(content, resp, meta)``.

    ``meta`` carries ``model`` and ``wall_ms`` so callers that need to tag the
    metrics purpose *after* seeing the response (the ReAct loop's finish call)
    can record it themselves via ``UsageLedger.record_llm``. Errors are
    recorded under the provisional ``purpose`` before re-raising.
    """
    client = _client()
    if client is None:
        raise RuntimeError(
            "no LLM API key configured (set OPENROUTER_API_KEY or OPENAI_API_KEY). "
            "The `kg query/path/explain/affected` tools work without any key."
        )
    cfg = config_ or LLMConfig()
    model = cfg.model or config.agent_model()
    ledger = kg_metrics.current_ledger()
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as e:
        if ledger:
            ledger.record_llm(purpose, model, wall_ms=(time.perf_counter() - t0) * 1000,
                              error=f"{type(e).__name__}: {e}")
        raise
    meta = {"model": model, "wall_ms": (time.perf_counter() - t0) * 1000}
    return (resp.choices[0].message.content or "", resp, meta)


def chat(system: str, user: str, config_: LLMConfig | None = None,
         purpose: str = "answer") -> str:
    """One completion call. Raises RuntimeError when no API key is set.

    When a metrics ledger is installed (bench runs), the call's tokens and
    wall time are recorded under ``purpose``.
    """
    content, resp, meta = chat_full(system, user, config_, purpose)
    ledger = kg_metrics.current_ledger()
    if ledger:
        ledger.record_llm(purpose, meta["model"], resp_usage=getattr(resp, "usage", None),
                          wall_ms=meta["wall_ms"])
    return content
