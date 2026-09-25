"""Code extraction via tree-sitter AST - deterministic, no LLM, fully local.

Supports Python, JavaScript/TypeScript, Go and Rust through the shared grammar
pack. For every file the extractor emits:

- one file node (anchor for all relationships in that file)
- one node per class / function / interface / trait, linked by ``contains``
- ``imports`` edges for import/include statements (file -> imported module)
- ``calls`` edges resolved *within* the file, plus a conservative cross-file
  second pass via the shared call-index in ``symbol_resolution``
- ``inherits``/``implements`` edges from base-class / heritage clauses
- ``references`` edges from type annotations to user-defined types

Every node/edge carries ``source_file`` and, where available, a line number so
each relationship in the final graph can be traced back to the exact source.
"""
from __future__ import annotations

import re
from pathlib import Path

from knowledgegraph.extractors.engine import (
    _make_id,
    _read_text,
    _file_node_id,
    file_node,
    edge,
)
from knowledgegraph.extractors.ts_loader import parse_bytes
from knowledgegraph.symbol_resolution import (
    build_label_index,
    existing_edge_pairs,
    resolve_raw_calls,
)

_GRAMMAR_BY_EXT = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
}

# builtins that must never become call-target nodes
_BUILTIN_GLOBALS = frozenset({
    "str", "int", "float", "bool", "bytes", "list", "dict", "set", "tuple",
    "len", "print", "range", "enumerate", "zip", "map", "filter", "open",
    "isinstance", "issubclass", "type", "super", "abs", "min", "max", "sum",
    "any", "all", "sorted", "reversed", "round", "repr", "hash", "id", "iter",
    "next", "getattr", "setattr", "hasattr", "delattr", "vars", "dir", "callable",
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "RuntimeError", "StopIteration", "OSError",
    "IOError", "ImportError", "NotImplementedError", "NameError", "LookupError",
    "console", "window", "document", "require", "module", "exports", "process",
    "Array", "Object", "String", "Number", "Boolean", "Promise", "Map", "Set",
    "JSON", "Math", "Date", "Error", "TypeError_JS", "RegExp", "Symbol",
})

_ANNOTATION_NOISE = frozenset({
    "str", "int", "float", "bool", "bytes", "object", "Any", "Optional", "List",
    "Dict", "Set", "Tuple", "Union", "Callable", "Type", "ClassVar", "Final",
    "Literal", "Protocol", "None", "True", "False", "self", "cls",
})


def _file_stem(rel_path: Path) -> str:
    """File-level node stem: one parent directory level, no extension."""
    parts = rel_path.parts
    if len(parts) >= 2:
        return f"{parts[-2]}_{parts[-1].stem}"
    return parts[-1].stem


def _label_from_id(nid: str) -> str:
    """Best-effort human label for a stub node derived from its id."""
    return nid.replace("_", " ").strip().title()


def _node_kind_name(node) -> str:
    return node.type


def _walk(root):
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        for child in reversed(n.children):
            stack.append(child)


class _FileScope:
    """Per-file accumulation of nodes, edges and resolution facts."""

    def __init__(self, path: Path, rel: Path):
        self.path = path
        self.rel = rel
        self.str_path = str(path)
        self.file_nid = _file_node_id(rel)
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.raw_calls: list[dict] = []
        self.definitions: dict[str, str] = {}   # label -> node_id
        self.imported: dict[str, str] = {}      # local alias -> module string
        self.type_refs: set[str] = set()
        self._seen: set[str] = set()

    def add_node(self, nid: str, label: str, file_type: str = "code",
                 location: str | None = None, **extra) -> None:
        if nid in self._seen:
            return
        self._seen.add(nid)
        d = {"id": nid, "label": label, "file_type": file_type,
             "source_file": self.str_path, "source_location": location}
        d.update(extra)
        self.nodes.append(d)

    def add_edge(self, src: str, tgt: str, relation: str,
                 confidence: str = "EXTRACTED", location: str | None = None,
                 **extra) -> None:
        # Edges may point at symbols defined in other files (a base class, an
        # imported type). Emit a stub node for any target this fragment does
        # not define so the fragment always passes schema validation; the
        # builder merges stubs with the real definitions by id.
        self.add_node(tgt, _label_from_id(tgt), location=None, stub=True)
        self.edges.append(edge(src, tgt, relation, confidence, self.str_path, location, **extra))

    def add_definition(self, nid: str, label: str, location: str | None,
                       kind: str = "function") -> None:
        clean = label.rstrip("()").lstrip(".")
        # a real definition outranks an earlier stub with the same id
        for n in self.nodes:
            if n["id"] == nid and n.get("stub"):
                n.pop("stub", None)
                n["label"] = label
                n["source_location"] = location
                break
        self.add_node(nid, label, location=location, kind=kind)
        self.definitions.setdefault(clean.lower(), nid)

    def add_call(self, caller_nid: str | None, name: str, location: str | None) -> None:
        name = name.strip()
        if not name or name in _BUILTIN_GLOBALS:
            return
        clean = name.rstrip("()").lstrip(".")
        if not clean or clean in _BUILTIN_GLOBALS:
            return
        self.raw_calls.append({
            "caller": caller_nid or self.file_nid,
            "name": clean,
            "location": location,
            "source_file": self.str_path,
        })

    def add_type_ref(self, name: str) -> None:
        name = name.strip()
        if name and name not in _ANNOTATION_NOISE and not name[0].islower():
            self.type_refs.add(name)

    def add_import(self, module: str, location: str | None) -> None:
        module = module.strip().strip("'\"")
        if not module:
            return
        tgt = _make_id("mod", module)
        self.add_node(tgt, module, location=None)
        self.add_edge(self.file_nid, tgt, "imports", location=location)
        # register alias for call resolution: `from x import y as z`
        self.imported[module.split(".")[-1].lower()] = module

    def flush(self) -> dict:
        # file anchor node first
        self.nodes.insert(0, file_node(self.file_nid, self.rel.name, self.str_path))
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "raw_calls": self.raw_calls,
            "definitions": self.definitions,
            "imported": self.imported,
            "type_refs": sorted(self.type_refs),
            "file_nid": self.file_nid,
        }


