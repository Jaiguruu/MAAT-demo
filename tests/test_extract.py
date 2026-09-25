"""Tests for stage 2 - extractors (code AST, markdown, manifests)."""
from __future__ import annotations

from pathlib import Path

from knowledgegraph.extract import extract_file, extract_package_manifest
from knowledgegraph.extractors.markdown import extract_markdown
from knowledgegraph.validate import validate_extraction

PY_SRC = '''
from os import path
from models import User

class AuthService(BaseService):
    def login(self, username: str) -> User:
        return authenticate(username)

def authenticate(username):
    return User(username)
'''

JS_SRC = '''
import { Pool } from './pool';

export class Client {
    connect() {
        return pool.connect();
    }
}

function helper() {
    return 1;
}
'''

GO_SRC = '''
package main

import "fmt"

type Server struct{}

func (s *Server) Start() {
    fmt.Println("start")
}

func main() {
    s := &Server{}
    s.Start()
}
'''

RUST_SRC = '''
mod pool;

pub trait Runner {
    fn run(&self);
}

pub struct Engine;

impl Runner for Engine {
    fn run(&self) {}
}
'''


def _frag(path: Path, text: str) -> dict:
    path.write_text(text, encoding="utf-8")
    return extract_file(path)


def test_python_extraction(tmp_path):
    frag = _frag(tmp_path / "svc.py", PY_SRC)
    assert not validate_extraction(frag) or all(
        "does not match" not in e for e in validate_extraction(frag)
    )
    labels = {n["label"] for n in frag["nodes"]}
    assert "AuthService" in labels
    assert "login()" in labels
    assert "authenticate()" in labels
    rel = {(e["relation"]) for e in frag["edges"]}
    assert "imports" in rel
    assert "contains" in rel
    assert "inherits" in rel
    calls = [c["name"] for c in frag.get("raw_calls", [])]
    assert "authenticate" in calls
    # every node is anchored to the source file
    assert all(n["source_file"].endswith("svc.py") for n in frag["nodes"])


def test_js_extraction(tmp_path):
    frag = _frag(tmp_path / "client.js", JS_SRC)
    labels = {n["label"] for n in frag["nodes"]}
    assert "Client" in labels
    assert "helper()" in labels
    assert any(e["relation"] == "imports" for e in frag["edges"])


def test_go_extraction(tmp_path):
    frag = _frag(tmp_path / "main.go", GO_SRC)
    labels = {n["label"] for n in frag["nodes"]}
    assert "Server" in labels
    assert "Start()" in labels
    assert "main()" in labels
    assert any(e["relation"] == "imports" for e in frag["edges"])


def test_rust_extraction(tmp_path):
    frag = _frag(tmp_path / "engine.rs", RUST_SRC)
    labels = {n["label"] for n in frag["nodes"]}
    assert "Engine" in labels
    assert "Runner" in labels
    assert any(e["relation"] == "inherits" for e in frag["edges"])


def test_markdown_extraction(tmp_path):
    src = "# Top\n\n## Section One\n\nSee [other](./other.md) and [[Wiki Link]].\n"
    frag = _frag(tmp_path / "doc.md", src)
    labels = {n["label"] for n in frag["nodes"]}
    assert "Top" in labels and "Section One" in labels
    rels = {e["relation"] for e in frag["edges"]}
    assert "contains" in rels
    assert "references" in rels


def test_extraction_failure_is_soft(tmp_path):
    p = tmp_path / "bad.py"
    p.write_bytes(b"\xff\xfe\x00broken")
    frag = extract_file(p)
    assert "nodes" in frag and "edges" in frag  # never raises


def test_package_manifest(tmp_path):
    p = tmp_path / "package.json"
    p.write_text('{"name": "myapp", "dependencies": {"express": "^4"}}', encoding="utf-8")
    frag = extract_package_manifest(p)
    assert frag is not None
    rels = {e["relation"] for e in frag["edges"]}
    assert "depends_on" in rels
    labels = {n["label"] for n in frag["nodes"]}
    assert "myapp" in labels and "express" in labels
