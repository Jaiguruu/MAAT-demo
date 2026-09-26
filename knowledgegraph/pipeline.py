"""Pipeline orchestrator - runs every stage in order and writes the outputs.

    detect -> extract -> build (+ inferred call edges, dedup)
           -> cluster -> analyze -> report -> graph.json
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from knowledgegraph import cache as kg_cache
from knowledgegraph import config
from knowledgegraph import detect as kg_detect
from knowledgegraph.analyze import analyze
from knowledgegraph.build import (
    add_inferred_call_edges,
    build_graph,
    dedupe_labels,
    save_graph,
)
from knowledgegraph.cluster import cluster
from knowledgegraph.extract import extract_all, extract_package_manifest
from knowledgegraph.paths import default_graph_json, default_report_md
from knowledgegraph.report import generate
from knowledgegraph.semantic import apply_semantic_fragments, extract_semantic_batch


@dataclass
class PipelineResult:
    root: str
    graph_path: Path
    report_path: Path
    nodes: int = 0
    edges: int = 0
    communities: int = 0
    cached_files: int = 0
    extracted_files: int = 0
    inferred_edges: int = 0
    merges: int = 0
    semantic_concepts: int = 0
    semantic_code_links: int = 0
    timings: dict = field(default_factory=dict)


def run_pipeline(
    root: Path,
    out_dir: Path | None = None,
    *,
    update: bool = False,
    directed: bool = False,
    no_report: bool = False,
    resolution: float = 1.0,
    max_workers: int | None = None,
    semantic: bool = True,
) -> PipelineResult:
    """Build the knowledge graph for ``root``. ``update`` reuses cached
    fragments for files whose content hash is unchanged."""
    t0 = time.perf_counter()
    root = Path(root).resolve()
    timings: dict[str, float] = {}

    def mark(stage: str, since: float) -> float:
        now = time.perf_counter()
        timings[stage] = round(now - since, 2)
        return now

    # Stage 1: detect
    detection = kg_detect.detect(root)
    mark("detect", t0)

    all_files: list[str] = []
    for bucket in ("code", "document", "paper"):
        all_files.extend(detection["files"].get(bucket, []))

    # Stage 2: extract (with cache replay when --update)
    cached: dict[str, dict] = {}
    to_extract = all_files
    if update:
        cached, to_extract = kg_cache.split_cached(all_files, root)

    fragments: dict[str, dict] = dict(cached)
    if to_extract:
        fresh = extract_all(to_extract, max_workers=max_workers)
        for p, frag in zip(to_extract, fresh):
            fragments[p] = frag
            if update and not frag.get("error"):
                kg_cache.save_cached(p, frag)
    if update:
        kg_cache.save_index(root)
    mark("extract", t0 + timings["detect"])

    # package manifests fold in as deterministic fragments
    for p in all_files:
        path = Path(p)
        if path.name.lower() in ("pyproject.toml", "package.json", "go.mod"):
            frag = extract_package_manifest(path)
            if frag:
                fragments[f"manifest:{p}"] = frag

    # Stage 3: build
    extraction_list = [fragments[p] for p in sorted(fragments)]
    G = build_graph(extraction_list, directed=directed)
    inferred = add_inferred_call_edges(G, extraction_list)
    merges = dedupe_labels(G)
    mark("build", t0 + timings["detect"] + timings["extract"])

    # Stage 3b: semantic pass (LLM over documents; skipped without a key)
    sem_concepts = sem_links = 0
    if semantic and config.llm_ready():
        doc_paths = [p for p in detection["files"].get("document", [])
                     + detection["files"].get("paper", [])]
        if doc_paths:
            sem_fragments = extract_semantic_batch(doc_paths, cache_dir=default_graph_json().parent / "cache" / "semantic")
            sem_stats = apply_semantic_fragments(G, sem_fragments)
            sem_concepts = sem_stats["concepts"]
            sem_links = sem_stats["code_links"]
    mark("semantic", t0 + timings["build"])

    # Stage 4: cluster
    communities = cluster(G, resolution=resolution)
    cohesion_scores = {int(k): v for k, v in (G.graph.get("cohesion") or {}).items()}
    community_labels = {int(k): v for k, v in (G.graph.get("community_labels") or {}).items()}
    mark("cluster", t0 + timings["semantic"])

    # Stage 5: analyze
    analysis = analyze(G, communities)
    mark("analyze", t0 + timings["cluster"])

    # Stage 6: report + serialize
    G.graph["source_root"] = str(root)
    graph_path = (out_dir / "graph.json") if out_dir else default_graph_json()
    save_graph(G, graph_path)

    report_path = graph_path.parent / "GRAPH_REPORT.md"
    if not no_report:
        report_md = generate(
            G=G,
            communities=communities,
            cohesion_scores=cohesion_scores,
            community_labels=community_labels,
            god_node_list=analysis["god_nodes"],
            surprise_list=analysis["surprises"],
            detection_result=detection,
            root=str(root),
            suggested_questions=analysis["questions"],
            gap_list=analysis["gaps"],
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")
    mark("report", time.perf_counter())
    return PipelineResult(
        root=str(root),
        graph_path=graph_path,
        report_path=report_path if not no_report else report_path,
        nodes=G.number_of_nodes(),
        edges=G.number_of_edges(),
        communities=len(communities),
        cached_files=len(cached),
        extracted_files=len(to_extract),
        inferred_edges=inferred,
        merges=merges,
        semantic_concepts=sem_concepts,
        semantic_code_links=sem_links,
        timings=timings,
    )
