"""Tests for the LangGraph multi-agent system (offline / deterministic mode).

The LLM-backed paths degrade gracefully when OPENAI_API_KEY is missing, so the
full graph can be exercised here with zero network access.
"""
from __future__ import annotations

import json

import pytest

from knowledgegraph.agents.graph import (
    _keyword_plan,
    build_agent_graph,
    synthesizer_node,
)
from knowledgegraph.agents.state import AgentState, _merge_list
from knowledgegraph.agents.tools import (
    tool_explain,
    tool_find_path,
    tool_graph_stats,
    tool_affected,
    tool_search,
)
from knowledgegraph.build import save_graph


@pytest.fixture(scope="module")
def graph_file(tmp_path_factory):
    import networkx as nx

    G = nx.Graph()
    G.add_node("auth_py", label="auth.py", file_type="code", source_file="auth.py")
    G.add_node("authservice", label="AuthService", file_type="code", source_file="auth.py")
    G.add_node("db_py", label="db.py", file_type="code", source_file="db.py")
    G.add_node("dbpool", label="DatabasePool", file_type="code", source_file="db.py")
    G.add_edge("auth_py", "authservice", relation="contains", confidence="EXTRACTED")
    G.add_edge("authservice", "db_py", relation="calls", confidence="INFERRED",
               confidence_score=0.85)
    G.add_edge("db_py", "dbpool", relation="contains", confidence="EXTRACTED")
    G.graph["community_labels"] = {"0": "auth.py", "1": "db.py"}
    p = tmp_path_factory.mktemp("agent") / "graph.json"
    save_graph(G, p)
    return str(p)


def _state(question: str, graph_file: str) -> AgentState:
    return {"question": question, "graph_path": graph_file, "evidence": [], "trace": []}


def test_keyword_plan_routes_impact():
    plan = _keyword_plan("what breaks if I change AuthService")
    assert "impact" in plan["workers"]
    plan2 = _keyword_plan("how does auth connect to the database")
    assert "explorer" in plan2["workers"]


def test_supervisor_node_routes(graph_file):
    node = __import__("knowledgegraph.agents.graph", fromlist=["supervisor_node"]).supervisor_node
    out = node(_state("what breaks if I change AuthService", graph_file))
    assert "impact" in out["plan"]
    assert out["trace"]


def test_workers_produce_evidence(graph_file):
    from knowledgegraph.agents.graph import explorer_node, impact_node, orient_node
    st = _state("how does AuthService work", graph_file)
    e = explorer_node(st)
    assert any(ev["tool"] == "search_graph" for ev in e["evidence"])
    st2 = _state("what breaks if I change AuthService", graph_file)
    i = impact_node(st2)
    assert any(ev["tool"] == "find_affected" for ev in i["evidence"])
    o = orient_node(st)
    assert any(ev["tool"] == "graph_stats" for ev in o["evidence"])


def test_synthesizer_deterministic_fallback(graph_file):
    st = _state("question", graph_file)
    st["evidence"] = [{"tool": "graph_stats", "ok": True, "text": "2 nodes", "data": {}}]
    out = synthesizer_node(st)
    assert "graph_stats" in out["answer"]
    assert out["trace"]


def test_tools_return_structured_results(graph_file):
    from knowledgegraph.agents.tools import load_graph
    G = load_graph(graph_file)
    stats = tool_graph_stats(G)
    assert stats.ok and "nodes" in stats.data
    search = tool_search(G, "AuthService connect database")
    assert search.data.get("matched")
    exp = tool_explain(G, "AuthService")
    assert exp.ok and exp.data.get("degree", 0) >= 1
    pth = tool_find_path(G, "AuthService", "DatabasePool")
    assert pth.ok and pth.data.get("hops")
    aff = tool_affected(G, "auth_py")
    assert aff.ok


def test_full_agent_graph_runs_offline(graph_file):
    app = build_agent_graph()
    final = app.invoke(_state("how does AuthService connect to the database", graph_file))
    assert final.get("answer")
    assert final.get("evidence")
    # supervisor -> workers -> synthesizer all appear in the trail
    trail = " ".join(final.get("trace") or [])
    assert "supervisor" in trail and "synthesizer" in trail


def test_merge_list_reducer():
    assert _merge_list(["a"], ["a", "b"]) == ["a", "b"]
    d1 = {"x": 1}
    assert _merge_list([d1], [d1, {"y": 2}]) == [d1, {"y": 2}]


def test_answer_question_missing_graph(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from knowledgegraph.agents.graph import answer_question
    out = answer_question("anything", graph_path=str(tmp_path / "nope.json"))
    assert "error" in out and "kg build" in out
