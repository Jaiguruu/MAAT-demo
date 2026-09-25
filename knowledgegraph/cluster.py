"""Stage 4 - community detection.

Uses Leiden (graspologic) when available for best quality, and falls back to
Louvain (built into networkx) otherwise. Oversized communities are split with a
second pass on the subgraph, isolates become single-node communities, and each
community receives a deterministic, LLM-free label derived from its
highest-degree member (the structural hub).
"""
from __future__ import annotations

import contextlib
import inspect
import io
import sys

import networkx as nx


def _suppress_output():
    return contextlib.redirect_stdout(io.StringIO())


def _partition(G: nx.Graph, resolution: float = 1.0) -> dict[str, int]:
    """Run community detection; returns {node_id: community_id}."""
    # deterministic input ordering keeps runs comparable across machines
    stable = nx.Graph()
    stable.add_nodes_from(sorted(G.nodes(), key=str))
    for src, tgt, attrs in sorted(
        G.edges(data=True),
        key=lambda row: (str(row[0]), str(row[1])),
    ):
        stable.add_edge(src, tgt, **attrs)

    try:
        from graspologic.partition import leiden
        sig = inspect.signature(leiden).parameters
        kwargs: dict = {}
        if "random_seed" in sig:
            kwargs["random_seed"] = 42
        if "trials" in sig:
            kwargs["trials"] = 1
        if "resolution" in sig:
            kwargs["resolution"] = resolution
        old_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            with _suppress_output():
                result = leiden(stable, **kwargs)
        finally:
            sys.stderr = old_stderr
        return result
    except ImportError:
        pass

    kwargs = {"seed": 42, "threshold": 1e-4, "resolution": resolution}
    if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
        kwargs["max_level"] = 10
    communities = nx.community.louvain_communities(stable, **kwargs)
    return {node: cid for cid, nodes in enumerate(communities) for node in nodes}


_MAX_COMMUNITY_FRACTION = 0.25
_MIN_SPLIT_SIZE = 10
_COHESION_SPLIT_THRESHOLD = 0.05
_COHESION_SPLIT_MIN_SIZE = 50


def label_communities_by_hub(G: nx.Graph, communities: dict[int, list[str]]) -> dict[int, str]:
    """Name each community after its highest-degree member - deterministic, no LLM."""
    labels: dict[int, str] = {}
    for cid, members in communities.items():
        present = [n for n in members if n in G]
        if not present:
            labels[cid] = f"Community {cid}"
            continue
        hub = min(present, key=lambda n: (-G.degree(n), str(n)))
        name = str(G.nodes[hub].get("label") or hub).strip()
        if name.endswith("()"):
            name = name[:-2]
        labels[cid] = name or f"Community {cid}"
    return labels


def cohesion(G: nx.Graph, members: list[str]) -> float:
    """Internal edge weight fraction of a community (0..1]."""
    internal = 0
    total = 0
    member_set = set(members)
    for n in members:
        for _, nbr in G.edges(n):
            total += 1
            if nbr in member_set:
                internal += 1
    return internal / total if total else 0.0


def _split_community(G: nx.Graph, members: list[str]) -> list[list[str]]:
    sub = G.subgraph(members)
    try:
        partition = _partition(sub, resolution=2.0)
    except Exception:
        return [members]
    groups: dict[int, list[str]] = {}
    for node, cid in partition.items():
        groups.setdefault(cid, []).append(node)
    return list(groups.values()) if len(groups) > 1 else [members]


def cluster(
    G: nx.Graph,
    resolution: float = 1.0,
    exclude_hubs_percentile: float | None = None,
) -> dict[int, list[str]]:
    """Run community detection. Returns {community_id: [node_ids]}.

    Community 0 is the largest after splitting. Oversized communities
    (> 25% of graph nodes, min 10 nodes) are re-split. Hub exclusion lets
    utility super-hubs be reattached by majority vote instead of gluing
    unrelated subsystems together.
    """
    if G.number_of_nodes() == 0:
        return {}
    if G.is_directed():
        G = G.to_undirected()
    if G.number_of_edges() == 0:
        return {i: [n] for i, n in enumerate(sorted(G.nodes))}

    hub_nodes: set[str] = set()
    if exclude_hubs_percentile is not None:
        degrees = sorted(d for _, d in G.degree())
        if degrees:
            idx = max(0, int(len(degrees) * exclude_hubs_percentile / 100) - 1)
            threshold = degrees[idx]
            hub_nodes = {n for n, d in G.degree() if d > threshold}

    excluded = hub_nodes
    isolates = [n for n in G.nodes() if G.degree(n) == 0 and n not in excluded]
    connected_nodes = [n for n in G.nodes() if G.degree(n) > 0 and n not in excluded]
    connected = G.subgraph(connected_nodes)

    raw: dict[int, list[str]] = {}
    if connected.number_of_nodes() > 0:
        partition = _partition(connected, resolution=resolution)
        for node, cid in partition.items():
            raw.setdefault(cid, []).append(node)

    next_cid = max(raw.keys(), default=-1) + 1
    for node in isolates:
        raw[next_cid] = [node]
        next_cid += 1

    if hub_nodes:
        node_community: dict[str, int] = {n: cid for cid, nodes in raw.items() for n in nodes}
        for hub in sorted(hub_nodes):
            votes: dict[int, int] = {}
            for nb in G.neighbors(hub):
                cid = node_community.get(nb)
                if cid is not None:
                    votes[cid] = votes.get(cid, 0) + 1
            if votes:
                best = min(votes, key=lambda c: (-votes[c], c))
                raw.setdefault(best, []).append(hub)
                node_community[hub] = best
            else:
                raw[next_cid] = [hub]
                node_community[hub] = next_cid
                next_cid += 1

    # split oversized communities
    max_size = max(_MIN_SPLIT_SIZE, int(G.number_of_nodes() * _MAX_COMMUNITY_FRACTION))
    final: list[list[str]] = []
    for nodes in raw.values():
        if len(nodes) > max_size:
            final.extend(_split_community(G, nodes))
        else:
            final.append(nodes)

    # stable ids: largest community first
    final.sort(key=len, reverse=True)
    communities = {i: nodes for i, nodes in enumerate(final)}

    # annotate graph
    node_community = {n: cid for cid, nodes in communities.items() for n in nodes}
    nx.set_node_attributes(G, node_community, "community")
    labels = label_communities_by_hub(G, communities)
    G.graph["community_labels"] = {str(k): v for k, v in labels.items()}
    cohesions = {cid: cohesion(G, nodes) for cid, nodes in communities.items()}
    G.graph["cohesion"] = {str(k): round(v, 3) for k, v in cohesions.items()}
    return communities
