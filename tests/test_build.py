"""Tests for stage 3 - build, schema validation, inferred call edges, dedup."""
from __future__ import annotations

import networkx as nx

from knowledgegraph.build import (
    add_inferred_call_edges,
    build_graph,
    dedupe_labels,
    from_node_link,
    save_graph,
    to_node_link,
)
from knowledgegraph.validate import assert_valid, validate_extraction


def _frag(nodes, edges, **extra):
    return {"nodes": nodes, "edges": edges, **extra}


def test_validate_accepts_good_fragment():
    frag = _frag(
        [{"id": "a", "label": "A", "file_type": "code", "source_file": "a.py"}],
        [{"source": "a", "target": "a", "relation": "contains",
          "confidence": "EXTRACTED", "source_file": "a.py"}],
    )
    assert validate_extraction(frag) == []
    assert_valid(frag)


def test_validate_rejects_bad_fragment():
    frag = _frag(
        [{"id": "a", "label": "A"}],  # missing file_type/source_file
        [{"source": "a", "target": "ghost", "relation": "x",
          "confidence": "WRONG", "source_file": "a.py"}],
    )
    errors = validate_extraction(frag)
    assert any("missing required field" in e for e in errors)
    assert any("invalid confidence" in e for e in errors)
    assert any("does not match any node id" in e for e in errors)


def test_build_merges_fragments(tmp_path):
    f1 = _frag(
        [{"id": "file_a", "label": "a.py", "file_type": "code", "source_file": "a.py"},
         {"id": "cls_x", "label": "X", "file_type": "code", "source_file": "a.py"}],
        [{"source": "file_a", "target": "cls_x", "relation": "contains",
          "confidence": "EXTRACTED", "source_file": "a.py"}],
    )
    f2 = _frag(
        [{"id": "file_b", "label": "b.py", "file_type": "code", "source_file": "b.py"}],
        [],
    )
    G = build_graph([f1, f2])
    assert G.number_of_nodes() == 3
    assert G.number_of_edges() == 1


def test_build_drops_edges_to_unknown_nodes():
    f1 = _frag(
        [{"id": "a", "label": "A", "file_type": "code", "source_file": "a.py"}],
        [{"source": "a", "target": "missing", "relation": "calls",
          "confidence": "INFERRED", "source_file": "a.py"}],
    )
    G = build_graph([f1])
    assert G.number_of_edges() == 0


def test_inferred_call_edges_second_pass():
    # file A defines auth(); file B calls auth() - second pass connects them
    frag_a = _frag(
        [{"id": "file_a", "label": "a.py", "file_type": "code", "source_file": "a.py"},
         {"id": "auth", "label": "auth()", "file_type": "code", "source_file": "a.py"}],
        [{"source": "file_a", "target": "auth", "relation": "contains",
          "confidence": "EXTRACTED", "source_file": "a.py"}],
        raw_calls=[],
        definitions={"auth": "auth"},
    )
    frag_b = _frag(
        [{"id": "file_b", "label": "b.py", "file_type": "code", "source_file": "b.py"}],
        [],
        raw_calls=[{"caller": "file_b", "name": "auth", "location": "L3",
                    "source_file": "b.py"}],
    )
    G = build_graph([frag_a, frag_b])
    added = add_inferred_call_edges(G, [frag_a, frag_b])
    assert added == 1
    assert G.has_edge("file_b", "auth")
    assert G["file_b"]["auth"]["confidence"] == "INFERRED"
    assert G["file_b"]["auth"]["confidence_score"] == 0.85


def test_roundtrip_serialization(tmp_path):
    G = nx.Graph()
    G.add_node("a", label="A", file_type="code", source_file="a.py")
    G.add_node("b", label="B", file_type="code", source_file="a.py")
    G.add_edge("a", "b", relation="calls", confidence="EXTRACTED")
    p = save_graph(G, tmp_path / "graph.json")
    G2 = from_node_link(__import__("json").loads(p.read_text(encoding="utf-8")))
    assert set(G2.nodes) == {"a", "b"}
    assert G2["a"]["b"]["relation"] == "calls"


def test_dedupe_merges_exact_label_duplicates():
    G = nx.Graph()
    G.add_node("x1", label="AuthService", file_type="code", source_file="a.py")
    G.add_node("x2", label="AuthService", file_type="concept", source_file="")
    G.add_node("doc1", label="Totally Different", file_type="document", source_file="d.md")
    G.add_edge("doc1", "x2", relation="references", confidence="EXTRACTED")
    merges = dedupe_labels(G)
    assert merges >= 1
    assert G.has_node("x1") or G.has_node("x2")
    # the non-duplicate survives
    assert G.has_node("doc1")
