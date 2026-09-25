"""Deterministic structural extraction from source and document files.

Each extractor turns one file into ``{"nodes": [...], "edges": [...]}`` using
the canonical extraction schema. All node IDs go through ``ids.make_id`` so
that independently produced fragments can be merged safely later.

The dispatch is purely suffix-based and fully deterministic: no LLM calls, no
network, nothing leaves the machine.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from knowledgegraph.extractors.code_extractor import extract_code
from knowledgegraph.extractors.markdown import extract_markdown
from knowledgegraph.ids import make_id

_RECURSION_LIMIT = 10_000


def _raise_recursion_limit() -> None:
    if sys.getrecursionlimit() < _RECURSION_LIMIT:
        sys.setrecursionlimit(_RECURSION_LIMIT)


def extract_file(path: Path) -> dict:
    """Extract one file. Never raises: failures return an empty fragment with
    an ``error`` key so one bad file cannot kill a corpus run."""
    path = Path(path)
    _raise_recursion_limit()
    try:
        ext = path.suffix.lower()
        if ext in (".md", ".mdx", ".qmd"):
            return extract_markdown(path)
        return extract_code(path)
    except RecursionError:
        print(f"  warning: skipped {path} (recursion limit)", file=sys.stderr, flush=True)
        return {"nodes": [], "edges": [], "error": "recursion_limit_exceeded"}
    except Exception as e:
        print(f"  warning: skipped {path} ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        return {"nodes": [], "edges": [], "error": f"{type(e).__name__}: {e}"}


def _extract_worker(path_str: str) -> dict:
    return extract_file(Path(path_str))


def extract_all(paths: list[str], max_workers: int | None = None) -> list[dict]:
    """Extract a list of files in parallel (process pool - bypasses the GIL).

    Results keep input order so downstream caching and merging stay stable.
    """
    if not paths:
        return []
    workers = max_workers or min(8, (len(paths) + 3) // 4) or 1
    results: list[dict | None] = [None] * len(paths)
    if workers <= 1:
        return [extract_file(Path(p)) for p in paths]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_extract_worker, p): i for i, p in enumerate(paths)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {"nodes": [], "edges": [], "error": f"worker: {type(e).__name__}: {e}"}
    return [r or {"nodes": [], "edges": [], "error": "no result"} for r in results]


# --- package-manifest extraction (pyproject.toml / package.json / go.mod) ----


def extract_package_manifest(path: Path) -> dict | None:
    """Extract a package node + depends_on edges from a manifest file.

    Returns None when the file is not a recognized manifest.
    """
    name = path.name.lower()
    try:
        if name == "pyproject.toml":
            try:
                import tomllib
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except ImportError:
                return None
            proj = data.get("project", {})
            pkg = proj.get("name") or data.get("tool", {}).get("poetry", {}).get("name")
            if not pkg:
                return None
            deps = list((proj.get("dependencies") or {}).keys())
        elif name == "package.json":
            data = json.loads(path.read_text(encoding="utf-8"))
            pkg = data.get("name")
            if not pkg:
                return None
            deps = list((data.get("dependencies") or {}).keys())
        elif name == "go.mod":
            text = path.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
            if not m:
                return None
            pkg = m.group(1)
            deps = re.findall(r"^\t([\w./-]+) v", text, re.MULTILINE)
        else:
            return None
    except Exception:
        return None

    pkg_nid = make_id("pkg", pkg)
    file_nid = make_id(path.name)
    nodes = [
        {"id": file_nid, "label": path.name, "file_type": "code",
         "source_file": str(path), "source_location": "L1"},
        {"id": pkg_nid, "label": str(pkg), "file_type": "code",
         "source_file": str(path), "source_location": "L1"},
    ]
    edges = [{"source": file_nid, "target": pkg_nid, "relation": "defines_package",
              "confidence": "EXTRACTED", "source_file": str(path), "source_location": "L1",
              "weight": 1.0}]
    for dep in deps[:60]:
        dep_nid = make_id("pkg", dep)
        nodes.append({"id": dep_nid, "label": str(dep), "file_type": "code",
                      "source_file": str(path), "source_location": None})
        edges.append({"source": pkg_nid, "target": dep_nid, "relation": "depends_on",
                      "confidence": "EXTRACTED", "source_file": str(path),
                      "source_location": "L1", "weight": 1.0})
    return {"nodes": nodes, "edges": edges}
