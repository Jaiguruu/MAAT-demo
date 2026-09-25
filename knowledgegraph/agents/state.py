"""Shared state for the multi-agent graph.

One ``AgentState`` flows through every node. Each node reads what it needs and
appends its contribution - the state is the only channel between agents, which
keeps the graph deterministic and easy to test (nodes are plain functions of
state -> state).
"""
from __future__ import annotations

import json
from typing import Annotated, TypedDict


def _merge_list(current: list, update: list) -> list:
    """Reducer: append without duplicates, preserving order.

    Keys are built from JSON-serialized values so dicts (evidence payloads)
    can participate in dedup without being hashed directly.
    """
    def _key(item):
        if isinstance(item, str):
            return item
        try:
            return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return repr(item)

    seen = {_key(i) for i in current}
    out = list(current)
    for item in update:
        k = _key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


class AgentState(TypedDict, total=False):
    """The single state object passed between agent nodes."""

    question: str
    graph_path: str
    plan: list[str]                     # supervisor's routing decision
    evidence: Annotated[list[dict], _merge_list]   # tool outputs, deduped
    trace: Annotated[list[str], _merge_list]       # human-readable agent trail
    needs_more: bool                    # workers may request a second hop
    answer: str
    error: str
