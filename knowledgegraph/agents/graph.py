"""The LangGraph multi-agent system.

Topology (LangGraph StateGraph):

                    ┌──────────────┐
   user question ──>│  supervisor  │─────────────┐
                    └──────┬───────┘             │
                 routes to │1..2 workers         │ (needs_more -> one more hop)
                   ▼       ▼       ▼             │
             ┌─────────┐ ┌─────────┐ ┌─────────┐ │
             │ orient  │ │ explorer│ │ impact  │ │
             └────┬────┘ └────┬────┘ └────┬────┘ │
                  └───────────┴───────────┘      │
                                │ evidence       │
                                ▼                │
                        ┌──────────────┐         │
                        │ synthesizer  │<────────┘
                        └──────────────┘
                                │
                                ▼
                             answer

- **supervisor** decides which workers to run. With an LLM it plans; without a
  key it uses a deterministic keyword router, so the graph still works offline.
- **orient**    calls graph_stats - grounds every answer in graph shape.
- **explorer**  calls search_graph / explain_concept / find_path - the "what
  is this and how does it connect" worker.
- **impact**    calls find_affected / list_neighbors - the "what breaks"
  worker.
- **synthesizer** writes the final answer from collected evidence (LLM when
  available, deterministic evidence summary otherwise).

Every node is a pure function of ``AgentState -> dict[str, partial state]``,
which makes the whole graph unit-testable without network calls.
"""
from __future__ import annotations

import json
from pathlib import Path

from knowledgegraph.agents import llm
from knowledgegraph.agents.state import AgentState
from knowledgegraph.agents.tools import (
    TOOL_CATALOG,
    ToolResult,
    load_graph,
    tool_affected,
    tool_explain,
    tool_graph_stats,
    tool_neighbors,
    tool_search,
)

# --- supervisor ---------------------------------------------------------------

_SUPERVISOR_SYSTEM = """You route questions about a codebase knowledge graph to workers.

Workers:
- orient: graph shape stats (always cheap, grounds the answer)
- explorer: what is X, how does X relate to Y (search/explain/path tools)
- impact: blast radius of changing X (affected/neighbors tools)

Answer with ONLY a JSON object:
{"workers": ["orient", ...], "hints": {"explorer": "...terms to search...",
"impact": "...entity to assess..."}}

Keep it to 1-3 workers. orient is almost always worth including."""


def _llm_plan(state: AgentState) -> dict:
    out = llm.chat(_SUPERVISOR_SYSTEM, state["question"])
    data = json.loads(out.strip().strip("`").removeprefix("json").strip())
    workers = [w for w in data.get("workers", []) if w in ("orient", "explorer", "impact")]
    return {"workers": workers or ["orient", "explorer"], "hints": data.get("hints", {})}


def _keyword_plan(question: str) -> dict:
    q = question.lower()
    workers = ["orient"]
    impact_words = ("break", "impact", "affect", "change", "risk", "remove", "delete", "refactor")
    explore_words = ("how", "what", "why", "where", "connect", "relate", "flow", "explain", "who", "uses")
    if any(w in q for w in impact_words):
        workers.append("impact")
    if any(w in q for w in explore_words) or len(workers) == 1:
        workers.append("explorer")
    return {"workers": workers, "hints": {}}


def supervisor_node(state: AgentState) -> dict:
    if llm.llm_available():
        try:
            plan = _llm_plan(state)
        except Exception:
            plan = _keyword_plan(state["question"])
    else:
        plan = _keyword_plan(state["question"])
    return {
        "plan": plan["workers"],
        "trace": [f"supervisor -> route to {', '.join(plan['workers'])}"],
    }


# --- workers --------------------------------------------------------------------


def orient_node(state: AgentState) -> dict:
    G = load_graph(state["graph_path"])
    res: ToolResult = tool_graph_stats(G)
    return {
        "evidence": [{"tool": res.name, "ok": res.ok, "text": res.text, "data": res.data}],
        "trace": [f"orient: {res.text}"],
    }


def explorer_node(state: AgentState) -> dict:
    G = load_graph(state["graph_path"])
    question = state["question"]
    results: list[ToolResult] = []
    res = tool_search(G, question)
    results.append(res)

    # second hop: explain the strongest hit, and try a path if two entities appear
    hints = state.get("plan") or []
    entities = res.data.get("matched") or []
    if entities:
        results.append(tool_explain(G, entities[0]))
    if len(entities) >= 2:
        from knowledgegraph.agents.tools import tool_find_path
        results.append(tool_find_path(G, entities[0], entities[1]))

    evidence = [{"tool": r.name, "ok": r.ok, "text": r.text, "data": _slim(r.data)} for r in results]
    ok_any = any(r.ok for r in results)
    return {
        "evidence": evidence,
        "needs_more": not ok_any,
        "trace": [f"explorer: ran {', '.join(r.name for r in results)}"],
    }


