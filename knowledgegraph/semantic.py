"""Semantic extraction - an LLM pass over documents that emits the same
validated fragment schema as the structural extractors.

Structural extraction is deterministic but only sees *structure*: headings,
links, code symbols. The semantic pass reads document *content* and pulls out
what a parser cannot:

- concept nodes (``file_type: "concept"``) - ideas, patterns, decisions,
  constraints named in prose, with a short ``summary`` attribute
- ``references`` edges between the concepts a document connects
- ``code_links``: raw (concept, code-name) claims about real code entities.
  These are stored UNRESOLVED in the fragment and resolved later by
  :func:`apply_semantic_fragments` against the fully-built graph. Keeping them
  raw keeps the per-document cache valid even when the code it references
  changes, and sidesteps the fragment-locality rule of ``validate.py``
  (edge endpoints must exist within the same fragment).

Ground rules that keep the pass safe:

- The LLM only ever receives document text (never secrets - detect already
  dropped those) inside an untrusted-source wrapper, and only ever returns
  JSON that goes through the same ``validate`` gate as every other fragment.
- Invalid fragments are dropped, not repaired: the LLM is a contributor with
  the same contract as any extractor, not a privileged one.
- Every concept edge lands as ``INFERRED`` with a confidence score; the graph
  never conflates LLM-derived structure with parser-derived fact.
- Per-fragment results are cached under the document's content hash, so
  re-runs re-bill nothing.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from knowledgegraph import config
from knowledgegraph.ids import make_id
from knowledgegraph.validate import validate_extraction

_SYSTEM_PROMPT = """You extract a knowledge-graph fragment from a document.

Return ONLY a JSON object with this exact shape:
{
  "nodes": [
    {"id": "snake_case_id", "label": "Human Name", "file_type": "concept",
     "summary": "one sentence"}
  ],
  "edges": [
    {"source": "node_id_a", "target": "node_id_b", "relation": "references",
     "confidence_score": 0.85}
  ],
  "code_links": [{"concept_id": "...", "code_name": "AuthService"}]
}

Rules:
- 3-10 concept nodes. Extract ideas, patterns, decisions, constraints,
  components - things the document is actually ABOUT. No stopwords as nodes.
- Concept ids: lowercase snake_case, stable and descriptive.
- Edges connect your concept ids to each other (relation: "references" or
  "semantically_similar_to").
- code_links: when the document names a real code entity (class, function,
  module, file), list it. Use the exact name as written.
