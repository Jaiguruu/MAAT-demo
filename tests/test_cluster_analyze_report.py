"""Tests for stages 4-6 - clustering, analysis, report rendering."""
from __future__ import annotations

import networkx as nx

from knowledgegraph.analyze import analyze, god_nodes, surprising_connections
from knowledgegraph.cluster import cluster, label_communities_by_hub
from knowledgegraph.report import generate


def _two_cluster_graph() -> nx.Graph:
    """Two dense triangles joined by one bridge edge."""
    G = nx.Graph()
    A = [f"a{i}" for i in range(4)]
    B = [f"b{i}" for i in range(4)]
    for i, n in enumerate(A):
        G.add_node(n, label=f"A{i}", file_type="code", source_file=f"a{i}.py")
        G.add_edge(n, A[(i + 1) % 4], relation="calls", confidence="EXTRACTED")
    for i, n in enumerate(B):
        G.add_node(n, label=f"B{i}", file_type="code", source_file=f"b{i}.py")
        G.add_edge(n, B[(i + 1) % 4], relation="calls", confidence="EXTRACTED")
    G.add_edge(A[0], B[0], relation="references", confidence="EXTRACTED")
    return G


def test_cluster_assigns_communities():
    G = _two_cluster_graph()
    communities = cluster(G)
    assert communities
    assert all("community" in G.nodes[n] for n in G.nodes)
    labels = G.graph["community_labels"]
    assert len(labels) == len(communities)
    # deterministic: rerun gives same partition sizes
    G2 = _two_cluster_graph()
    communities2 = cluster(G2)
    assert sorted(len(v) for v in communities.values()) == sorted(len(v) for v in communities2.values())


def test_cluster_handles_empty_and_edgeless():
    assert cluster(nx.Graph()) == {}
    G = nx.Graph()
    G.add_node("solo", label="Solo", file_type="code", source_file="s.py")
    comms = cluster(G)
    assert sum(len(v) for v in comms.values()) == 1


def test_community_labels_from_hub():
    G = _two_cluster_graph()
    communities = cluster(G)
    labels = label_communities_by_hub(G, communities)
    assert all(isinstance(v, str) and v for v in labels.values())


def test_god_nodes_exclude_file_anchors():
    G = nx.Graph()
    G.add_node("file_a", label="a.py", file_type="code", source_file="a.py")
    G.add_node("core", label="Core", file_type="code", source_file="a.py")
    G.add_node("x", label="X", file_type="code", source_file="a.py")
    G.add_node("y", label="Y", file_type="code", source_file="a.py")
    G.add_edge("file_a", "core", relation="contains")
    G.add_edge("core", "x", relation="calls")
    G.add_edge("core", "y", relation="calls")
    G.add_edge("x", "y", relation="calls")
    gods = god_nodes(G)
    assert gods[0]["label"] == "Core"
    assert all(g["label"] != "a.py" for g in gods)


def test_surprising_connections_cross_file():
    G = nx.Graph()
    G.add_node("a1", label="A1", file_type="code", source_file="a.py")
    G.add_node("b1", label="B1", file_type="code", source_file="b.py")
    G.add_node("a2", label="A2", file_type="code", source_file="a.py")
    G.add_edge("a1", "b1", relation="calls", confidence="INFERRED")
    G.add_edge("a1", "a2", relation="contains", confidence="EXTRACTED")
    surprises = surprising_connections(G, top_n=5)
    assert any(s["source"] == "a1" and s["target"] == "b1" for s in surprises)


def test_analyze_full():
    G = _two_cluster_graph()
    communities = cluster(G)
    result = analyze(G, communities)
    assert "god_nodes" in result and "surprises" in result
    assert "questions" in result and "confidence_split" in result
    assert result["confidence_split"]["counts"]["EXTRACTED"] == G.number_of_edges()


def test_report_renders_all_sections():
    G = _two_cluster_graph()
    communities = cluster(G)
    analysis = analyze(G, communities)
    md = generate(
        G=G,
        communities=communities,
        cohesion_scores={int(k): v for k, v in G.graph["cohesion"].items()},
        community_labels={int(k): v for k, v in G.graph["community_labels"].items()},
        god_node_list=analysis["god_nodes"],
        surprise_list=analysis["surprises"],
        detection_result={"total_files": 8, "total_words": 9000, "warning": None},
        root="/tmp/demo",
        suggested_questions=analysis["questions"],
        gap_list=analysis["gaps"],
    )
    for section in ("# Graph Report", "## Headline Numbers", "## God Nodes",
                    "## Communities", "## Surprising Connections",
                    "## Suggested Questions"):
        assert section in md
    assert "EXTRACTED" in md