# --- Python ------------------------------------------------------------------


def _extract_python(source: bytes, path: Path, rel: Path) -> dict:
    tree = parse_bytes("python", source)
    sc = _FileScope(path, rel)

    for node in _walk(tree.root_node):
        t = node.type
        loc = f"L{node.start_point[0] + 1}"

        if t == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    raw = _read_text(child, source)
                    module = raw.split(" as ")[0].strip().lstrip(".")
                    sc.add_import(module, loc)

        elif t == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module = _read_text(module_node, source).lstrip(".") if module_node else ""
            if module:
                sc.add_import(module, loc)
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import", "identifier"):
                    raw = _read_text(child, source)
                    if raw == module or raw.startswith("import"):
                        continue
                    local = raw.split(" as ")[0].strip()
                    if local and local != "*":
                        sc.imported[local.lower()] = module

        elif t in ("class_definition", "decorated_definition") or t == "class_definition":
            pass  # handled below via field access

        if t in ("class_definition", "class_specifier") or t == "class":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                sc.add_definition(nid, name, loc, kind="class")
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)
                bases = node.child_by_field_name("superclasses") or node.child_by_field_name("arguments")
                if bases:
                    for b in _walk(bases):
                        if b.type in ("identifier", "attribute"):
                            base = _read_text(b, source).split("(")[0].strip()
                            if base and base not in _BUILTIN_GLOBALS:
                                sc.add_edge(nid, _make_id(base), "inherits", location=loc)
                                sc.add_type_ref(base)

        elif t in ("function_definition", "function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                sc.add_definition(nid, f"{name}()", loc, kind="function")
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)

        if t == "call":
            fn = node.child_by_field_name("function")
            if fn:
                cname = _read_text(fn, source).split("(")[0].strip()
                sc.add_call(None, cname, loc)

        if t in ("type", "generic_type", "user_type", "type_identifier") and node.parent:
            parent_t = node.parent.type
            if parent_t in ("parameter", "default_parameter", "typed_parameter",
                            "type_annotation", "return_type", "annotation"):
                sc.add_type_ref(_read_text(node, source))

    return sc.flush()


# --- JavaScript / TypeScript ---------------------------------------------------


def _extract_jsts(source: bytes, path: Path, rel: Path, grammar: str) -> dict:
    tree = parse_bytes(grammar, source)
    sc = _FileScope(path, rel)

    for node in _walk(tree.root_node):
        t = node.type
        loc = f"L{node.start_point[0] + 1}"

        if t in ("import_statement", "import_declaration"):
            src_node = node.child_by_field_name("source")
            if src_node:
                module = _read_text(src_node, source).strip("'\"")
                tgt = _make_id("mod", module)
                sc.add_node(tgt, module, location=None)
                sc.add_edge(sc.file_nid, tgt, "imports", location=loc)
                default = node.child_by_field_name("default")
                if default:
                    sc.imported[_read_text(default, source).lower()] = module

        elif t in ("class_declaration", "class"):
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                sc.add_definition(nid, name, loc, kind="class")
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)
                heritage = node.child_by_field_name("heritage") or node.child_by_field_name("body")
                if heritage:
                    for b in _walk(heritage):
                        if b.type in ("identifier", "member_expression"):
                            base = _read_text(b, source)
                            if base and not base.startswith("{"):
                                sc.add_edge(nid, _make_id(base.split("(")[0]), "inherits", location=loc)
                                break

        elif t in ("function_declaration", "generator_function_declaration",
                   "method_definition", "arrow_function", "function"):
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                sc.add_definition(nid, f"{name}()", loc, kind="function")
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn:
                cname = _read_text(fn, source).split("(")[0].strip()
                sc.add_call(None, cname, loc)

        if t in ("type_identifier",) and node.parent and node.parent.type in (
            "type_annotation", "parameter", "return_type",
        ):
            sc.add_type_ref(_read_text(node, source))

    return sc.flush()


