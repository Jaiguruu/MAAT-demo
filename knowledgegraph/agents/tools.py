"""Graph tools - the same primitives the CLI exposes, wrapped for agents.

Every tool takes the loaded graph explicitly (no hidden globals) and returns
plain dicts so both the LLM and the deterministic fallback path can consume
them. ``ToolResult`` carries a compact ``text`` rendering for prompts plus the
structured payload for debugging.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from knowledgegraph.query import affected, explain, neighbors, path, query


@dataclass
class ToolResult:
    name: str
    ok: bool
    text: str
    data: dict = field(default_factory=dict)


def load_graph(graph_path: str) -> nx.Graph:
    from knowledgegraph.build import load_graph as _load
    return _load(graph_path)


def tool_graph_stats(G: nx.Graph) -> ToolResult:
    """Overall shape of the graph - the orientation tool."""
    labels = G.graph.get("community_labels") or {}
    data = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "communities": len(labels),
        "community_names": sorted(labels.values())[:15],
    }
    text = (f"graph: {data['nodes']} nodes, {data['edges']} edges, "
            f"{data['communities']} communities: {', '.join(data['community_names'])}")
    return ToolResult("graph_stats", True, text, data)


def tool_search(G: nx.Graph, question: str, budget: int = 40) -> ToolResult:
    """Scoped subgraph for a plain-language question."""
    res = query(G, question, budget=budget)
    data = {"matched": res.matched, "nodes": res.nodes, "edges": res.edges}
    text = f"{len(res.matched)} direct hit(s): {', '.join(res.matched[:8])}\n{res.render()}"
    return ToolResult("search_graph", bool(res.nodes), text, data)


def tool_find_path(G: nx.Graph, a: str, b: str) -> ToolResult:
    """Shortest relationship path between two entities."""
    hops = path(G, a, b)
    if hops is None:
        return ToolResult("find_path", False, f"no path found between {a!r} and {b!r}", {})
    lines = [f"{h['source_label']} --{h['relation']}--> {h['target_label']} [{h['confidence']}]" for h in hops]
    return ToolResult("find_path", True, f"path ({len(hops)} hops):\n" + "\n".join(lines), {"hops": hops})


def tool_explain(G: nx.Graph, name: str) -> ToolResult:
    """One concept plus its neighborhood."""
    d = explain(G, name)
    if d is None:
        return ToolResult("explain_concept", False, f"no node matches {name!r}", {})
    lines = [f"{d['label']} ({d['file_type']}, {d['source_file']} {d['source_location'] or ''})",
             f"degree {d['degree']}, community {d.get('community_label') or d.get('community')}"]
    for c in d["connections"][:15]:
        arrow = "-->" if c["direction"] == "out" else "<--"
        lines.append(f"  {arrow} {c['label']} [{c['relation']}] [{c['confidence']}]")
    return ToolResult("explain_concept", True, "\n".join(lines), d)


def tool_neighbors(G: nx.Graph, name: str, relation: str | None = None) -> ToolResult:
    """Immediate neighbors, optionally filtered by relation type."""
    out = neighbors(G, name, relation=relation)
    if not out:
        return ToolResult("list_neighbors", False, f"no neighbors for {name!r}", {})
    lines = [f"{n['label']} --{n['relation']}-->  [{n['confidence']}]" for n in out]
    return ToolResult("list_neighbors", True, "\n".join(lines), {"neighbors": out})


def tool_affected(G: nx.Graph, name: str, depth: int = 3) -> ToolResult:
    """Blast radius of a node."""
    hits = affected(G, name, max_depth=depth)
    if not hits:
        return ToolResult("find_affected", False, f"no downstream dependents for {name!r}", {})
    lines = [f"{'  ' * h['depth']}{h['label']} (via {h['via']})" for h in hits]
    return ToolResult("find_affected", True, f"{len(hits)} affected:\n" + "\n".join(lines),
                      {"affected": hits})


TOOL_CATALOG = """Available graph tools:
- graph_stats: overall node/edge/community counts and community names
- search_graph(question): scoped subgraph for a plain-language question
- find_path(a, b): shortest relationship path between two entities
- explain_concept(name): one concept plus its neighborhood
- list_neighbors(name, relation?): immediate neighbors by relation type
- find_affected(name): everything transitively downstream of a node (blast radius)
"""
