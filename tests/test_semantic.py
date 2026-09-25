"""Tests for the semantic extraction pass (offline - no LLM calls)."""
from __future__ import annotations

import networkx as nx
import pytest

from knowledgegraph.analyze import god_nodes
from knowledgegraph.semantic import (
    _parse_json_out,
    apply_semantic_fragments,
    build_code_label_index,
    extract_semantic_fragment,
)
from knowledgegraph.security import normalize_file_type


# --- normalization fix ---------------------------------------------------------

def test_normalize_file_type_keeps_canonical_types():
    assert normalize_file_type("code") == "code"
    assert normalize_file_type("document") == "document"
    assert normalize_file_type("paper") == "paper"
    assert normalize_file_type("image") == "image"
    assert normalize_file_type("concept") == "concept"
    assert normalize_file_type("rationale") == "rationale"


def test_normalize_file_type_maps_synonyms():
    assert normalize_file_type("markdown") == "document"
    assert normalize_file_type("technology") == "concept"
    assert normalize_file_type("note") == "rationale"


def test_normalize_file_type_falls_back_to_concept():
    assert normalize_file_type(None) == "concept"
    assert normalize_file_type("who-knows") == "concept"


# --- LLM output parsing --------------------------------------------------------

def test_parse_json_out_plain():
    assert _parse_json_out('{"a": 1}') == {"a": 1}


def test_parse_json_out_fenced():
    raw = "```json\n{\"a\": 1}\n```"
    assert _parse_json_out(raw) == {"a": 1}


def test_parse_json_out_embedded():
    raw = "Here you go:\n{\"a\": {\"b\": 2}}\nhope that helps"
    assert _parse_json_out(raw) == {"a": {"b": 2}}


def test_parse_json_out_garbage():
    assert _parse_json_out("not json at all") is None
    assert _parse_json_out("[1, 2, 3]") is None


# --- fragment extraction (monkeypatched LLM) -----------------------------------

def _fake_llm(payload: str):
    def _call(user_prompt: str, model: str) -> str:
        return payload
    return _call


def test_extract_fragment_happy_path(tmp_path, monkeypatch):
    doc = tmp_path / "design.md"
    doc.write_text("# Design\n\nThe AuthService handles tokens.\n", encoding="utf-8")
    payload = """
    {"nodes": [{"id": "token_flow", "label": "Token Flow", "summary": "how tokens move"}],
     "edges": [],
     "code_links": [{"concept_id": "token_flow", "code_name": "AuthService"}]}
    """
    monkeypatch.setattr("knowledgegraph.semantic._call_llm", _fake_llm(payload))

    frag = extract_semantic_fragment(doc, model="test-model")
    assert not frag.get("error")
    ids = {n["id"] for n in frag["nodes"]}
    types = {n["id"]: n["file_type"] for n in frag["nodes"]}
    assert "concept_token_flow" in ids
    assert types["concept_token_flow"] == "concept"
    # the file anchor is a *document* node, not a concept
    file_nodes = [n for n in frag["nodes"] if n["id"] == "design_md"]
    assert file_nodes and file_nodes[0]["file_type"] == "document"
    # code_links stay raw in the fragment
    assert frag["code_links"] == [{"concept_id": "concept_token_flow",
                                   "code_name": "AuthService"}]
    # every concept is anchored to the document
    assert any(e["relation"] == "contains" and e["target"] == "concept_token_flow"
               for e in frag["edges"])


def test_extract_fragment_drops_dangling_llm_edges(tmp_path, monkeypatch):
    doc = tmp_path / "bad.md"
    doc.write_text("content", encoding="utf-8")
    # edges reference a node the LLM never declared -> dropped in normalization
    payload = ('{"nodes": [{"id": "a", "label": "A"}], "edges": '
               '[{"source": "a", "target": "ghost"}]}')
    monkeypatch.setattr("knowledgegraph.semantic._call_llm", _fake_llm(payload))

    frag = extract_semantic_fragment(doc, model="test-model")
    assert not frag.get("error")
    # the dangling edge is gone; only the document->concept anchor remains
    assert not any(e["target"] == "concept_ghost" for e in frag["edges"])
    assert any(e["relation"] == "contains" and e["target"] == "concept_a"
               for e in frag["edges"])


def test_extract_fragment_survives_llm_failure(tmp_path, monkeypatch):
    doc = tmp_path / "x.md"
    doc.write_text("content", encoding="utf-8")

    def _boom(user_prompt: str, model: str) -> str:
        raise RuntimeError("api down")

    monkeypatch.setattr("knowledgegraph.semantic._call_llm", _boom)
    frag = extract_semantic_fragment(doc, model="test-model")
    assert frag == {"nodes": [], "edges": [], "error": "llm: RuntimeError: api down"}


