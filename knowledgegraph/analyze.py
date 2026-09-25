"""Stage 5 - graph analysis: god nodes, surprising connections, suggested questions."""
from __future__ import annotations

from pathlib import Path

import networkx as nx

_BUILTIN_NOISE_LABELS = frozenset({
    "str", "int", "float", "bool", "bytes", "bytearray", "complex", "object",
    "True", "False", "Path", "Any", "Optional", "List", "Dict", "Set", "Tuple",
    "Union", "Callable", "Type", "ClassVar", "Final", "Literal", "Protocol",
    "os", "sys", "re", "json", "io", "abc", "typing",
})

_JSON_NOISE_LABELS = frozenset({
    "start", "end", "name", "id", "type", "properties", "value", "key",
    "data", "items", "title", "description", "version", "dependencies",
})

_LANG_FAMILY: dict[str, str] = {
    **{e: "python" for e in (".py", ".pyw")},
    **{e: "js" for e in (".js", ".jsx", ".mjs", ".ts", ".tsx", ".mts", ".cts")},
    **{e: "go" for e in (".go",)},
    **{e: "rust" for e in (".rs",)},
    **{e: "c" for e in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp")},
}


def _cross_language(src_a: str, src_b: str) -> bool:
    fam_a = _LANG_FAMILY.get(Path(src_a).suffix.lower())
    fam_b = _LANG_FAMILY.get(Path(src_b).suffix.lower())
    return bool(fam_a and fam_b and fam_a != fam_b)


def _is_file_node(G: nx.Graph, node_id: str) -> bool:
    """True for synthetic hub nodes: file anchors and method stubs."""
    attrs = G.nodes[node_id]
    label = attrs.get("label", "")
    if not label:
        return False
    source_file = attrs.get("source_file", "")
    if source_file and label == Path(source_file).name:
        return True
    if label.startswith(".") and label.endswith("()"):
        return True
    if label.endswith("()") and G.degree(node_id) <= 1:
        return True
    return False


def _is_concept_node(G: nx.Graph, node_id: str) -> bool:
    """True for LLM-injected semantic annotations (file_type == concept)."""
    return G.nodes[node_id].get("file_type", "") == "concept"


def _is_json_key_node(G: nx.Graph, node_id: str) -> bool:
    src = (G.nodes[node_id].get("source_file") or "").lower()
    if not src.endswith(".json"):
        return False
    return (G.nodes[node_id].get("label") or "").strip().lower() in _JSON_NOISE_LABELS


def god_nodes(G: nx.Graph, top_n: int = 10) -> list[dict]:
    """The top_n most-connected real entities - the core abstractions."""
    degree = dict(G.degree())
    result = []
    for node_id, deg in sorted(degree.items(), key=lambda x: x[1], reverse=True):
        if _is_file_node(G, node_id) or _is_concept_node(G, node_id) or _is_json_key_node(G, node_id):
            continue
        if G.nodes[node_id].get("label", "") in _BUILTIN_NOISE_LABELS:
            continue
        result.append({
            "id": node_id,
            "label": G.nodes[node_id].get("label", node_id),
            "degree": deg,
        })
        if len(result) >= top_n:
            break
    return result


def surprising_connections(
    G: nx.Graph,
    communities: dict[int, list[str]] | None = None,
    top_n: int = 5,
) -> list[dict]:
    """Connections that are not obvious from file layout.

    Multi-file corpora: cross-file edges, ranked AMBIGUOUS -> INFERRED ->
    EXTRACTED (surprise first). Single-source corpora: high-betweenness edges
    that bridge distant parts of the graph.
    """
    source_files = {
        d.get("source_file", "") for _, d in G.nodes(data=True) if d.get("source_file", "")
    }
    is_multi_source = len(source_files) > 1

    if is_multi_source:
        node_community = {}
        if communities:
            node_community = {n: cid for cid, nodes in communities.items() for n in nodes}
        results = []
        for u, v, d in G.edges(data=True):
            su = G.nodes[u].get("source_file", "")
            sv = G.nodes[v].get("source_file", "")
            if su and sv and su != sv:
                if _cross_language(su, sv):
                    continue  # cross-runtime edges are phantom noise
                same_community = (
                    node_community.get(u) is not None
                    and node_community.get(u) == node_community.get(v)
                )
                if same_community:
                    continue
                results.append({
                    "source": u, "target": v,
                    "source_label": G.nodes[u].get("label", u),
                    "target_label": G.nodes[v].get("label", v),
                    "relation": d.get("relation", ""),
                    "confidence": d.get("confidence", "EXTRACTED"),
                    "from_file": su, "to_file": sv,
                })
        rank = {"AMBIGUOUS": 0, "INFERRED": 1, "EXTRACTED": 2}
        results.sort(key=lambda r: (rank.get(r["confidence"], 3), -G.degree(r["source"])))
        return results[:top_n]

    # single source: bridge edges by betweenness
    try:
        centrality = nx.edge_betweenness_centrality(G)
    except Exception:
        return []
    ranked = sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [
        {
            "source": u, "target": v,
            "source_label": G.nodes[u].get("label", u),
            "target_label": G.nodes[v].get("label", v),
            "relation": d.get("relation", ""),
            "confidence": d.get("confidence", "EXTRACTED"),
            "score": round(score, 3),
        }
        for (u, v), score in ranked
        for d in [G.get_edge_data(u, v) or {}]
    ]


def knowledge_gaps(G: nx.Graph, top_n: int = 5) -> list[dict]:
    """Real code nodes with no incoming relationships - potential orphans."""
    gaps = []
    for nid, d in G.nodes(data=True):
        if d.get("file_type") != "code" or _is_file_node(G, nid):
            continue
        in_deg = G.in_degree(nid) if G.is_directed() else G.degree(nid)
        if in_deg == 0 and G.degree(nid) <= 1:
            gaps.append({"id": nid, "label": d.get("label", nid),
                         "source_file": d.get("source_file", "")})
        if len(gaps) >= top_n:
            break
    return gaps


def suggested_questions(G: nx.Graph, god_node_list: list[dict], top_n: int = 4) -> list[str]:
    """Questions this graph is uniquely positioned to answer."""
    questions: list[str] = []
    if len(god_node_list) >= 2:
        a, b = god_node_list[0]["label"], god_node_list[1]["label"]
        questions.append(f"How do {a} and {b} interact?")
    if len(god_node_list) >= 3:
        questions.append(f"What breaks if I change {god_node_list[2]['label']}?")
    hub = god_node_list[0]["label"] if god_node_list else None
    if hub:
        questions.append(f"Explain the role of {hub} in this codebase.")
    communities = G.graph.get("community_labels") or {}
    if len(communities) >= 2:
        first = next(iter(communities.values()))
        questions.append(f"What belongs to the {first} subsystem and what touches it?")
    return questions[:top_n]


def analyze(G: nx.Graph, communities: dict[int, list[str]] | None = None) -> dict:
    """Run the full analysis pass. Returns an analysis dict for the report."""
    if communities is None:
        communities = {i: [n] for i, n in enumerate(G.nodes)}
    god = god_nodes(G)
    return {
        "god_nodes": god,
        "surprises": surprising_connections(G, communities),
        "gaps": knowledge_gaps(G),
        "questions": suggested_questions(G, god),
        "confidence_split": _confidence_split(G),
    }


def _confidence_split(G: nx.Graph) -> dict:
    counts = {"EXTRACTED": 0, "INFERRED": 0, "AMBIGUOUS": 0}
    for _, _, d in G.edges(data=True):
        c = d.get("confidence", "EXTRACTED")
        counts[c] = counts.get(c, 0) + 1
    total = sum(counts.values()) or 1
    return {
        "counts": counts,
        "percent": {k: round(v / total * 100) for k, v in counts.items()},
    }
