# Path helpers - the single place that knows where outputs live.
from __future__ import annotations

import os
from pathlib import Path

# Output directory name. Override with KG_OUT for worktrees / shared-output setups.
GRAPH_OUT_NAME = os.environ.get("KG_OUT", "kg-out")
GRAPH_OUT = GRAPH_OUT_NAME


def out_path(name: str) -> Path:
    """Path to ``name`` inside the output directory (relative to CWD)."""
    return Path(GRAPH_OUT) / name


def default_graph_json() -> Path:
    """Default location of the serialized knowledge graph."""
    return out_path("graph.json")


def default_report_md() -> Path:
    """Default location of the human-readable report."""
    return out_path("GRAPH_REPORT.md")


def default_cache_dir() -> Path:
    """Directory holding per-file extraction caches."""
    return out_path("cache")
