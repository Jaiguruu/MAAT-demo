"""Query layer - traverse the graph instead of re-reading the corpus.

Four primitives, all pure graph operations on ``graph.json``:

- ``query(question)``  -> scored subgraph scoped to the question terms
- ``path(a, b)``       -> shortest relationship path between two nodes
- ``explain(name)``    -> one node plus its full neighborhood
- ``affected(name)``   -> everything transitively downstream of a node

The agent system (``knowledgegraph.agents``) exposes these same primitives as
tools so an LLM can compose them to answer open-ended questions.
"""
from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field

import networkx as nx

_AFFECTED_RELATIONS = frozenset({
    "calls", "indirect_call", "references", "imports", "imports_from",
    "re_exports", "inherits", "extends", "implements", "uses", "mixes_in",
    "embeds", "contains", "method",
})


def _norm(label: str) -> str:
    return unicodedata.normalize("NFC", label).casefold()


def _bare(label: str) -> str:
    label = _norm(label)
    return label[:-2] if label.endswith("()") else label


@dataclass
class SubgraphResult:
    """A question-scoped slice of the graph, with evidence for each hit."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    truncated: bool = False

    def render(self, max_edges: int = 40) -> str:
        lines = []
        for n in self.nodes:
            label = n.get("label", n.get("id", "?"))
            src = n.get("source_file") or ""
            loc = n.get("source_location") or ""
            where = f"{src}:{loc}" if loc and src else (src or "-")
            lines.append(f"  {label}  [{n.get('file_type', '?')}]  ({where})")
        lines.append("")
        shown = self.edges[:max_edges]
        for e in shown:
            lines.append(f"  {e['source']} --{e.get('relation', '?')}--> {e['target']}"
                         f"  [{e.get('confidence', '?')}]")
        if len(self.edges) > max_edges:
            lines.append(f"  ... {len(self.edges) - max_edges} more edges")
        return "\n".join(lines)


def _tokenize(question: str) -> list[str]:
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", question)
    stop = {
        "what", "which", "how", "does", "do", "the", "a", "an", "is", "are",
        "in", "on", "of", "to", "and", "or", "for", "with", "connects",
        "between", "from", "this", "that", "it", "its", "show", "me", "explain",
    }
    return [w.lower() for w in words if w.lower() not in stop and len(w) > 1]


def _find_node(G: nx.Graph, name: str) -> str | None:
    """Resolve a user-supplied name to a node id: exact id, exact label,
    bare-callable label, then substring fallback."""
    q = name.rstrip("/\\")
    if q in G:
        return q
    qn = _norm(q)
    for nid, d in G.nodes(data=True):
        if _norm(str(d.get("label", ""))) == qn:
            return str(nid)
    qb = _bare(q)
    for nid, d in G.nodes(data=True):
        if _bare(str(d.get("label", ""))) == qb:
            return str(nid)
    matches = [
        str(nid) for nid, d in G.nodes(data=True)
        if qb and qb in _bare(str(d.get("label", "")))
    ]
    if len(matches) == 1:
        return matches[0]
    return matches[0] if matches else None


def query(G: nx.Graph, question: str, budget: int = 60) -> SubgraphResult:
    """Return the subgraph most relevant to a plain-language question.

    Scoring: exact label hits > prefix hits > substring hits; one hop of
    neighborhood is pulled in per hit until the budget is spent.
    """
    tokens = _tokenize(question)
    if not tokens:
        return SubgraphResult()

    scored: dict[str, float] = {}
    for nid, d in G.nodes(data=True):
        label = _norm(str(d.get("label", "")))
        bare = _bare(str(d.get("label", "")))
        score = 0.0
        for t in tokens:
            if label == t or bare == t:
                score += 5.0
            elif bare.startswith(t) or t in bare:
                score += 2.0
        if score > 0:
            scored[nid] = score

    if not scored:
        return SubgraphResult()

    top = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    keep: list[str] = []
    for nid, _ in top:
        if len(keep) >= budget // 3:
            break
        keep.append(nid)

    # one hop of neighborhood per direct hit
    extra: set[str] = set()
    for nid in keep:
        for nbr in G.neighbors(nid):
            extra.add(nbr)
            if len(keep) + len(extra) >= budget:
                break
        if len(keep) + len(extra) >= budget:
            break

    node_ids = set(keep) | extra
    nodes = [dict(G.nodes[n], id=n) for n in node_ids if n in G]
    edges = [
        {"source": u, "target": v, **d}
        for u, v, d in G.edges(data=True)
        if u in node_ids and v in node_ids
    ]
    return SubgraphResult(
        nodes=nodes,
        edges=edges,
        matched=[str(G.nodes[n].get("label", n)) for n in keep],
        truncated=len(nodes) >= budget,
    )


def path(G: nx.Graph, a: str, b: str, max_hops: int = 6) -> list[dict] | None:
    """Shortest relationship path between two named entities, as edge dicts."""
    src = _find_node(G, a)
    tgt = _find_node(G, b)
    if src is None or tgt is None:
        return None
    try:
        nodes = nx.shortest_path(G.to_undirected(), src, tgt)
    except nx.NetworkXNoPath:
        return None
    if len(nodes) - 1 > max_hops:
        return None
    hops: list[dict] = []
    for u, v in zip(nodes, nodes[1:]):
        d = G.get_edge_data(u, v) or (G.get_edge_data(v, u) or {})
        hops.append({
            "source": u, "target": v,
            "source_label": G.nodes[u].get("label", u),
            "target_label": G.nodes[v].get("label", v),
            "relation": d.get("relation", "?"),
            "confidence": d.get("confidence", "EXTRACTED"),
        })
    return hops


def explain(G: nx.Graph, name: str, max_connections: int = 30) -> dict | None:
    """One node plus its full neighborhood, sorted by connection strength."""
    nid = _find_node(G, name)
    if nid is None:
        return None
    d = dict(G.nodes[nid])
    connections = []
    for nbr in G.neighbors(nid):
        ed = G.get_edge_data(nid, nbr) or {}
        connections.append({
            "node": nbr,
            "label": G.nodes[nbr].get("label", nbr),
            "file_type": G.nodes[nbr].get("file_type", ""),
            "source_file": G.nodes[nbr].get("source_file", ""),
            "direction": "out" if G.has_edge(nid, nbr) else "in",
            "relation": ed.get("relation", "?"),
            "confidence": ed.get("confidence", "EXTRACTED"),
        })
    connections.sort(key=lambda c: -G.degree(c["node"]))
    community = G.nodes[nid].get("community")
    return {
        "id": nid,
        "label": d.get("label", nid),
        "file_type": d.get("file_type", ""),
        "source_file": d.get("source_file", ""),
        "source_location": d.get("source_location"),
        "degree": G.degree(nid),
        "community": community,
        "community_label": (G.graph.get("community_labels") or {}).get(str(community)),
        "connections": connections[:max_connections],
    }


def neighbors(G: nx.Graph, name: str, relation: str | None = None) -> list[dict]:
    """Immediate neighbors of a node, optionally filtered by relation."""
    nid = _find_node(G, name)
    if nid is None:
        return []
    out = []
    for nbr in G.neighbors(nid):
        eds = G.get_edge_data(nid, nbr) or {}
        if relation and eds.get("relation") != relation:
            continue
        out.append({
            "node": nbr, "label": G.nodes[nbr].get("label", nbr),
            "relation": eds.get("relation", "?"),
            "confidence": eds.get("confidence", "EXTRACTED"),
        })
    return out


def affected(G: nx.Graph, name: str, max_depth: int = 4) -> list[dict]:
    """Blast radius: everything transitively downstream of a node."""
    nid = _find_node(G, name)
    if nid is None:
        return []
    visited: dict[str, tuple[int, str]] = {}
    queue: deque[tuple[str, int, str]] = deque([(nid, 0, "root")])
    directed = G.is_directed()
    while queue:
        node, depth, via = queue.popleft()
        if node in visited or depth > max_depth:
            continue
        visited[node] = (depth, via)
        out_iter = G.out_edges(node, data=True) if directed else G.edges(node, data=True)
        for _, nbr, d in out_iter:
            if d.get("relation", "") in _AFFECTED_RELATIONS:
                queue.append((nbr, depth + 1, str(d.get("relation", "?"))))
    return [
        {
            "node": n,
            "label": G.nodes[n].get("label", n),
            "depth": depth,
            "via": via,
            "source_file": G.nodes[n].get("source_file", ""),
        }
        for n, (depth, via) in sorted(visited.items(), key=lambda kv: kv[1][0])
        if n != nid
    ]
