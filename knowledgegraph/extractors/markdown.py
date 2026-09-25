"""Markdown extraction - headings become nodes, links become references edges.

Headings (## Level) map to nodes with a ``contains`` chain (file -> h1 -> h2
-> ...). Inline ``[text](target.md)`` links and ``[[wikilinks]]`` become
``references`` edges between document nodes, so doc-to-doc structure survives
into the graph.
"""
from __future__ import annotations

import re
from pathlib import Path

from knowledgegraph.extractors.engine import _make_id, file_node

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_PAPER_SIGNALS = 3  # minimum academic signals before reclassifying as paper


def _looks_like_paper(text: str) -> bool:
    signals = [
        r"\barxiv\b", r"\bdoi\s*:", r"\babstract\b", r"\bproceedings\b",
        r"\bjournal\b", r"\bpreprint\b", r"\\cite\{", r"\[\d+\]",
    ]
    head = text[:3000]
    return sum(1 for p in signals if re.search(p, head, re.IGNORECASE)) >= _PAPER_SIGNALS


def extract_markdown(path: Path) -> dict:
    """Extract heading nodes and link edges from one markdown-ish document."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    file_type = "paper" if _looks_like_paper(text) else "document"
    str_path = str(path)
    file_nid = _make_id(path.name)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def add_node(nid: str, label: str, location: str | None) -> None:
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"id": nid, "label": label, "file_type": file_type,
                      "source_file": str_path, "source_location": location})

    def add_edge(src: str, tgt: str, relation: str, location: str | None) -> None:
        edges.append({"source": src, "target": tgt, "relation": relation,
                      "confidence": "EXTRACTED", "source_file": str_path,
                      "source_location": location, "weight": 1.0})

    # heading stack: index -> node id of the most recent heading at that level
    stack: dict[int, str] = {}
    prev_level = 0
    prev_nid = file_nid

    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        if not title:
            continue
        nid = _make_id(path.stem, title)
        add_node(nid, title, f"L{lineno}")
        add_edge(file_nid, nid, "contains", f"L{lineno}")
        # link to the nearest heading above (level - 1 .. 1)
        parent = stack.get(level - 1) or (prev_nid if level > prev_level else None)
        if parent and parent != nid:
            add_edge(parent, nid, "contains", f"L{lineno}")
        # pop deeper levels off the stack
        for l in list(stack):
            if l >= level:
                del stack[l]
        stack[level] = nid
        prev_level = level
        prev_nid = nid

    # links
    for m in _MD_LINK_RE.finditer(text):
        label, target = m.group(1).strip(), m.group(2).strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        target = target.split("#")[0].split("?")[0]
        if not target:
            continue
        tgt_name = Path(target).name
        tgt_nid = _make_id(tgt_name)
        add_node(tgt_nid, tgt_name, None)
        add_edge(file_nid, tgt_nid, "references", None)

    for m in _WIKILINK_RE.finditer(text):
        label = m.group(1).split("|")[0].strip()
        if not label:
            continue
        tgt_nid = _make_id(label)
        add_node(tgt_nid, label, None)
        add_edge(file_nid, tgt_nid, "references", None)

    nodes.insert(0, file_node(file_nid, path.name, str_path, file_type=file_type))
    return {"nodes": nodes, "edges": edges}
