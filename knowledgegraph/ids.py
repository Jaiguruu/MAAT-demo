# Canonical node-ID normalization.
#
# Every producer in the pipeline (AST extractors, the semantic extractor, the
# graph builder) must agree on node IDs or one real-world entity splits into
# disconnected ghost nodes. The recipe lives here and nowhere else:
#
#   1. NFKC-normalize (composed/decomposed Unicode forms collapse to one form)
#   2. replace runs of non-word characters with a single underscore
#      (re.UNICODE so CJK/Cyrillic/accented Latin letters survive)
#   3. collapse repeated underscores
#   4. strip leading/trailing underscores
#   5. casefold
#
# All operations are idempotent: normalize(normalize(s)) == normalize(s).
from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_id", "make_id"]


def normalize_id(s: str) -> str:
    r"""Normalize a single ID string to its canonical form."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s)
    return s.strip("_").casefold()


def make_id(*parts: str) -> str:
    """Build a canonical node ID from one or more name parts.

    Parts are joined with ``_`` (after stripping stray ``_``/``.`` edges from
    each part) and then run through :func:`normalize_id`.
    """
    return normalize_id("_".join(p.strip("_.") for p in parts if p))
