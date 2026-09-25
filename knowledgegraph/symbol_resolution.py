"""Deterministic symbol indexing and conservative cross-file call resolution."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalise_callable_label(label: str) -> str:
    """Normalize a node label into the key used for call resolution."""
    return label.strip().strip("()").lstrip(".").lower()


def node_is_resolvable_symbol(node: dict[str, Any]) -> bool:
    """True when a node can serve as a call target.

    Only code-class nodes participate: a markdown heading that happens to share
    a name with a function must never receive a raw code call edge.
    """
    if node.get("file_type") != "code":
        return False
    label = str(node.get("label", "")).strip()
    if not label:
        return False
    if label.endswith((".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs")):
        return False
    return bool(normalise_callable_label(label))


def build_label_index(G) -> dict[str, list[str]]:
    """label-key -> node ids, for conservative cross-file resolution."""
    index: dict[str, list[str]] = {}
    for nid, data in G.nodes(data=True):
        if not node_is_resolvable_symbol(data):
            continue
        key = normalise_callable_label(str(data.get("label", "")))
        if key:
            index.setdefault(key, []).append(str(nid))
    return index


def existing_edge_pairs(G) -> set[tuple[str, str, str]]:
    """All (source, target, relation) triples already in the graph."""
    return {(str(u), str(v), str(d.get("relation", ""))) for u, v, d in G.edges(data=True)}


def _module_stem(module_name: str | None) -> str:
    if not module_name:
        return ""
    return module_name.strip(".").split(".")[-1]


def resolve_raw_calls(G, raw_calls: list[dict]) -> list[tuple[str, str, str, float, str | None, str]]:
    """Resolve raw per-file calls against the label index.

    Returns tuples of (source, target, relation, confidence_score, location,
    source_file). Only unambiguous targets (exactly one candidate) produce an
    edge; ambiguous names are skipped rather than guessed at.
    """
    index = build_label_index(G)
    existing = existing_edge_pairs(G)
    results: list[tuple[str, str, str, float, str | None, str]] = []

    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", "")).strip()
        caller = str(call.get("caller", ""))
        if not name or not caller:
            continue
        key = normalise_callable_label(name)
        candidates = index.get(key, [])
        # function labels are stored with "()" decoration; index keys are bare
        if not candidates:
            candidates = index.get(name.lower(), [])
        if len(candidates) != 1:
            continue
        target = candidates[0]
        if target == caller:
            continue
        if (caller, target, "calls") in existing:
            continue
        results.append((
            caller, target, "calls", 0.85,
            call.get("location"), str(call.get("source_file", "")),
        ))
    return results