- confidence_score is 0.0-1.0: how sure are you this relationship is real?
- No prose, no markdown fences - JSON only.
"""

_UNTRUSTED_WRAPPER = (
    "<untrusted_source path=\"{path}\" sha256=\"{sha}\">\n{body}\n</untrusted_source>\n"
    "Extract the knowledge-graph fragment for the document above. JSON only."
)

_MAX_DOC_CHARS = 20_000  # per-document input cap
_CHARS_PER_TOKEN = 4
_DEFAULT_TOKEN_BUDGET = 60_000


def _fragment_id(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{path.name}:{digest}"


def _clip_to_budget(text: str, budget_tokens: int) -> str:
    limit = budget_tokens * _CHARS_PER_TOKEN
    return text if len(text) <= limit else text[:limit]


def _call_llm(user_prompt: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=config.api_key(), base_url=config.base_url())
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content or ""


def _parse_json_out(raw: str) -> dict | None:
    """Tolerant JSON extraction: strips fences, finds the outermost object."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def extract_semantic_fragment(
    path: Path,
    model: str | None = None,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
) -> dict:
    """Run the LLM extraction for one document.

    Returns a validated nodes/edges fragment whose ``code_links`` remain
    raw claims (``[{concept_id, code_name}]``) to be resolved against the
    built graph by :func:`apply_semantic_fragments`. On any failure returns
    a fragment with an ``error`` key - one bad document never kills the run.
    """
    path = Path(path)
    model = model or config.semantic_model()
    try:
        body = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"nodes": [], "edges": [], "error": f"read: {e}"}

    body = _clip_to_budget(body, token_budget)
    sha = hashlib.sha256(body.encode()).hexdigest()[:16]
    user_prompt = _UNTRUSTED_WRAPPER.format(path=path.name, sha=sha, body=body)

    try:
        raw = _call_llm(user_prompt, model)
    except Exception as e:
        return {"nodes": [], "edges": [], "error": f"llm: {type(e).__name__}: {e}"}

    data = _parse_json_out(raw)
    if data is None:
        return {"nodes": [], "edges": [], "error": "llm returned unparseable JSON"}

    # normalize into the canonical fragment schema
    file_nid = make_id(path.name)
    nodes: list[dict] = [{
        "id": file_nid, "label": path.name, "file_type": "document",
        "source_file": str(path), "source_location": "L1",
    }]
    edges: list[dict] = []
    seen: set[str] = {file_nid}
    concept_ids: set[str] = set()

    for n in data.get("nodes", [])[:30]:
        if not isinstance(n, dict):
            continue
        label = str(n.get("label") or "").strip()
        if not label:
            continue
        nid = make_id("concept", str(n.get("id") or label))
        concept_ids.add(nid)
        node = {
            "id": nid, "label": label, "file_type": "concept",
            "source_file": str(path), "source_location": None,
        }
        summary = str(n.get("summary") or "").strip()
        if summary:
            node["summary"] = summary[:300]
        if nid not in seen:
            seen.add(nid)
            nodes.append(node)

    for e in data.get("edges", [])[:40]:
        if not isinstance(e, dict):
            continue
        relation = str(e.get("relation") or "references").strip()
        if relation not in ("references", "semantically_similar_to"):
            relation = "references"
        try:
            score = float(e.get("confidence_score", 0.75))
        except (TypeError, ValueError):
            score = 0.75
        score = max(0.0, min(1.0, score))
        src = make_id("concept", str(e.get("source") or ""))
        tgt = make_id("concept", str(e.get("target") or ""))
        if src in concept_ids and tgt in concept_ids and src != tgt:
            edges.append({
                "source": src, "target": tgt, "relation": relation,
                "confidence": "INFERRED", "confidence_score": round(score, 2),
                "source_file": str(path), "source_location": None, "weight": score,
            })

    # code links stay RAW in the fragment (not edges) so that (a) validation
    # holds without stub nodes and (b) cached fragments stay valid when the
    # code they point at is renamed or deleted.
    code_links: list[dict] = []
    for link in data.get("code_links", [])[:20]:
        if not isinstance(link, dict):
            continue
        cid = make_id("concept", str(link.get("concept_id") or ""))
        cname = str(link.get("code_name") or "").strip()
        if cid in concept_ids and cname:
            code_links.append({"concept_id": cid, "code_name": cname})

    # anchor every concept to its source document
    for nid in sorted(concept_ids):
        edges.append({
            "source": file_nid, "target": nid, "relation": "contains",
            "confidence": "EXTRACTED", "source_file": str(path),
            "source_location": None, "weight": 1.0,
        })

    frag: dict = {"nodes": nodes, "edges": edges}
    if code_links:
        frag["code_links"] = code_links
    errors = validate_extraction(frag)
    if errors:
        print(f"  semantic: dropping {path.name} fragment ({len(errors)} errors)",
              file=sys.stderr, flush=True)
        return {"nodes": [], "edges": [], "error": f"schema: {errors[0]}"}
    return frag


def build_code_label_index(G) -> dict[str, str]:
    """lowercase bare label -> node id, for code-class nodes only.

    Duplicate labels map to "" (ambiguous: never resolve).
    """
    from knowledgegraph.query import _bare
    index: dict[str, str] = {}
    for nid, d in G.nodes(data=True):
        if d.get("file_type") != "code":
            continue
        label = str(d.get("label", ""))
        bare = _bare(label)
        if not bare or bare in index:
            index[bare] = ""  # ambiguous
            continue
        index[bare] = str(nid)
    return {k: v for k, v in index.items() if v}