# --- label index + apply pass ----------------------------------------------------

def _built_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("authservice", label="AuthService", file_type="code",
               source_file="auth.py", source_location="L3")
    G.add_node("dbpool", label="DatabasePool", file_type="code", source_file="db.py")
    G.add_node("amb1", label="Dup", file_type="code", source_file="a.py")
    G.add_node("amb2", label="Dup", file_type="code", source_file="b.py")
    return G


def test_build_code_label_index_and_ambiguity():
    idx = build_code_label_index(_built_graph())
    assert idx["authservice"] == "authservice"
    assert idx["databasepool"] == "dbpool"
    assert "dup" not in idx  # ambiguous labels never resolve


def test_apply_semantic_fragments_adds_nodes_edges_and_links():
    G = _built_graph()
    frag = {
        "nodes": [
            # the file anchor node (real fragments always include it)
            {"id": "docs_auth_md", "label": "auth.md", "file_type": "document",
             "source_file": "docs/auth.md", "source_location": "L1"},
            {"id": "concept_token_flow", "label": "Token Flow", "file_type": "concept",
             "source_file": "docs/auth.md", "source_location": None},
        ],
        "edges": [
            {"source": "docs_auth_md", "target": "concept_token_flow",
             "relation": "contains", "confidence": "EXTRACTED",
             "source_file": "docs/auth.md", "source_location": None, "weight": 1.0},
        ],
        "code_links": [
            {"concept_id": "concept_token_flow", "code_name": "AuthService"},
            {"concept_id": "concept_token_flow", "code_name": "NoSuchClass"},
            {"concept_id": "concept_token_flow", "code_name": "Dup"},  # ambiguous
        ],
    }
    G.add_node("docs_auth_md", label="auth.md", file_type="document",
               source_file="docs/auth.md")

    stats = apply_semantic_fragments(G, {"docs/auth.md": frag})
    assert G.has_node("concept_token_flow")
    assert G.has_edge("docs_auth_md", "concept_token_flow")
    # resolved link landed, unresolved ones did not
    assert G.has_edge("concept_token_flow", "authservice")
    assert G["concept_token_flow"]["authservice"]["confidence"] == "INFERRED"
    assert stats["code_links"] == 1 and stats["unresolved"] == 2


def test_apply_skips_error_and_invalid_fragments():
    G = _built_graph()
    err_frag = {"nodes": [], "edges": [], "error": "llm: boom"}
    bad_frag = {"nodes": [{"id": "a", "label": "A"}], "edges": []}
    stats = apply_semantic_fragments(G, {"x": err_frag, "y": bad_frag})
    assert G.number_of_nodes() == 4  # unchanged
    assert stats["concepts"] == 0


def test_god_nodes_exclude_semantic_concepts():
    G = nx.Graph()
    G.add_node("core", label="Core", file_type="code", source_file="a.py")
    G.add_node("concept_thing", label="Some Concept", file_type="concept",
               source_file="docs/x.md", source_location=None)
    G.add_edge("core", "concept_thing", relation="references", confidence="INFERRED")
    gods = god_nodes(G)
    assert [g["label"] for g in gods] == ["Core"]


# --- pipeline gating -------------------------------------------------------------

def test_pipeline_skips_semantic_without_key(tmp_path, monkeypatch):
    from knowledgegraph import config
    from knowledgegraph.pipeline import run_pipeline

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "notes.md").write_text("# Notes\n\nSome design notes here.\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    # semantic=True (default) but no key -> the pass must be skipped silently
    result = run_pipeline(corpus, semantic=True)
    assert result.semantic_concepts == 0 and result.semantic_code_links == 0
    assert result.nodes > 0  # the markdown still built its structural nodes


def test_pipeline_semantic_false_never_calls_llm(tmp_path, monkeypatch):
    from knowledgegraph.pipeline import run_pipeline

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "notes.md").write_text("# Notes\n\nSome design notes here.\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def _no_llm(*args, **kwargs):  # pragma: no cover - must never fire
        raise AssertionError("LLM must not be called with semantic=False")

    monkeypatch.setattr("knowledgegraph.semantic._call_llm", _no_llm)
    monkeypatch.setattr(config_module(), "llm_ready", lambda: True)
    result = run_pipeline(corpus, semantic=False)
    assert result.semantic_concepts == 0


def config_module():
    from knowledgegraph import config
    return config
