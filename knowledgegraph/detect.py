# Stage 1 - file discovery and type classification.
#
# Walks a corpus root, applies ignore rules (gitignore syntax), skips secrets
# and known noise, and buckets every remaining file by type. The output of this
# stage fully determines what the later stages will even look at.
from __future__ import annotations

import fnmatch
import os
import re
from enum import Enum
from pathlib import Path


class FileType(str, Enum):
    CODE = "code"
    DOCUMENT = "document"
    PAPER = "paper"
    IMAGE = "image"


CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
    ".rb", ".php", ".cs", ".kt", ".scala", ".swift", ".lua", ".sh", ".bash",
    ".sql", ".json", ".yaml", ".yml", ".toml",
}
DOC_EXTENSIONS = {".md", ".mdx", ".qmd", ".txt", ".rst", ".html"}
PAPER_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# Directories that are never architecturally meaningful.
SKIP_DIRS = {
    "venv", ".venv", "env", ".env", "node_modules", "__pycache__", ".git",
    "dist", "build", "target", "out", "site-packages",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".eggs",
    "kg-out",  # never treat our own output as source input
    ".kg",    # extraction cache / stat index
    ".next", ".nuxt", ".turbo", ".svelte-kit", ".idea", ".cache",
    ".worktrees", "__snapshots__",
}

# Lockfiles and other generated artifacts.
SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
    "poetry.lock", "Gemfile.lock", "composer.lock", "go.sum", "uv.lock",
}

# Parent directories whose contents are assumed sensitive.
SENSITIVE_DIRS = frozenset({
    ".ssh", ".gnupg", ".aws", ".gcloud", "secrets", ".secrets", "credentials",
})

# Filename patterns that indicate credential stores.
SENSITIVE_PATTERNS = [
    re.compile(r"(^|[\\/])\.(env|envrc)(\.|$)", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx|cert|crt|der|p8)$", re.IGNORECASE),
    re.compile(r"(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$"),
    re.compile(r"(\.netrc|\.pgpass|\.htpasswd)$", re.IGNORECASE),
]

# Generic keywords that are only treated as secret-store names when load-bearing
# (i.e. the keyword ends the stem, or the name is short). "tokenizer.py" and
# "token-economics.md" are topic files, not credential stores.
_GENERIC_KEYWORD_PATTERNS = [
    re.compile(r"(?<![a-zA-Z0-9])(credential|secret|passwd|password|private_key|creds|api_key|apikey|auth_key)s?(?![a-zA-Z])", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z0-9])tokens?(?![a-zA-Z])", re.IGNORECASE),
]
_WORD_SPLIT = re.compile(r"[-_\s]+")

# Signals that a text file is a converted academic paper (scanned on the first
# 3000 chars; need at least PAPER_SIGNAL_THRESHOLD hits to reclassify).
_PAPER_SIGNALS = [
    re.compile(r"\barxiv\b", re.IGNORECASE),
    re.compile(r"\bdoi\s*:", re.IGNORECASE),
    re.compile(r"\babstract\b", re.IGNORECASE),
    re.compile(r"\bproceedings\b", re.IGNORECASE),
    re.compile(r"\bjournal\b", re.IGNORECASE),
    re.compile(r"\bpreprint\b", re.IGNORECASE),
    re.compile(r"\\cite\{"),
    re.compile(r"\[\d+\]"),
    re.compile(r"\d{4}\.\d{4,5}"),  # arXiv-style ID
]
_PAPER_SIGNAL_THRESHOLD = 3

_VCS_MARKERS = (".git", ".hg", ".svn")


def _generic_keyword_hit(name: str) -> bool:
    """True if a generic secret keyword appears load-bearing in the filename."""
    stem = name.lstrip(".").split(".")[0]
    for pat in _GENERIC_KEYWORD_PATTERNS:
        hit = False
        for m in pat.finditer(stem):
            hit = True
            if m.end() == len(stem):  # keyword ends the stem -> names the contents
                return True
        if hit and len([w for w in _WORD_SPLIT.split(stem) if w]) <= 2:
            return True
    return False


def is_sensitive(path: Path) -> bool:
    """Return True if this file likely contains secrets and must be skipped."""
    if any(part in SENSITIVE_DIRS for part in path.parts[:-1]):
        return True
    if any(p.search(path.name) for p in SENSITIVE_PATTERNS):
        return True
    return _generic_keyword_hit(path.name)


