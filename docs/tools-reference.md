# Tools Reference

Every capability the graph exposes, in both surfaces: the `kg` CLI and the
agent tool layer. Both call the same `knowledgegraph.query` primitives.

## Prerequisites

All query commands need a built graph:

```bash
kg build <path>          # produces kg-out/graph.json
```

---

## CLI commands

### `kg build <root>`

Runs the full pipeline. Options:

| Flag | Effect |
|---|---|
| `--update` | re-extract only files whose content hash changed |
| `--directed` | build a DiGraph (preserve edge direction) |
| `--no-viz` | skip the markdown report |
| `--resolution <f>` | community granularity (>1 = more, smaller communities) |
| `--max-workers <n>` | parallel extraction processes |
| `--no-semantic` | skip the LLM semantic pass (no API calls, no API key needed) |

### `kg query "<question>" [--budget N]`

Scored subgraph for a plain-language question. Scoring: exact label match (5.0)
> prefix/substring (2.0); each direct hit pulls one hop of neighborhood until
the budget (default 60 nodes) is spent. Output: direct hits, then the
subgraph's nodes and edges with confidence tags.

### `kg path "<A>" "<B>" [--max-hops N]`

Shortest relationship path between two named entities (undirected traversal,
default max 6 hops). Each hop prints `source --relation--> target
[confidence]`.

### `kg explain "<name>"`

One concept plus its neighborhood: source file and line, community, degree,
and connections sorted by the neighbor's own degree (in/out direction shown).

### `kg affected "<name>" [--depth N]`

Blast radius: BFS over downstream relations (`calls`, `imports`, `inherits`,
`references`, `contains`, `method`, …) to `--depth` (default 4). Indented by
hop, each line shows the relation it arrived via.

### `kg ask "<question>" [--graph path]`

The multi-agent answer (see `docs/agents.md`). Requires `OPENAI_API_KEY` for
LLM synthesis; runs deterministically without it.

---

## Entity name resolution

All lookup commands resolve names the same way, in order:

1. exact node id (`auth_py`)
2. exact label, case-insensitive (`AuthService`)
3. bare callable label (`login` matches `login()`)
4. unique substring (`authserv` when unambiguous)

Ambiguous substrings resolve to the first candidate deterministically.

---

## Agent-facing tools

Defined in `knowledgegraph/agents/tools.py`. Each returns
`ToolResult(name, ok, text, data)` where `text` is the compact prompt-ready
rendering and `data` is the structured payload.

| Tool | Signature | Wraps | Notes |
|---|---|---|---|
| `graph_stats` | `(G)` | — | node/edge counts, community names |
| `search_graph` | `(G, question, budget=40)` | `query.query` | matched labels + subgraph |
| `find_path` | `(G, a, b)` | `query.path` | hops list or `ok=False` |
| `explain_concept` | `(G, name)` | `query.explain` | degree, community, connections |
| `find_affected` | `(G, name, depth=3)` | `query.affected` | depth-annotated hit list |
| `list_neighbors` | `(G, name, relation=None)` | `query.neighbors` | relation-filterable |

Tool payloads passed to the synthesizer are slimmed (`_slim`: lists capped at
25 items) to bound context growth.

---

## Relations emitted by the extractors

| Relation | Producer | Confidence |
|---|---|---|
| `contains` | AST (file/class/function) and markdown (headings) | EXTRACTED |
| `imports` | import/include/use statements | EXTRACTED |
| `calls` | call sites, resolved within file or cross-file | EXTRACTED / INFERRED (0.85) |
| `inherits` | base classes, trait impls | EXTRACTED |
| `method` | Go receivers | EXTRACTED |
| `references` | type annotations, doc links, wikilinks | EXTRACTED |
| `depends_on` | package manifests | EXTRACTED |

## Confidence tags

| Tag | Meaning |
|---|---|
| `EXTRACTED` | read directly from source; score 1.0 |
| `INFERRED` | resolved by the pipeline; carries `confidence_score` |
| `AMBIGUOUS` | multiple plausible targets; flagged, never guessed |
