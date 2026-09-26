# KnowledgeGraph System

Turn a folder of code and documents into a **queryable knowledge graph** — then
answer plain-language questions about it with a **LangGraph multi-agent system**
backed by the OpenAI SDK.

```
detect → extract → build → semantic → cluster → analyze → report → graph.json
                                                        │
                                            ┌───────────┴───────────┐
                                            ▼                       ▼
                                  kg query/path/explain     kg ask (multi-agent)
```

## Quick start

```bash
pip install -e ".[dev]"

# 1. build the graph for any folder
kg build path/to/repo            # add --no-semantic to skip LLM calls

# 2. query it (no API key needed)
kg query "how does auth connect to the database"
kg path "AuthService" "DatabasePool"
kg explain "AuthService"
kg affected "auth.py"

# 3. multi-agent answers (needs OPENAI_API_KEY)
export OPENAI_API_KEY=sk-...
kg ask "what breaks if I change the database layer?"

# 4. benchmark it against a primitive ReAct baseline (needs API key)
kg-bench path/to/repo --all
```

## What you get

```
kg-out/
├── graph.json        the full graph (node-link format) — query it, diff it, ship it
└── GRAPH_REPORT.md   god nodes, communities, surprising connections, suggested questions
```

## The graph

- **Nodes** — files, classes, functions, types, document headings, packages,
  and (with an API key) LLM-extracted **concepts** named in your docs.
  Every node is anchored to `source_file` (+ line where available).
- **Edges** — `contains`, `calls`, `imports`, `inherits`, `references`,
  `depends_on`, `method`, … Each edge is tagged:
  - `EXTRACTED` — read directly from the source (confidence 1.0)
  - `INFERRED` — resolved by the pipeline (confidence 0.55–0.95)
  - `AMBIGUOUS` — flagged for review
- **Communities** — the graph is partitioned into subsystems (Louvain/Leiden)
  and each community is named after its structural hub.

## The agent system

`kg ask` runs a supervisor → workers → synthesizer LangGraph:

| Agent | Job | Tools |
|---|---|---|
| supervisor | routes the question | — (plans) |
| orient | grounds the answer in graph shape | `graph_stats` |
| explorer | what is X / how does X relate to Y | `search_graph`, `explain_concept`, `find_path` |
| impact | what breaks if X changes | `find_affected`, `list_neighbors` |
| synthesizer | writes the final answer from evidence | — (LLM or extractive) |

**Works offline too.** Without `OPENAI_API_KEY` the supervisor uses a keyword
router and the synthesizer emits a deterministic evidence digest — the whole
pipeline and every tool still run locally with zero network calls.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | module map, data contracts, design decisions |
| [`docs/pipeline.md`](docs/pipeline.md) | the build stages, stage by stage |
| [`docs/agents.md`](docs/agents.md) | the multi-agent design, state, and fallbacks |
| [`docs/tools-reference.md`](docs/tools-reference.md) | every graph tool, CLI + agent-facing |
| [`docs/benchmark.md`](docs/benchmark.md) | the ReAct baseline comparison, metrics, provider switching |
| [`docs/operations.md`](docs/operations.md) | operating manual: UI, bench, every report and metric |
| [`docs/interview-guide.md`](docs/interview-guide.md) | explain the whole system to a third person |
| [`docs/diagrams/`](docs/diagrams/00-overview.md) | UML set: use-case, component, activity, sequence, class, state |

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -q        # 63 unit tests, no network
```

## Privacy

Code is parsed locally with tree-sitter. With no `OPENAI_API_KEY` in the
environment, graph building makes zero network calls. With a key, one extra
thing happens: the **semantic pass** sends each document's text to an
OpenAI-compatible endpoint to extract concept nodes (`kg build --no-semantic
turns this off`). Code files are never sent — only documents, and only after
the secret-detection gate has dropped credential files. No telemetry, no
usage tracking.
