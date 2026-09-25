"""Per-file extraction cache - skip unchanged files on re-run.

Every file is fingerprinted by SHA-256 of its content. A stat index
(size + mtime_ns -> hash) avoids re-reading files that have not changed at
all, the same trade-off make(1) takes. Re-extraction only touches new or
modified files; unchanged fragments are replayed from cache.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from knowledgegraph.paths import default_cache_dir

_stat_index: dict[str, dict] = {}
_stat_index_loaded = False


def _index_path(root: Path) -> Path:
    return Path(root).resolve() / ".kg" / "stat-index.json"


def _load_index(root: Path) -> None:
    global _stat_index, _stat_index_loaded
    if _stat_index_loaded:
        return
    p = _index_path(root)
    if p.exists():
        try:
            _stat_index = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _stat_index = {}
    _stat_index_loaded = True


def save_index(root: Path) -> None:
    p = _index_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_stat_index), encoding="utf-8")
    except OSError:
        pass


def content_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _fast_hash(path: Path, stat: object) -> str | None:
    """Reuse the cached hash when size+mtime_ns are unchanged."""
    key = str(path.resolve())
    entry = _stat_index.get(key)
    if entry and entry.get("size") == stat.st_size and entry.get("mtime_ns") == stat.st_mtime_ns:
        return entry.get("hash")
    return None


def split_cached(
    paths: list[str],
    root: Path,
    cache_dir: Path | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """Split file paths into (cached_fragments, uncached_paths)."""
    _load_index(root)
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
    cached: dict[str, dict] = {}
    uncached: list[str] = []
    for p in paths:
        path = Path(p)
        try:
            stat = path.stat()
        except OSError:
            continue
        digest = _fast_hash(path, stat) or content_hash(path)
        if not digest:
            continue
        _stat_index[str(path.resolve())] = {
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "hash": digest,
        }
        entry_file = cache_dir / "ast" / f"{digest}.json"
        if entry_file.exists():
            try:
                cached[p] = json.loads(entry_file.read_text(encoding="utf-8"))
                continue
            except (json.JSONDecodeError, OSError):
                pass
        uncached.append(p)
    return cached, uncached


def save_cached(path: str, fragment: dict, cache_dir: Path | None = None) -> None:
    """Persist one extraction fragment under its content hash."""
    key = str(Path(path).resolve())
    entry = _stat_index.get(key)
    if not entry:
        return
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
    entry_file = cache_dir / "ast" / f"{entry['hash']}.json"
    try:
        entry_file.parent.mkdir(parents=True, exist_ok=True)
        entry_file.write_text(json.dumps(fragment, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
