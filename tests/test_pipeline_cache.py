"""End-to-end pipeline tests plus cache behavior."""
from __future__ import annotations

import json

from knowledgegraph import cache as kg_cache
from knowledgegraph.build import load_graph
from knowledgegraph.pipeline import run_pipeline
from knowledgegraph.query import query


def _write_corpus(root):
    (root / "a.py").write_text(
        "from b import helper\n"
        "def main():\n    return helper()\n",
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        "def helper():\n    return 42\n",
        encoding="utf-8",
    )
    (root / "notes.md").write_text(
        "# Notes\n\n## Design\nmain() calls helper in b.py.\n",
        encoding="utf-8",
    )


def test_full_pipeline_end_to_end(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_corpus(corpus)
    monkeypatch.chdir(tmp_path)  # outputs go to tmp kg-out/

    result = run_pipeline(corpus, semantic=False)  # offline, no LLM
    assert result.nodes > 0 and result.edges > 0
    assert result.graph_path.exists()
    assert result.report_path.exists()
    report = result.report_path.read_text(encoding="utf-8")
    assert "# Graph Report" in report

    G = load_graph(result.graph_path)
    labels = {d.get("label") for _, d in G.nodes(data=True)}
    assert "main()" in labels or "helper()" in labels

    res = query(G, "how does main use helper")
    assert res.nodes


def test_update_replays_cache(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_corpus(corpus)
    monkeypatch.chdir(tmp_path)

    first = run_pipeline(corpus, update=True, semantic=False)
    assert first.extracted_files == 3 and first.cached_files == 0

    second = run_pipeline(corpus, update=True, semantic=False)
    assert second.cached_files == 3 and second.extracted_files == 0
    assert second.nodes == first.nodes and second.edges == first.edges

    # touch one file -> only it re-extracts
    (corpus / "b.py").write_text(
        "def helper():\n    return 43\n",
        encoding="utf-8",
    )
    third = run_pipeline(corpus, update=True, semantic=False)
    assert third.cached_files == 2 and third.extracted_files == 1


def test_cache_split_and_content_hash(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("x = 1\n", encoding="utf-8")
    h1 = kg_cache.content_hash(p)
    assert len(h1) == 64
    p.write_text("x = 2\n", encoding="utf-8")
    assert kg_cache.content_hash(p) != h1


def test_stat_index_persists(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("x = 1\n", encoding="utf-8")
    cached, uncached = kg_cache.split_cached([str(p)], tmp_path)
    assert str(p) in uncached
    kg_cache.save_index(tmp_path)
    index = json.loads((tmp_path / ".kg" / "stat-index.json").read_text(encoding="utf-8"))
    assert any("f.py" in k for k in index)
