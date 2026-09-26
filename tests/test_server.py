"""Offline tests for the kg-ui server (TestClient - no network, no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from knowledgegraph.server.app import app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # hermetic: no key and no model env (env wins over the dev .env),
    # graph output in tmp
    monkeypatch.setattr("knowledgegraph.config.api_key", lambda: None)
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_MODEL",
                "KG_SEMANTIC_MODEL", "KG_AGENT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return TestClient(app)


def test_settings_roundtrip(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["effective"]["llm_ready"] is False
    assert body["api_key_set"] is False

    r2 = client.post("/api/settings", json={"model": "test-model-x", "api_key": "k"})
    assert r2.status_code == 200
    assert r2.json()["effective"]["model"] == "test-model-x"
    assert r2.json()["api_key_set"] is True
    # the key itself is never echoed
    assert "k" != r2.json().get("api_key")


def test_settings_test_requires_key(client):
    r = client.post("/api/settings/test", json={})
    assert r.status_code == 400


def test_graph_404_when_not_built(client):
    r = client.get("/api/graph")
    assert r.status_code == 404


def test_graph_served_when_built(client):
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json

    G = nx.Graph()
    G.add_node("auth_py", label="auth.py", file_type="code", source_file="auth.py")
    G.add_node("authservice", label="AuthService", file_type="code", source_file="auth.py")
    G.add_edge("auth_py", "authservice", relation="contains", confidence="EXTRACTED")
    G.graph["community_labels"] = {"0": "auth.py"}
    save_graph(G, default_graph_json())

    r = client.get("/api/graph")
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"][0]["label"] == "auth.py"
    assert len(body["links"]) == 1

    r2 = client.get("/api/graph/node/auth_py")
    assert r2.status_code == 200
    assert r2.json()["node"]["label"] == "auth.py"
    assert len(r2.json()["neighbors"]) == 1

    r3 = client.get("/api/graph/node/nope")
    assert r3.status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "kg-ui" in r.text


def test_ask_without_graph_400(client):
    r = client.post("/api/ask", json={"question": "q", "system": "graph"})
    assert r.status_code == 400


def test_ask_graph_stream_offline(client, tmp_path):
    """Full ask over SSE with no LLM: keyword plan + extractive synthesis."""
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json

    G = nx.Graph()
    G.add_node("auth_py", label="auth.py", file_type="code", source_file="auth.py")
    G.add_node("authservice", label="AuthService", file_type="code", source_file="auth.py")
    G.add_edge("auth_py", "authservice", relation="contains", confidence="EXTRACTED")
    G.graph["community_labels"] = {"0": "auth.py"}
    save_graph(G, default_graph_json())

    with client.stream("POST", "/api/ask",
                       json={"question": "how does AuthService work", "system": "graph"}) as r:
        assert r.status_code == 200
        events_seen = []
        for line in r.iter_lines():
            if line.startswith("data: "):
                events_seen.append(json.loads(line[6:]))
    types = [e["type"] for e in events_seen]
    assert "run_started" in types
    assert "plan" in types
    assert "tool_result" in types
    assert types[-1] == "done"
    plan_ev = next(e for e in events_seen if e["type"] == "plan")
    assert plan_ev["source"].startswith("keyword")


def test_graph_source_root_stamped(client, tmp_path):
    """The pipeline stamps source_root; the server reads it back."""
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json
    from knowledgegraph.server.app import graph_source_root

    assert graph_source_root() is None  # nothing built
    G = nx.Graph()
    G.add_node("a", label="a.py", file_type="code", source_file="a.py")
    save_graph(G, default_graph_json())
    assert graph_source_root() is None  # hand-made graph has no stamp

    G.graph["source_root"] = "/some/repo"
    save_graph(G, default_graph_json())
    assert graph_source_root() == "/some/repo"


def test_baseline_root_prefers_graph_repo(client, tmp_path, monkeypatch):
    """Baseline follows the graph's stamped repo unless explicitly overridden."""
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json
    from knowledgegraph.server.app import baseline_root

    monkeypatch.delenv("KG_UI_REPO", raising=False)
    assert baseline_root() == Path.cwd()  # no graph, no override

    G = nx.Graph()
    G.add_node("a", label="a.py", file_type="code", source_file="a.py")
    G.graph["source_root"] = str(tmp_path / "there")
    save_graph(G, default_graph_json())
    assert baseline_root() == tmp_path / "there"  # follows the graph

    # explicit override wins
    client.post("/api/settings", json={"baseline_repo": "/explicit/override"})
    assert str(baseline_root()).replace("\\", "/").endswith("explicit/override")


def test_settings_reports_repo_mismatch(client, tmp_path):
    """The settings payload exposes graph_source vs baseline_root for the UI."""
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json

    G = nx.Graph()
    G.add_node("a", label="a.py", file_type="code", source_file="a.py")
    G.graph["source_root"] = "/built/from/here"
    save_graph(G, default_graph_json())
    client.post("/api/settings", json={"baseline_repo": "/exploring/there"})

    repos = client.get("/api/settings").json()["repos"]
    assert repos["graph_source"] == "/built/from/here"
    assert Path(repos["baseline_root"]) == Path("/exploring/there")  # OS-native sep
    assert repos["baseline_override"] == "/exploring/there"


def test_bench_warns_on_repo_mismatch(tmp_path, capsys):
    """skip_build + mismatched repo prints a provenance warning."""
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json
    from knowledgegraph.bench import run_bench

    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    (repo_a / "a.py").write_text("x = 1\n", encoding="utf-8")
    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    (repo_b / "b.py").write_text("y = 2\n", encoding="utf-8")

    G = nx.Graph()
    G.add_node("a", label="a.py", file_type="code", source_file="a.py")
    G.graph["source_root"] = str(repo_a)
    save_graph(G, default_graph_json())

    run_bench(repo_b, ["q"], skip_build=True,
              graph_path=str(default_graph_json()),
              out_dir=tmp_path / "bench")
    out = capsys.readouterr().out
    assert "stale structure" in out and str(repo_a) in out


def test_ask_baseline_stream_offline(client, tmp_path, monkeypatch):
    """Baseline SSE stream runs (LLM unavailable -> immediate error event)."""
    import networkx as nx
    from knowledgegraph.build import save_graph
    from knowledgegraph.paths import default_graph_json

    G = nx.Graph()
    G.add_node("a", label="a.py", file_type="code", source_file="a.py")
    save_graph(G, default_graph_json())

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr("knowledgegraph.server.app._bench_repo_root", lambda: repo)

    with client.stream("POST", "/api/ask",
                       json={"question": "q", "system": "baseline"}) as r:
        assert r.status_code == 200
        events_seen = []
        for line in r.iter_lines():
            if line.startswith("data: "):
                events_seen.append(json.loads(line[6:]))
    types = [e["type"] for e in events_seen]
    assert "run_started" in types
    assert types[-1] == "done"


def test_events_bridge_replay_and_live():
    """Late subscriber gets transcript replay + live events on the loop."""
    import asyncio
    from knowledgegraph.agents.events import SSEBridge, emit

    async def scenario():
        b = SSEBridge()
        b.publish(emit({"type": "a"}))   # before subscribe -> replay covers it
        q = b.subscribe()
        assert q.get_nowait()["type"] == "a"
        b.publish(emit({"type": "b"}))   # live: call_soon_threadsafe delivery
        await asyncio.sleep(0)            # let the loop run the scheduled put
        assert q.get_nowait()["type"] == "b"
        assert [e["type"] for e in b.transcript()] == ["a", "b"]

    asyncio.run(scenario())