def apply_semantic_fragments(G, fragments: dict[str, dict]) -> dict:
    """Fold validated semantic fragments into the built graph.

    Adds concept nodes and intra-concept edges directly; resolves each raw
    ``code_links`` entry against the code-label index (exact bare-name match,
    ambiguous names skipped) and adds the resulting INFERRED ``references``
    edge only when both endpoints exist in the graph. Returns a stats dict.
    """
    label_index = build_code_label_index(G)
    stats = {"concepts": 0, "concept_edges": 0, "code_links": 0, "unresolved": 0}

    for frag in fragments.values():
        if frag.get("error"):
            continue
        errors = validate_extraction(frag)
        if errors:
            continue

        for node in frag.get("nodes", []):
            nid = node["id"]
            attrs = {k: v for k, v in node.items() if k != "stub"}
            if G.has_node(nid):
                G.nodes[nid].update(
                    {k: v for k, v in attrs.items()
                     if v not in (None, "") or G.nodes[nid].get(k) in (None, "")}
                )
            else:
                G.add_node(nid, **attrs)
                if attrs.get("file_type") == "concept":
                    stats["concepts"] += 1

        for e in frag.get("edges", []):
            src, tgt = e["source"], e["target"]
            if not G.has_node(src) or not G.has_node(tgt) or G.has_edge(src, tgt):
                continue
            attrs = {k: v for k, v in e.items() if k not in ("source", "target")}
            G.add_edge(src, tgt, **attrs)
            stats["concept_edges"] += 1

        for link in frag.get("code_links") or []:
            target = label_index.get(link["code_name"].strip().rstrip("()")
                                     .lstrip(".").lower())
            cid = link["concept_id"]
            if target and G.has_node(cid) and target != cid and not G.has_edge(cid, target):
                G.add_edge(
                    cid, target, relation="references",
                    confidence="INFERRED", confidence_score=0.85,
                    source_file=G.nodes[cid].get("source_file"),
                    source_location=None, weight=0.85,
                )
                stats["code_links"] += 1
            else:
                stats["unresolved"] += 1

    return stats


def extract_semantic_batch(
    paths: list[str],
    model: str | None = None,
    max_concurrency: int = 4,
    cache_dir: Path | None = None,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
) -> dict[str, dict]:
    """Semantic extraction over many documents, in parallel, with caching.

    Returns {path: fragment}. Cached fragments are replayed under the
    document's content hash; failures return error fragments.
    """
    cache_dir = Path(cache_dir) if cache_dir else Path("kg-out/cache/semantic")
    results: dict[str, dict] = {}

    # try cache first (keyed by content hash of the document)
    to_run: list[str] = []
    for p in paths:
        try:
            content_hash = hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
        except OSError:
            continue
        entry = cache_dir / f"{content_hash}.json"
        if entry.exists():
            try:
                results[p] = json.loads(entry.read_text(encoding="utf-8"))
                continue
            except (json.JSONDecodeError, OSError):
                pass
        to_run.append(p)

    if to_run:
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            futures = {
                pool.submit(extract_semantic_fragment, Path(p), model, token_budget): p
                for p in to_run
            }
            for fut in as_completed(futures):
                p = futures[fut]
                frag = fut.result()
                results[p] = frag
                if not frag.get("error"):
                    try:
                        content_hash = hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        (cache_dir / f"{content_hash}.json").write_text(
                            json.dumps(frag, ensure_ascii=False), encoding="utf-8")
                    except OSError:
                        pass

    for p in paths:
        results.setdefault(p, {"nodes": [], "edges": [], "error": "skipped"})
    return results