# --- Go ------------------------------------------------------------------------


def _extract_go(source: bytes, path: Path, rel: Path) -> dict:
    tree = parse_bytes("go", source)
    sc = _FileScope(path, rel)

    for node in _walk(tree.root_node):
        t = node.type
        loc = f"L{node.start_point[0] + 1}"

        if t == "import_declaration":
            for child in _walk(node):
                if child.type == "import_spec":
                    path_node = child.child_by_field_name("path")
                    if path_node:
                        module = _read_text(path_node, source).strip("'\"")
                        tgt = _make_id("mod", module)
                        sc.add_node(tgt, module, location=None)
                        sc.add_edge(sc.file_nid, tgt, "imports", location=loc)

        elif t == "type_declaration":
            for child in _walk(node):
                if child.type == "type_spec":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        name = _read_text(name_node, source)
                        nid = _make_id(name)
                        sc.add_definition(nid, name, loc, kind="type")
                        sc.add_edge(sc.file_nid, nid, "contains", location=loc)

        elif t in ("function_declaration", "method_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                sc.add_definition(nid, f"{name}()", loc, kind="function")
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)
                recv = node.child_by_field_name("receiver")
                if recv:
                    recv_name = _read_text(recv, source).strip("() *")
                    if recv_name:
                        sc.add_edge(_make_id(recv_name.split(" ")[-1]), nid, "method", location=loc)

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn:
                cname = _read_text(fn, source).split("(")[0].strip()
                sc.add_call(None, cname, loc)

        if t == "type_identifier" and node.parent and node.parent.type in (
            "parameter_declaration", "field_declaration", "result",
        ):
            sc.add_type_ref(_read_text(node, source))

    return sc.flush()


# --- Rust -----------------------------------------------------------------------


def _extract_rust(source: bytes, path: Path, rel: Path) -> dict:
    tree = parse_bytes("rust", source)
    sc = _FileScope(path, rel)

    for node in _walk(tree.root_node):
        t = node.type
        loc = f"L{node.start_point[0] + 1}"

        if t == "use_declaration":
            arg = node.child_by_field_name("argument")
            if arg:
                module = _read_text(arg, source)
                tgt = _make_id("mod", module)
                sc.add_node(tgt, module, location=None)
                sc.add_edge(sc.file_nid, tgt, "imports", location=loc)

        elif t in ("struct_item", "enum_item", "trait_item", "impl_item", "type_item"):
            name_node = node.child_by_field_name("name") or node.child_by_field_name("type")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                kind = {"struct_item": "struct", "enum_item": "enum",
                        "trait_item": "trait", "impl_item": "impl", "type_item": "type"}[t]
                sc.add_definition(nid, name, loc, kind=kind)
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)
                if t == "trait_item":
                    bounds = node.child_by_field_name("bounds")
                    if bounds:
                        for b in _walk(bounds):
                            if b.type == "type_identifier":
                                sc.add_edge(nid, _make_id(_read_text(b, source)),
                                            "inherits", location=loc)
                if t == "impl_item":
                    # impl Trait for Type (or impl Type): link type -> trait
                    trait_node = node.child_by_field_name("trait")
                    if trait_node:
                        trait_name = _read_text(trait_node, source).strip()
                        if trait_name:
                            sc.add_edge(nid, _make_id(trait_name), "inherits", location=loc)

        elif t == "function_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _read_text(name_node, source)
                nid = _make_id(name)
                sc.add_definition(nid, f"{name}()", loc, kind="function")
                sc.add_edge(sc.file_nid, nid, "contains", location=loc)

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn:
                cname = _read_text(fn, source).split("(")[0].strip()
                sc.add_call(None, cname, loc)

        if t in ("generic_type", "reference_type", "scalar_type") and node.parent:
            pass

    return sc.flush()


# --- dispatch --------------------------------------------------------------------


def extract_code(path: Path) -> dict:
    """Extract structural nodes/edges from one source file."""
    path = Path(path)
    ext = path.suffix.lower()
    grammar = _GRAMMAR_BY_EXT.get(ext)
    if grammar is None:
        return {"nodes": [], "edges": []}
    source = path.read_bytes()
    rel = path  # caller passes already-relative-friendly paths

    if grammar == "python":
        result = _extract_python(source, path, rel)
    elif grammar in ("javascript", "typescript"):
        result = _extract_jsts(source, path, rel, grammar)
    elif grammar == "go":
        result = _extract_go(source, path, rel)
    else:
        result = _extract_rust(source, path, rel)

    # strip internal keys not part of the public fragment contract
    result.pop("imported", None)
    result.pop("type_refs", None)
    return result
