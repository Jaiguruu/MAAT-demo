"""``kg-agent`` - interactive or one-shot multi-agent question answering.

    kg-agent "how does auth connect to the database"   # one-shot
    kg-agent                                            # REPL
    kg-agent --graph path/to/graph.json "question"      # explicit graph
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from knowledgegraph.agents.graph import answer_question, build_agent_graph
from knowledgegraph.agents.llm import llm_available
from knowledgegraph.agents.state import AgentState


def _run(app, question: str, graph_path: str) -> str:
    state: AgentState = {"question": question, "graph_path": graph_path,
                         "evidence": [], "trace": []}
    final = app.invoke(state)
    trace = final.get("trace") or []
    lines = [f"  [{t}]" for t in trace]
    print("\n".join(lines), file=sys.stderr)
    return str(final.get("answer") or "no answer produced")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kg-agent",
        description="LangGraph multi-agent QA over the knowledge graph",
    )
    parser.add_argument("question", nargs="*", help="question (omit for REPL)")
    parser.add_argument("--graph", default=None, help="path to graph.json")
    args = parser.parse_args(argv)

    graph_path = args.graph or str(Path("kg-out/graph.json"))
    if not Path(graph_path).exists():
        print(f"error: no graph at {graph_path}. Run `kg build <path>` first.",
              file=sys.stderr)
        sys.exit(1)

    mode = "LLM-backed" if llm_available() else "deterministic (no OPENAI_API_KEY)"
    print(f"agent mode: {mode}", file=sys.stderr)
    app = build_agent_graph()

    if args.question:
        print(_run(app, " ".join(args.question), graph_path))
        return

    # REPL
    print("kg-agent REPL - empty line to quit.")
    while True:
        try:
            question = input("ask> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print(_run(app, question, graph_path))
        print()


if __name__ == "__main__":
    main()
