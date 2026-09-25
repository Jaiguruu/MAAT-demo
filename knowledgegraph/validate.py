# Schema validation for extraction fragments, enforced before graph assembly.
from __future__ import annotations

VALID_FILE_TYPES = {"code", "document", "paper", "image", "rationale", "concept"}
VALID_CONFIDENCES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
REQUIRED_NODE_FIELDS = {"id", "label", "file_type", "source_file"}
REQUIRED_EDGE_FIELDS = {"source", "target", "relation", "confidence", "source_file"}


def validate_extraction(data: dict) -> list[str]:
    """Validate one extraction fragment. Returns a list of errors (empty = valid)."""
    if not isinstance(data, dict):
        return ["Extraction must be a JSON object"]

    errors: list[str] = []
    node_ids: set = set()

    if "nodes" not in data:
        errors.append("Missing required key 'nodes'")
    elif not isinstance(data["nodes"], list):
        errors.append("'nodes' must be a list")
    else:
        for i, node in enumerate(data["nodes"]):
            if not isinstance(node, dict):
                errors.append(f"Node {i} must be an object")
                continue
            for field in REQUIRED_NODE_FIELDS:
                if field not in node:
                    errors.append(f"Node {i} (id={node.get('id', '?')!r}) missing required field '{field}'")
            if "id" in node:
                try:
                    hash(node["id"])
                except TypeError:
                    errors.append(f"Node {i} has non-hashable id {node['id']!r} - id must be a string")
                else:
                    node_ids.add(node["id"])
            if "file_type" in node and node["file_type"] not in VALID_FILE_TYPES:
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) has invalid file_type "
                    f"'{node['file_type']}' - must be one of {sorted(VALID_FILE_TYPES)}"
                )

    edge_list = data.get("edges") if "edges" in data else data.get("links")
    if edge_list is None:
        errors.append("Missing required key 'edges'")
    elif not isinstance(edge_list, list):
        errors.append("'edges' must be a list")
    else:
        for i, e in enumerate(edge_list):
            if not isinstance(e, dict):
                errors.append(f"Edge {i} must be an object")
                continue
            for field in REQUIRED_EDGE_FIELDS:
                if field not in e:
                    errors.append(f"Edge {i} missing required field '{field}'")
            if "confidence" in e and e["confidence"] not in VALID_CONFIDENCES:
                errors.append(f"Edge {i} has invalid confidence '{e['confidence']}'")
            for endpoint in ("source", "target"):
                if endpoint not in e:
                    continue
                val = e[endpoint]
                try:
                    unmatched = bool(node_ids) and val not in node_ids
                except TypeError:
                    errors.append(f"Edge {i} {endpoint} {val!r} is non-hashable")
                    continue
                if unmatched:
                    errors.append(f"Edge {i} {endpoint} '{val}' does not match any node id")

    return errors


def assert_valid(data: dict) -> None:
    """Raise ValueError with all errors if the fragment is invalid."""
    errors = validate_extraction(data)
    if errors:
        msg = f"Extraction has {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(msg)
