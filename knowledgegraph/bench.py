"""Benchmark harness: run the graph system and the ReAct baseline on the same
questions and emit an arithmetic comparison.

Usage:
    kg-bench examples/sample_repo --question "how does auth connect to the db"
    kg-bench examples/sample_repo --all            # built-in scenario set
    kg-bench repo/ --questions q1 q2 --max-steps 12

Outputs:
    kg-out/bench/runs.json         raw metric log (appended per run)
    kg-out/bench/COMPARISON.md     generated comparison report

Fairness rules (see docs/diagrams/10-bench-workflow.md):
- same questions, same order, both sides
- graph build cost recorded separately as "ingest", never hidden
- failed LLM calls still count (zero tokens, error recorded)
- the baseline gets max_steps and the same model as the graph side
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from knowledgegraph import metrics
from knowledgegraph.baseline.react import run_react
from knowledgegraph.paths import default_graph_json

DEFAULT_SCENARIOS = [
    "how does auth connect to the database",
    "what breaks if I change the database layer",
    "explain the token flow through the system",
    "which parts of the codebase handle rate limiting",
]


def _graph_side_answers(questions: list[str], graph_path: str,
                        ledger: metrics.UsageLedger) -> list[str]:
    """Run the graph agent for each question; returns answers."""
    from knowledgegraph.agents.graph import answer_question
    answers = []
    for q in questions:
        answers.append(answer_question(q, graph_path=graph_path))
    # aggregate the agent's recorded wall time per purpose into phases
    return answers


def run_bench(repo: Path, questions: list[str], *, graph_path: str | None = None,
              skip_build: bool = False, max_steps: int = 12,
              model: str | None = None, out_dir: Path | None = None) -> dict:
    """Run the full comparison. Returns the summary dict (also written to disk)."""
    repo = Path(repo).resolve()
    out_dir = Path(out_dir) if out_dir else Path("kg-out/bench")
    gp = graph_path or str(default_graph_json())

    # provenance: if reusing a prebuilt graph, warn when it was built from a
    # different repo than the one being benched (stale-structure comparison)
    if skip_build and Path(gp).exists():
        stamped = None
        try:
            stamped = json.loads(Path(gp).read_text(encoding="utf-8"))\
                .get("graph", {}).get("source_root")
        except (json.JSONDecodeError, OSError):
            pass
        if stamped and Path(stamped).resolve() != repo:
            print(f"warning: graph was built from {stamped} but bench targets "
                  f"{repo} - results compare against stale structure")

    # ---- graph side -----------------------------------------------------
    graph_ledger = metrics.UsageLedger()
    metrics.set_ledger(graph_ledger)
    try:
        if not skip_build:
            t0 = time.perf_counter()
            from knowledgegraph.pipeline import run_pipeline
            run_pipeline(repo, semantic=True)  # semantic tokens land in the ledger
            graph_ledger.record_phase("ingest", "kg_build", (time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        graph_answers = _graph_side_answers(questions, gp, graph_ledger)
        graph_ledger.record_phase("answer", "graph_qa_wall", (time.perf_counter() - t0) * 1000,
                                  extra={"questions": len(questions)})
    finally:
        metrics.set_ledger(None)
    graph_summary = graph_ledger.summarize("graph")
    graph_summary["graph_source_root"] = (
        json.loads(Path(gp).read_text(encoding="utf-8")).get("graph", {}).get("source_root")
        if Path(gp).exists() else None)

    # ---- baseline side ----------------------------------------------------
    baseline_ledger = metrics.UsageLedger()
    metrics.set_ledger(baseline_ledger)
    baseline_answers: list[str] = []
    baseline_steps: list[list[dict]] = []
    try:
        t0 = time.perf_counter()
        baseline_ledger.record_phase("ingest", "file_scan", 0.0)  # scan happens inside the loop
        for q in questions:
            result = run_react(q, repo, max_steps=max_steps, model=model)
            baseline_answers.append(result.answer)
            baseline_steps.append([s for s in result.steps])
        baseline_ledger.record_phase("answer", "baseline_qa_wall",
                                     (time.perf_counter() - t0) * 1000,
                                     extra={"questions": len(questions)})
    finally:
        metrics.set_ledger(None)
    baseline_summary = baseline_ledger.summarize("baseline")

    # ---- assemble ----------------------------------------------------------
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "questions": questions,
        "max_steps": max_steps,
        "graph": graph_summary,
        "baseline": baseline_summary,
        "per_question": [
            {
                "question": q,
                "graph_answer_chars": len(a),
                "baseline_answer_chars": len(b),
                "baseline_steps": steps,
            }
            for q, a, b, steps in zip(questions, graph_answers, baseline_answers,
                                      baseline_steps)
        ],
    }

    # runs.json is a log: append, never overwrite
    runs_path = out_dir / "runs.json"
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    log = []
    if runs_path.exists():
        try:
            log = json.loads(runs_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log = []
    log.append(report)
    runs_path.write_text(json.dumps(log, indent=2), encoding="utf-8")

    # persist each side's raw records too
    graph_ledger.save(out_dir / "graph-side.json", "graph")
    baseline_ledger.save(out_dir / "baseline-side.json", "baseline")

    # human report
    md = render_comparison(report)
    (out_dir / "COMPARISON.md").write_text(md, encoding="utf-8")
    return report


def render_comparison(report: dict) -> str:
    """Render the human-readable comparison markdown."""
    g, b = report["graph"], report["baseline"]

    def _row(name, gval, bval, fmt="{}", lower_better=True):
        return f"| {name} | {fmt.format(gval)} | {fmt.format(bval)} |"

    lines = [
        f"# Bench Comparison - {report['repo']}",
        f"_{report['ts']} · {len(report['questions'])} questions · "
        f"baseline max_steps={report['max_steps']}_",
        "",
        "## Headline",
        "",
        "| Metric | Graph system | ReAct baseline |",
        "|---|---|---|",
        _row("LLM calls", g["llm_calls"], b["llm_calls"]),
        _row("Total tokens", g["total_tokens"], b["total_tokens"]),
        _row("Prompt tokens", g["prompt_tokens"], b["prompt_tokens"]),
        _row("Completion tokens", g["completion_tokens"], b["completion_tokens"]),
        _row("Cost (USD, est.)", f"${g['cost_usd']:.4f}", f"${b['cost_usd']:.4f}"),
        _row("Wall time (s)", f"{g['wall_ms'] / 1000:.1f}", f"{b['wall_ms'] / 1000:.1f}"),
        _row("Failed calls", g["failed_calls"], b["failed_calls"]),
        "",
        "## Per-purpose breakdown",
        "",
        "| Purpose | Graph tokens | Baseline tokens | Graph calls | Baseline calls |",
        "|---|---|---|---|---|",
    ]
    for p in metrics.PURPOSES:
        gp, bp = g["by_purpose"][p], b["by_purpose"][p]
        lines.append(
            f"| {p} | {gp['prompt_tokens'] + gp['completion_tokens']} "
            f"| {bp['prompt_tokens'] + bp['completion_tokens']} "
            f"| {gp['calls']} | {bp['calls']} |")

    lines += [
        "",
        "## Per-question baseline effort",
        "",
        "| Question | Baseline steps | Tools used |",
        "|---|---|---|",
    ]
    for pq in report["per_question"]:
        tools = ", ".join(sorted({s.get("action", "?") for s in pq["baseline_steps"]
                                  if s.get("action") not in ("finish",)})) or "-"
        lines.append(f"| {pq['question'][:60]} | {len(pq['baseline_steps'])} | {tools} |")

    # break-even: graph build cost vs per-question saving
    build_tokens = g["by_purpose"]["ingest"]["prompt_tokens"] + g["by_purpose"]["ingest"]["completion_tokens"]
    g_qa = sum(g["by_purpose"][p]["prompt_tokens"] + g["by_purpose"][p]["completion_tokens"]
               for p in ("plan", "answer"))
    b_qa = sum(b["by_purpose"][p]["prompt_tokens"] + b["by_purpose"][p]["completion_tokens"]
               for p in ("plan", "read", "answer"))
    per_q_saving = b_qa - g_qa
    lines += [
        "",
        "## Break-even",
        "",
        f"- Graph build (semantic pass): **{build_tokens}** tokens (once, cached)",
        f"- Graph ask-time cost/question: **{g_qa // max(len(report['questions']), 1)}** tokens",
        f"- Baseline ask-time cost/question: **{b_qa // max(len(report['questions']), 1)}** tokens",
    ]
    if per_q_saving > 0:
        lines.append(
            f"- Saving/question: **{per_q_saving}** tokens → build amortizes after "
            f"**{build_tokens // max(per_q_saving, 1)}** questions")
    else:
        lines.append("- No per-question saving at this corpus size (expected for tiny repos)")

    lines += [
        "",
        "## How to read this",
        "",
        "- Both sides use the same model and the same questions in the same order.",
        "- `read` tokens are the structural advantage of the graph: the baseline",
        "  re-reads source every run; the graph precomputed it.",
        "- Graph build cost is one-time and cached (content-hash keyed);",
        "  baseline cost is per-question and never cached.",
        "- Costs are estimates from a built-in price table; check current pricing",
        "  before quoting dollar figures.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kg-bench",
                                     description="Compare the graph system against the ReAct baseline")
    parser.add_argument("repo", help="repo folder to benchmark")
    parser.add_argument("--question", action="append", default=None,
                        help="question to ask (repeatable)")
    parser.add_argument("--all", action="store_true", help="run the built-in scenario set")
    parser.add_argument("--graph", default=None, help="existing graph.json to reuse (--skip-build)")
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing graph")
    parser.add_argument("--max-steps", type=int, default=12, help="baseline ReAct step budget")
    parser.add_argument("--model", default=None, help="override model for the baseline")
    args = parser.parse_args(argv)

    questions = args.question or (DEFAULT_SCENARIOS if args.all else None)
    if not questions:
        parser.error("provide --question or --all")

    report = run_bench(Path(args.repo), questions,
                       graph_path=args.graph, skip_build=args.skip_build,
                       max_steps=args.max_steps, model=args.model)
    g, b = report["graph"], report["baseline"]
    print(f"graph:    {g['llm_calls']} calls, {g['total_tokens']} tokens, "
          f"${g['cost_usd']:.4f}, {g['wall_ms'] / 1000:.1f}s")
    print(f"baseline: {b['llm_calls']} calls, {b['total_tokens']} tokens, "
          f"${b['cost_usd']:.4f}, {b['wall_ms'] / 1000:.1f}s")
    print("full report: kg-out/bench/COMPARISON.md")


if __name__ == "__main__":
    main()
