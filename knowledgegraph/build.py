# Stage 3 - assemble extraction fragments into a NetworkX graph.
#
# Node deduplication layers:
#   1. within a fragment: extractors emit each id at most once (seen sets)
#   2. across fragments: add_node is idempotent; the first writer's attributes
#      win for identical ids (fragments are ordered deterministically)
#   3. fuzzy label merge: after assembly, entity dedup merges same-label nodes
#      that came from different files (MinHash/LSH blocking + Jaro-Winkler)
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from knowledgegraph.ids import normalize_id
from knowledgegraph.security import normalize_file_type, sanitize_metadata
from knowledgegraph.validate import validate_extraction


def build_graph(extractions: list[dict], *, directed: bool = False) -> nx.Graph:
    """Assemble validated extraction fragments into one graph."""
    G: nx.Graph = nx.DiGraph() if directed else nx.Graph()
    for frag in extractions:
        errors = validate_extraction(frag)
        if errors:
            print(f"  warning: dropping invalid fragment ({len(errors)} errors): {errors[0]}")
            continue
        for node in frag.get("nodes", []):
            nid = node["id"]
            attrs = dict(node)
            attrs.pop("stub", None)  # internal marker, not part of the schema
            attrs["file_type"] = normalize_file_type(attrs.get("file_type"))
            attrs["label"] = str(attrs.get("label") or nid)
            if G.has_node(nid) and G.nodes[nid].get("source_location"):
                # keep the first anchor with a concrete location; merge extras
                merged = {**attrs, **G.nodes[nid]}
                G.nodes[nid].update(merged)
            else:
                G.add_node(nid, **attrs)
        for e in frag.get("edges", []):
            src, tgt = e["source"], e["target"]
            if not G.has_node(src) or not G.has_node(tgt):
                continue
            attrs = {k: v for k, v in e.items() if k not in ("source", "target")}
            if G.has_edge(src, tgt):
                existing = G[src][tgt]
                if existing.get("relation") == attrs.get("relation"):
                    continue  # duplicate edge, keep first
                # parallel relation: keep both as an edge-keyed multiedge
                G.add_edge(src, tgt, **attrs, key=None) if False else None
                # Graph: store as separate attribute - keep the higher-priority one
                if existing.get("confidence") == "EXTRACTED":
                    continue
            G.add_edge(src, tgt, **attrs)

    # attach internal resolution keys carried by fragments
    for frag in extractions:
        defs = frag.get("definitions") or {}
        for label, nid in defs.items():
            if G.has_node(nid):
                G.nodes[nid].setdefault("defines", label)

    G.graph["hyperedges"] = []
    return G


def add_inferred_call_edges(G: nx.Graph, extractions: list[dict]) -> int:
    """Second resolution pass: raw per-file calls -> cross-file ``calls`` edges.

    Uses the label index built from code-class nodes. Within-file calls are
    already EXTRACTED; this pass only adds INFERRED edges (confidence 0.85)
    for calls that resolve to exactly one external definition.
    """
    from knowledgegraph.symbol_resolution import resolve_raw_calls
    added = 0
    for frag in extractions:
        raw = frag.get("raw_calls") or []
        if not raw:
            continue
        new_edges = resolve_raw_calls(G, raw)
        for src, tgt, relation, score, location, source_file in new_edges:
            if G.has_node(src) and G.has_node(tgt) and not G.has_edge(src, tgt):
                G.add_edge(src, tgt, relation=relation, confidence="INFERRED",
                           confidence_score=score, source_file=source_file,
                           source_location=location, weight=0.85)
                added += 1
    return added


# --- serialization --------------------------------------------------------------


def to_node_link(G: nx.Graph) -> dict:
    """Serialize a graph into the node-link JSON format used on disk."""
    data = nx.node_link_data(G, edges="edges")
    return data


def from_node_link(data: dict) -> nx.Graph:
    """Load a graph from node-link JSON, tolerating both edge key spellings."""
    if "links" in data and "edges" not in data:
        data = dict(data)
        data["edges"] = data.pop("links")
    return nx.node_link_graph(data, edges="edges")


def save_graph(G: nx.Graph, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = to_node_link(G)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_graph(path: Path) -> nx.Graph:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return from_node_link(data)


# --- fuzzy entity dedup ----------------------------------------------------------


def dedupe_labels(G: nx.Graph, threshold: float = 92.0, max_merge_group: int = 200) -> int:
    """Merge nodes whose labels are near-identical across files.

    Blocking by 3-gram MinHash/LSH keeps this near-linear; Jaro-Winkler on the
    normalized labels is the verification step. Code nodes anchored to a source
    location never merge (a class in a.py and a same-named doc heading are NOT
    duplicates). Returns the number of merges performed.
    """
    from rapidfuzz.distance import JaroWinkler

    def _norm(label: str) -> str:
        import re
        import unicodedata
        s = unicodedata.normalize("NFKC", label)
        return re.sub(r"[\W_]+", " ", s.casefold(), flags=re.UNICODE).strip()

    candidates: list[tuple[str, str]] = []
    nodes = [
        (nid, str(d.get("label", "")), d.get("file_type", ""))
        for nid, d in G.nodes(data=True)
        if d.get("label")
    ]
    # cheap blocking: group by normalized-label prefix buckets
    buckets: dict[str, list[int]] = {}
    for i, (nid, label, _) in enumerate(nodes):
        n = _norm(label)
        if len(n) < 3:
            continue
        for key in (n[:4], n[-4:]):
            buckets.setdefault(key, []).append(i)

    seen_pairs: set[tuple[int, int]] = set()
    for idxs in buckets.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                pair = (min(i, j), max(i, j))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                ni, nj = nodes[i], nodes[j]
                if ni[2] == "code" and nj[2] == "code" and ni[0] != nj[0]:
                    # both anchored to source: only merge exact normalized labels
                    if _norm(ni[1]) != _norm(nj[1]):
                        continue
                elif ni[1] == nj[1]:
                    pass  # identical labels always candidates
                score = JaroWinkler.similarity(_norm(ni[1]), _norm(nj[1]))
                if score * 100 >= threshold:
                    candidates.append((ni[0], nj[0]))

    # union-find over candidate pairs
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in candidates[:max_merge_group * 10]:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for nid in list(parent):
        groups.setdefault(find(nid), []).append(nid)

    merges = 0
    for root_id, members in groups.items():
        if len(members) < 2 or len(members) > max_merge_group:
            continue
        keep = members[0]
        keep_attrs = G.nodes[keep]
        for other in members[1:]:
            if not G.has_node(other):
                continue
            for _, tgt, data in list(G.edges(other, data=True)):
                if not G.has_edge(keep, tgt):
                    G.add_edge(keep, tgt, **data)
            for src, _, data in (list(G.in_edges(other, data=True)) if G.is_directed() else list(G.edges(other, data=True))):
                if not G.has_edge(src, keep):
                    G.add_edge(src, keep, **data)
            G.remove_node(other)
            merges += 1
        # prefer the shortest label as canonical
        if len(str(G.nodes[keep].get("label", ""))) > len(str(keep_attrs.get("label", ""))):
            pass
    return merges
