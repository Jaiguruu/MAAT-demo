"""Helpers shared by all extractors: ID construction, text reading, node/edge factories."""
from __future__ import annotations

from pathlib import Path

from knowledgegraph.ids import make_id
from knowledgegraph.security import sanitize_label

__all__ = ["_make_id", "_read_text", "_file_node_id", "file_node", "edge"]


def _make_id(*parts: str) -> str:
    return make_id(*parts)


def _read_text(node, source: bytes) -> str:
    """Read the source text covered by a tree-sitter node."""
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _file_node_id(rel_path: Path) -> str:
    """File-level node ID: ``{parent_dir}_{stem}`` for nested files, bare
    ``{stem}`` for top-level files. Kept in one place so every producer of
    file-level nodes agrees on the identity."""
    parts = Path(rel_path).parts
    if len(parts) >= 2:
        return make_id(parts[-2], Path(parts[-1]).stem)
    return make_id(Path(parts[-1]).stem)


def file_node(nid: str, label: str, source_file: str, file_type: str = "code") -> dict:
    """Build the anchor node every file contributes to the graph."""
    return {"id": nid, "label": sanitize_label(label), "file_type": file_type,
            "source_file": source_file, "source_location": "L1"}


def edge(src: str, tgt: str, relation: str, confidence: str = "EXTRACTED",
         source_file: str | None = None, source_location: str | None = None,
         weight: float = 1.0, **extra) -> dict:
    """Build one canonical edge dict."""
    d = {"source": src, "target": tgt, "relation": relation, "confidence": confidence,
         "source_file": source_file, "source_location": source_location, "weight": weight}
    d.update(extra)
    return d