def looks_like_paper(path: Path) -> bool:
    """Heuristic: does this text file read like an academic paper?"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:3000]
        hits = sum(1 for pattern in _PAPER_SIGNALS if pattern.search(text))
        return hits >= _PAPER_SIGNAL_THRESHOLD
    except Exception:
        return False


def classify_file(path: Path) -> FileType | None:
    """Map one file to its pipeline type, or None when it carries no content."""
    ext = path.suffix.lower()
    if not ext:
        return None
    if ext in CODE_EXTENSIONS:
        return FileType.CODE
    if ext in PAPER_EXTENSIONS:
        return FileType.PAPER
    if ext in IMAGE_EXTENSIONS:
        return FileType.IMAGE
    if ext in DOC_EXTENSIONS:
        return FileType.PAPER if looks_like_paper(path) else FileType.DOCUMENT
    return None


def parse_ignore_line(raw: str) -> str:
    """Parse one gitignore-syntax line; returns '' for blanks and comments."""
    line = raw.rstrip("\n\r").strip()
    if not line or line.startswith("#"):
        return ""
    line = re.sub(r"\s+#+[^\\].*$", "", line)  # inline comments
    line = line.replace("\\#", "#")
    line = re.sub(r"(?<!\\) +$", "", line)     # trailing spaces
    return line


def load_ignore_patterns(root: Path) -> list[tuple[Path, str]]:
    """Read .gitignore + .kgignore anchored per directory, outer-first.

    .kgignore patterns are read last so they win on conflict.
    """
    root = root.resolve()
    ceiling = root
    current = root
    while True:
        if any((current / m).exists() for m in _VCS_MARKERS):
            ceiling = current
            break
        if current.parent == current:
            break
        current = current.parent
    dirs: list[Path] = []
    current = root
    while True:
        dirs.append(current)
        if current == ceiling:
            break
        current = current.parent
    dirs.reverse()

    patterns: list[tuple[Path, str]] = []
    for d in dirs:
        for fname in (".gitignore", ".kgignore"):
            f = d / fname
            if f.exists():
                for raw in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = parse_ignore_line(raw)
                    if line:
                        patterns.append((d, line))
    return patterns


def _matches(rel: str, name: str, p: str) -> bool:
    if fnmatch.fnmatch(rel, p):
        return True
    if fnmatch.fnmatch(name, p):
        return True
    parts = rel.split("/")
    for i, part in enumerate(parts):
        if fnmatch.fnmatch(part, p):
            return True
        if fnmatch.fnmatch("/".join(parts[:i + 1]), p):
            return True
    return False


def is_ignored(path: Path, root: Path, patterns: list[tuple[Path, str]]) -> bool:
    """Gitignore-style last-match-wins evaluation with ! negation."""
    if not patterns:
        return False
    try:
        rel = str(path.relative_to(root)).replace(os.sep, "/")
    except ValueError:
        rel = path.name
    result = False
    for anchor, pattern in patterns:
        negated = pattern.startswith("!")
        raw = pattern[1:] if negated else pattern
        raw = raw.strip("/")
        if not raw:
            continue
        matched = _matches(rel, path.name, raw)
        if anchor != root and not matched:
            try:
                matched = _matches(
                    str(path.relative_to(anchor)).replace(os.sep, "/"), path.name, raw
                )
            except ValueError:
                pass
        if matched:
            result = not negated
    return result


def _is_noise_dir(part: str) -> bool:
    if part in SKIP_DIRS:
        return True
    if part.endswith("_venv") or part.endswith("_env") or part.endswith(".egg-info"):
        return True
    return False


def detect(root: Path, extra_excludes: list[str] | None = None) -> dict:
    """Walk ``root`` and bucket files by type.

    Returns a dict with per-type file lists, corpus health stats, and the lists
    of skipped sensitive files and unclassifiable files.
    """
    root = root.resolve()
    files: dict[FileType, list[str]] = {t: [] for t in FileType}
    total_words = 0
    skipped_sensitive: list[str] = []
    unclassified: list[str] = []
    seen: set[Path] = set()

    ignore_patterns = load_ignore_patterns(root)
    if extra_excludes:
        for pat in extra_excludes:
            line = parse_ignore_line(pat)
            if line:
                ignore_patterns.append((root, line))

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Prune noise directories in-place so os.walk never descends.
        dirnames[:] = [
            d for d in dirnames
            if not _is_noise_dir(d) and not is_ignored(here / d, root, ignore_patterns)
        ]
        for fname in filenames:
            path = here / fname
            if path in seen:
                continue
            seen.add(path)
            if path.name in SKIP_FILES or fname.endswith(".lock"):
                continue
            if path.name in (".gitignore", ".kgignore"):
                continue
            if is_ignored(path, root, ignore_patterns):
                continue
            if is_sensitive(path):
                skipped_sensitive.append(str(path.relative_to(root)))
                continue
            ftype = classify_file(path)
            if ftype is None:
                unclassified.append(str(path.relative_to(root)))
                continue
            files[ftype].append(str(path))
            if ftype in (FileType.DOCUMENT, FileType.PAPER):
                try:
                    total_words += len(path.read_text(encoding="utf-8", errors="ignore").split())
                except Exception:
                    pass

    total_files = sum(len(v) for v in files.values())
    warning = None
    if total_files == 0:
        warning = "No indexable files found in this folder."
    elif total_words < 5_000 and total_files <= 6:
        warning = "Small corpus - the graph adds structural clarity, not token savings."

    return {
        "root": str(root),
        "files": {t.value: v for t, v in files.items()},
        "total_files": total_files,
        "total_words": total_words,
        "warning": warning,
        "skipped_sensitive": skipped_sensitive,
        "unclassified": unclassified,
    }
