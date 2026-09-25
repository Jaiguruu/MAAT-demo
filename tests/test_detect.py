"""Tests for stage 1 - file discovery and classification."""
from __future__ import annotations

from pathlib import Path

from knowledgegraph.detect import classify_file, detect, is_sensitive


def _mk(root: Path, rel: str, content: str = "x = 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_classify_by_extension(tmp_path):
    assert classify_file(Path("a.py")) == "code"
    assert classify_file(Path("a.ts")) == "code"
    assert classify_file(Path("a.go")) == "code"
    assert classify_file(Path("a.rs")) == "code"
    assert classify_file(Path("a.md")) == "document"
    assert classify_file(Path("a.pdf")) == "paper"
    assert classify_file(Path("a.png")) == "image"
    assert classify_file(Path("a.xyz")) is None
    assert classify_file(Path("Makefile")) is None  # no extension


def test_sensitive_parent_dirs(tmp_path):
    assert is_sensitive(Path("repo/.env"))
    assert is_sensitive(Path("repo/server.pem"))
    assert is_sensitive(Path("repo/.ssh/id_rsa"))
    assert is_sensitive(Path("repo/secrets/api_key.json"))
    assert is_sensitive(Path("repo/api_token.txt"))
    # load-bearing keyword only: topic files must survive
    assert not is_sensitive(Path("repo/tokenizer.py"))
    assert not is_sensitive(Path("repo/docs/token-economics-of-recall.md"))


def test_paper_reclassification(tmp_path):
    paper = _mk(
        tmp_path,
        "notes.md",
        "Abstract\nWe propose a method. arXiv preprint, see [1] and [2].\n",
    )
    assert classify_file(paper) == "paper"


def test_detect_buckets_and_prunes(tmp_path):
    _mk(tmp_path, "src/app.py")
    _mk(tmp_path, "docs/readme.md")
    _mk(tmp_path, "node_modules/lib.js")
    _mk(tmp_path, ".venv/site.py")
    _mk(tmp_path, "creds.json", "{}")
    result = detect(tmp_path)
    assert any(p.endswith("app.py") for p in result["files"]["code"])
    assert any(p.endswith("readme.md") for p in result["files"]["document"])
    flat = [p for v in result["files"].values() for p in v]
    assert not any("node_modules" in p for p in flat)
    assert not any(".venv" in p for p in flat)
    assert any("creds.json" in p for p in result["skipped_sensitive"])


def test_gitignore_respected(tmp_path):
    _mk(tmp_path, ".gitignore", "generated/\n")
    _mk(tmp_path, "generated/out.py")
    _mk(tmp_path, "keep.py")
    result = detect(tmp_path)
    flat = [p for v in result["files"].values() for p in v]
    assert any("keep.py" in p for p in flat)
    assert not any("generated/out.py" in p for p in flat)
