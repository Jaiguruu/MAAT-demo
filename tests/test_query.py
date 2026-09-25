"""Tests for the query layer and the multi-agent system (offline modes)."""
from __future__ import annotations

import networkx as nx

from knowledgegraph.query import affected, explain, neighbors, path, query


def _graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("auth_py", label="auth.py", file_type="code", source_file="auth.py")
    G.add_node("authservice", label="AuthService", file_type="code", source_file="auth.py")
    G.add_node("login", label="login()", file_type="code", source_file="auth.py")
    G.add_node("db_py", label="db.py", file_type="code", source_file="db.py")
    G.add_node("dbpool", label="DatabasePool", file_type="code", source_file="db.py")
    G.add_edge("auth_py", "authservice", relation="contains", confidence="EXTRACTED")
    G.add_edge("auth_py", "login", relation="contains", confidence="EXTRACTED")
    G.add_edge("authservice", "db_py", relation="calls", confidence="INFERRED",
               confidence_score=0.85)
    G.add_edge("db_py", "dbpool", relation="contains", confidence="EXTRACTED")
    return G


def test_query_finds_relevant_subgraph():
    res = query(_graph(), "how does AuthService connect to the database")
    matched_labels = set(res.matched)
    assert "AuthService" in matched_labels or "DatabasePool" in matched_labels
    assert res.nodes and res.edges


def test_query_no_match_returns_empty():
    res = query(_graph(), "quantum-flux-capacitor-xyzzy")
    assert res.nodes == [] and res.matched == []


def test_find_node_fuzzy_resolution():
    G = _graph()
    from knowledgegraph.query import _find_node
    assert _find_node(G, "AuthService") == "authservice"
    assert _find_node(G, "authservice") == "authservice"
    assert _find_node(G, "auth.py") == "auth_py"
    assert _find_node(G, "NoSuchThing") is None


def test_path_between_entities():
    hops = path(_graph(), "AuthService", "DatabasePool")
    assert hops is not None
    assert len(hops) >= 1
    labels = [h["source_label"] for h in hops] + [hops[-1]["target_label"]]
    assert "AuthService" in labels and "DatabasePool" in labels


def test_path_unknown_entity_returns_none():
    assert path(_graph(), "AuthService", "QuantumFlux") is None


def test_explain_returns_neighborhood():
    d = explain(_graph(), "AuthService")
    assert d is not None
    assert d["label"] == "AuthService"
    assert d["degree"] >= 1
    assert any(c["label"] == "auth.py" for c in d["connections"])


def test_neighbors_filter_by_relation():
    out = neighbors(_graph(), "auth.py", relation="contains")
    assert all(n["relation"] == "contains" for n in out)
    assert out


def test_affected_blast_radius():
    hits = affected(_graph(), "auth_py", max_depth=3)
    labels = {h["label"] for h in hits}
    assert "AuthService" in labels
    depths = {h["label"]: h["depth"] for h in hits}
    assert depths["AuthService"] == 1


def test_affected_unknown_node_empty():
    assert affected(_graph(), "zzz-unknown") == []
