# Input validation and sanitization. All externally-supplied strings pass
# through this module before they reach the graph or an LLM prompt.
from __future__ import annotations

import re
from urllib.parse import urlparse

_LABEL_MAX = 256


def sanitize_label(label: str) -> str:
    """Strip control characters, cap length, neutralize HTML-significant chars."""
    if not isinstance(label, str):
        label = "" if label is None else str(label)
    label = re.sub(r"[\x00-\x1f\x7f]", "", label)
    label = label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return label[:_LABEL_MAX]


def sanitize_metadata(d: dict) -> dict:
    """Sanitize every string value in a metadata dict (used on LLM output)."""
    out = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = sanitize_label(v)
        elif isinstance(v, dict):
            out[k] = sanitize_metadata(v)
        else:
            out[k] = v
    return out


def validate_url(url: str) -> str:
    """Accept only http/https URLs; raise ValueError otherwise."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"only http(s) URLs are supported: {url!r}")
    return url


_FILE_TYPE_SYNONYMS = {
    "document": "document",
    "markdown": "document",
    "text": "document",
    "doc": "document",
    "image": "image",
    "code": "code",
    "source": "code",
    "tool": "code",
    "library": "code",
    "concept": "concept",
    "pattern": "concept",
    "principle": "concept",
    "tech": "concept",
    "technology": "concept",
    "framework": "concept",
    "rationale": "rationale",
    "note": "rationale",
    "paper": "paper",
}


def normalize_file_type(file_type: str | None) -> str:
    """Map LLM-emitted file types onto the canonical set (fallback: concept)."""
    if not file_type:
        return "concept"
    return _FILE_TYPE_SYNONYMS.get(str(file_type).strip().lower(), "concept")
