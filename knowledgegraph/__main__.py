"""``kg`` command-line interface.

    kg build <path>      run the full pipeline (detect->extract->build->cluster->analyze->report)
    kg query "<question>"  scoped subgraph for a plain-language question
    kg path "A" "B"        shortest relationship path between two entities
    kg explain "A"         one node plus its neighborhood
    kg affected "A"        blast radius of a node
    kg ask "<question>"    multi-agent answer (LangGraph + OpenAI; needs OPENAI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from knowledgegraph.build import load_graph
from knowledgegraph.paths import default_graph_json
from knowledgegraph.query import affected, explain, path, query


def _load_default_graph() -> "nx.Graph":
    gp = default_graph_json()
    if not gp.exists():
        print("error: no graph found. Run `kg build <path>` first.", file=sys.stderr)
        sys.exit(1)
    return load_graph(gp)


def cmd_build(args: argparse.Namespace) -> None:
    from knowledgegraph.pipeline import run_pipeline
    result = run_pipeline(
        Path(args.root),
        update=args.update,
        directed=args.directed,
        no_report=args.no_viz,
        resolution=args.resolution,
        max_workers=args.max_workers,
        semantic=not args.no_semantic,
    )
    print(f"built graph: {result.nodes} nodes, {result.edges} edges, "
          f"{result.communities} communities")
    print(f"  cached {result.cached_files} / extracted {result.extracted_files} files "
          f"(inferred edges: {result.inferred_edges}, label merges: {result.merges})")
    if result.semantic_concepts or result.semantic_code_links:
        print(f"  semantic: +{result.semantic_concepts} concept nodes, "
              f"+{result.semantic_code_links} code links")
    print(f"  graph: {result.graph_path}")
    if result.report_path.exists():
        print(f"  report: {result.report_path}")


def cmd_query(args: argparse.Namespace) -> None:
    G = _load_default_graph()
    result = query(G, args.question, budget=args.budget)
    if not result.nodes:
        print("no matching nodes - try different terms")
        return
    print(f"{len(result.matched)} direct hit(s): {', '.join(result.matched[:8])}")
    print(f"subgraph: {len(result.nodes)} nodes, {len(result.edges)} edges\n")
    print(result.render())


def cmd_path(args: argparse.Namespace) -> None:
    G = _load_default_graph()
    hops = path(G, args.a, args.b, max_hops=args.max_hops)
    if hops is None:
        print("no path found (or unknown entity)")
        return
    print(f"shortest path ({len(hops)} hops):")
    for h in hops:
        print(f"  {h['source_label']} --{h['relation']}--> {h['target_label']} "
              f"[{h['confidence']}]")


def cmd_explain(args: argparse.Namespace) -> None:
    G = _load_default_graph()
    d = explain(G, args.name)
    if d is None:
        print("no such node")
        return
    print(f"Node: {d['label']}")
    print(f"  Source:    {d['source_file'] or '-'} {d['source_location'] or ''}")
    comm = d.get("community_label") or d.get("community")
    print(f"  Community: {comm if comm is not None else '-'}")
    print(f"  Degree:    {d['degree']}")
    print(f"\nConnections ({len(d['connections'])}):")
    for c in d["connections"]:
        arrow = "-->" if c["direction"] == "out" else "<--"
        print(f"  {arrow} {c['label']} [{c['relation']}] [{c['confidence']}] "
              f"({c['source_file'] or '-'})")


def cmd_affected(args: argparse.Namespace) -> None:
    G = _load_default_graph()
    hits = affected(G, args.name, max_depth=args.depth)
    if not hits:
        print("no downstream dependents (or unknown entity)")
        return
    print(f"{len(hits)} affected node(s):")
    for h in hits:
        indent = "  " * (h["depth"] + 1)
        print(f"{indent}{h['label']}  (via {h['via']}, depth {h['depth']})")


def cmd_ask(args: argparse.Namespace) -> None:
    from knowledgegraph.agents.graph import answer_question
    gp = args.graph or str(default_graph_json())
    result = answer_question(args.question, graph_path=gp)
    print(result)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kg", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="build the knowledge graph for a folder")
    p.add_argument("root", help="corpus root folder")
    p.add_argument("--update", action="store_true", help="re-extract only changed files")
    p.add_argument("--directed", action="store_true", help="preserve edge direction")
    p.add_argument("--no-viz", action="store_true", help="skip the markdown report")
    p.add_argument("--resolution", type=float, default=1.0,
                   help="community granularity (>1 = more, smaller communities)")
    p.add_argument("--max-workers", type=int, default=None, help="parallel extraction workers")
    p.add_argument("--no-semantic", action="store_true",
                   help="skip the LLM semantic pass (no API calls, no API key needed)")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("query", help="scoped subgraph for a question")
    p.add_argument("question")
    p.add_argument("--budget", type=int, default=60)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("path", help="shortest path between two entities")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--max-hops", type=int, default=6)
    p.set_defaults(func=cmd_path)

    p = sub.add_parser("explain", help="one node plus its neighborhood")
    p.add_argument("name")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("affected", help="blast radius of a node")
    p.add_argument("name")
    p.add_argument("--depth", type=int, default=4)
    p.set_defaults(func=cmd_affected)

    p = sub.add_parser("ask", help="multi-agent answer (needs OPENAI_API_KEY)")
    p.add_argument("question")
    p.add_argument("--graph", default=None, help="path to graph.json")
    p.set_defaults(func=cmd_ask)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