def impact_node(state: AgentState) -> dict:
    G = load_graph(state["graph_path"])
    # entity: try the strongest search hit, else the first quoted token
    res = tool_search(G, state["question"], budget=15)
    entities = res.data.get("matched") or []
    target = entities[0] if entities else state["question"].strip("'\"")
    results = [tool_affected(G, target), tool_neighbors(G, target)]
    evidence = [{"tool": r.name, "ok": r.ok, "text": r.text, "data": _slim(r.data)} for r in results]
    return {
        "evidence": evidence,
        "trace": [f"impact: assessed {target!r}"],
    }


def _slim(data: dict, max_items: int = 25) -> dict:
    """Keep tool payloads compact for the context window."""
    out = {}
    for k, v in data.items():
        if isinstance(v, list):
            out[k] = v[:max_items]
        elif isinstance(v, dict):
            out[k] = _slim(v, max_items)
        else:
            out[k] = v
    return out


# --- synthesizer ------------------------------------------------------------------


def _deterministic_answer(state: AgentState) -> str:
    """Extractive fallback: stitch the evidence texts together."""
    parts = [f"Question: {state['question']}", ""]
    for ev in state.get("evidence", []):
        mark = "[ok]" if ev.get("ok") else "[--]"
        parts.append(f"{mark} {ev.get('tool')}:")
        text = ev.get("text", "")
        parts.extend("  " + line for line in text.splitlines())
        parts.append("")
    return "\n".join(parts).strip()


_SYNTH_SYSTEM = """You answer questions about a codebase using ONLY the evidence
from knowledge-graph tool outputs provided to you.

Rules:
- Cite concrete entity names, files, and relations from the evidence.
- Distinguish EXTRACTED (read directly from source) from INFERRED (resolved
  with a confidence score) when describing relationships.
- If the evidence does not cover the question, say exactly what is missing.
- Be concise: a short direct answer, then the supporting path/relations.
"""


def synthesizer_node(state: AgentState) -> dict:
    evidence_block = "\n\n".join(
        f"## tool: {ev.get('tool')} [{'ok' if ev.get('ok') else 'miss'}]\n{ev.get('text', '')}"
        for ev in state.get("evidence", [])
    )
    if llm.llm_available():
        try:
            answer = llm.chat(_SYNTH_SYSTEM, f"Question: {state['question']}\n\n{evidence_block}")
            return {"answer": answer, "trace": ["synthesizer: LLM answer"]}
        except Exception as e:
            answer = _deterministic_answer(state) + f"\n\n(LLM synthesis unavailable: {e})"
            return {"answer": answer, "trace": ["synthesizer: fallback (LLM error)"]}
    return {"answer": _deterministic_answer(state), "trace": ["synthesizer: extractive fallback"]}


# --- graph assembly ---------------------------------------------------------------


def build_agent_graph():
    """Compile the multi-agent StateGraph. Returns a runnable LangGraph app."""
    from langgraph.graph import StateGraph, END

    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("orient", orient_node)
    g.add_node("explorer", explorer_node)
    g.add_node("impact", impact_node)
    g.add_node("synthesizer", synthesizer_node)

    g.set_entry_point("supervisor")

    def _route(state: AgentState) -> list[str]:
        workers = state.get("plan") or ["orient", "explorer"]
        return [w for w in workers if w in ("orient", "explorer", "impact")]

    g.add_conditional_edges("supervisor", _route, ["orient", "explorer", "impact"])
    g.add_edge("orient", "synthesizer")
    g.add_edge("explorer", "synthesizer")
    g.add_edge("impact", "synthesizer")
    g.add_edge("synthesizer", END)

    return g.compile()


def answer_question(question: str, graph_path: str | None = None) -> str:
    """One-shot entry: build the graph, run it, return the final answer."""
    gp = graph_path or str(Path("kg-out/graph.json"))
    if not Path(gp).exists():
        return (f"error: no graph at {gp}. Run `kg build <path>` first, "
                f"then `kg ask \"...\"`.")
    app = build_agent_graph()
    state: AgentState = {
        "question": question,
        "graph_path": gp,
        "evidence": [],
        "trace": [],
    }
    final = app.invoke(state)
    return str(final.get("answer") or "no answer produced")
