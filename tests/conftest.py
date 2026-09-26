"""Hermetic test suite: no test may reach a real LLM endpoint.

The repo ships a .env with a live key in dev environments; the agent layer
correctly picks it up (llm_available() -> True). Unit tests must be
deterministic and free, so every test gets the LLM disabled by default.

Tests that need a scripted LLM patch ``chat``/``chat_full`` themselves inside
the test body - their patch applies after this fixture's setup and wins for
the duration of the test.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_offline(monkeypatch):
    """Force every test offline: no key, no LLM, no network."""

    def _forbidden(*args, **kwargs):  # pragma: no cover - safety net
        raise AssertionError("real LLM call attempted in offline test")

    # no API key anywhere: config.api_key() -> None covers llm._client(),
    # semantic._call_llm(), and config.llm_ready() (pipeline semantic gate)
    monkeypatch.setattr("knowledgegraph.config.api_key", lambda: None)
    # agent layer reports itself unavailable -> keyword router + extractive synth
    monkeypatch.setattr("knowledgegraph.agents.llm.llm_available", lambda: False)
    # belt and braces: any direct chat call fails loudly, never hits network
    monkeypatch.setattr("knowledgegraph.agents.llm.chat_full", _forbidden)
    monkeypatch.setattr("knowledgegraph.agents.llm.chat", _forbidden)
