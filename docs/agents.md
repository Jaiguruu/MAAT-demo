# The Multi-Agent Question-Answering System

## What it is

A **LangGraph `StateGraph`** in which specialized agents cooperate to answer
plain-language questions about the knowledge graph. Model calls go through the
**OpenAI SDK**. Every graph traversal is a real tool call against
`graph.json` — the agents never read raw source files.

## Topology

```
                    ┌──────────────┐
  user question ──> │  supervisor  │
                    └──────┬───────┘
                    routes 1..3 workers (fan-out, LangGraph conditional edges)
                     ▼        ▼        ▼
               ┌─────────┐ ┌─────────┐ ┌─────────┐
               │ orient  │ │ explorer│ │ impact  │
               └────┬────┘ └────┬────┘ └────┬────┘
                    └───────────┼───────────┘
                                ▼ (fan-in)
                        ┌──────────────┐
                        │ synthesizer  │──> answer
                        └──────────────┘
```

## State

One `AgentState` TypedDict flows through every node:

| Field | Type | Purpose |
|---|---|---|
| `question` | `str` | the user's question |
| `graph_path` | `str` | which `graph.json` to operate on |
| `plan` | `list[str]` | supervisor's routing decision |
| `evidence` | `list[dict]` | tool outputs (reducer: dedup-append) |
| `trace` | `list[str]` | human-readable agent trail (reducer: dedup-append) |
| `needs_more` | `bool` | worker signal for a second hop |
| `answer` | `str` | synthesizer's final output |

The `evidence` and `trace` channels use **Annotated reducers** that merge
lists without duplicates. Because parallel workers write to the same state,
this is what makes fan-out/fan-in deterministic instead of last-write-wins.

## The agents

### supervisor

Reads the question and picks workers. Two implementations behind one interface:

- **LLM planner** (when `OPENAI_API_KEY` is set): a compact system prompt asks
  for strict JSON — `{"workers": [...], "hints": {...}}` — and unknown worker
  names are filtered out.
- **Keyword router** (offline): impact vocabulary ("break", "change",
  "affect", "risk") routes to `impact`; exploration vocabulary routes to
  `explorer`; `orient` is almost always included.

If the LLM call fails for any reason, the keyword router takes over. The agent
graph *never* fails because the planner failed.

### orient

Calls `graph_stats` once. Cheap, and it grounds everything downstream: how
many nodes/edges exist, and which communities (subsystems) the graph found.
The synthesizer uses this to scope claims ("in this 19-node graph…").

### explorer

Answers "what is X / how does X relate to Y":

1. `search_graph(question)` — scored subgraph: exact label hits > prefix >
   substring, each direct hit pulling in one hop of neighborhood.
2. `explain_concept(strongest hit)` — the concept's neighborhood.
3. `find_path(top two hits)` — when two distinct entities match, their
   shortest relationship path.

If nothing matched, it sets `needs_more: true` rather than fabricating.

### impact

Answers "what breaks if X changes":

1. Resolves the target entity (strongest search hit).
2. `find_affected(target)` — BFS over downstream relations (`calls`,
   `inherits`, `imports`, …) up to depth 4, producing the blast radius.
3. `list_neighbors(target)` — the immediate relationship types, useful for
   "who calls this directly".

### synthesizer

Turns collected evidence into the answer. Again two modes:

- **LLM synthesis**: evidence blocks are rendered into the prompt with strict
  grounding rules — cite entity names/files/relations, distinguish
  `EXTRACTED` from `INFERRED`, and say exactly what is missing rather than
  filling gaps.
- **Deterministic fallback**: the evidence digest is the answer — each tool
  result with its hit list, paths, and blast radius rendered in stable text
  form. Always available, zero network.

## The tools

All agents share one tool module; each tool loads nothing globally, takes the
graph explicitly, and returns `ToolResult(name, ok, text, data)`:

| Tool | Wraps | Used by |
|---|---|---|
| `graph_stats` | node/edge/community counts + names | orient |
| `search_graph` | `query.query` scored subgraph | explorer, impact |
| `explain_concept` | `query.explain` neighborhood | explorer |
| `find_path` | `query.path` shortest path | explorer |
| `find_affected` | `query.affected` BFS blast radius | impact |
| `list_neighbors` | `query.neighbors` by relation | impact |

The same primitives back the `kg query|path|explain|affected` CLI commands —
one implementation, two consumers.

## Why a supervisor instead of one ReAct loop?

- **Predictability.** The plan is inspectable state (`plan` field), so a run
  can be replayed and its routing reasoned about.
- **Parallelism.** Workers are independent nodes; LangGraph fans them out in
  one superstep and the reducers merge their evidence.
- **Cost control.** One planning call + one synthesis call + fixed tool
  budget, instead of an unbounded think-act loop.
- **Graceful degradation.** Every LLM touchpoint has a deterministic twin, so
  the same graph runs offline (CI, air-gapped) with reduced quality but no
  failure.

## Failure matrix

| Failure | Behavior |
|---|---|
| no `OPENAI_API_KEY` | keyword router + extractive synthesizer |
| planner returns malformed JSON | keyword router |
| a tool returns no results | `needs_more` set; synthesizer states the gap |
| `graph.json` missing | friendly error pointing at `kg build` |
| OpenAI call raises mid-synthesis | extractive answer + notice appended |

## Running it

```bash
kg ask "how does auth connect to the database"        # one-shot
kg-agent "what breaks if I change the db layer?"      # one-shot with trace
kg-agent                                              # REPL
kg-agent --graph path/to/graph.json "question?"       # explicit graph
```

The trace (stderr) shows the actual routing decisions and which tools ran:

```
agent mode: deterministic (no OPENAI_API_KEY)
  [supervisor -> route to orient, impact, explorer]
  [explorer: ran search_graph, explain_concept]
  [impact: assessed 'AuthService']
  [orient: graph: 19 nodes, 20 edges, 4 communities: ...]
  [synthesizer: extractive fallback]
```
