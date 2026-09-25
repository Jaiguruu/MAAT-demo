"""Tree-sitter loader shared by all AST extractors.

Uses the ``tree-sitter-language-pack`` so every supported grammar comes from a
single dependency with prebuilt wheels, instead of one package per language.
"""
from __future__ import annotations

from functools import lru_cache

from tree_sitter_language_pack import get_parser


@lru_cache(maxsize=None)
def get_language(name: str):
    """Return the language object for a grammar name (e.g. ``python``)."""
    return get_language(name)


@lru_cache(maxsize=None)
def get_parser_for(name: str):
    """Return a fresh tree-sitter Parser for a grammar name."""
    return get_parser(name)


def parse_bytes(grammar: str, source: bytes):
    """Parse source bytes with the named grammar; returns the syntax tree root."""
    parser = get_parser_for(grammar)
    return parser.parse(source)
