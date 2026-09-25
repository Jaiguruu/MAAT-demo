"""Tree-sitter loader backed by the single-dependency grammar pack."""
from __future__ import annotations

from functools import lru_cache

from tree_sitter_language_pack import get_parser


@lru_cache(maxsize=None)
def get_parser_for(name: str):
    """Return a tree-sitter Parser for a grammar name (e.g. ``python``)."""
    return get_parser(name)


def parse_bytes(grammar: str, source: bytes):
    """Parse source bytes with the named grammar; returns the syntax tree."""
    return get_parser_for(grammar).parse(source)
