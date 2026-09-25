# Architecture

## One-line summary

A staged pipeline converts a filesystem corpus into a validated knowledge
graph (`graph.json`), and a LangGraph multi-agent system turns natural-language
questions into graph traversals over that artifact.

## Design principles

1. **Stages over monoliths.** Each stage is one function in its own module,
   consuming plain dicts and NetworkX graphs. No shared mutable state between
   stages; every intermediate is inspectable.
2. **Deterministic first.** Graph building uses zero LLM calls. Code is parsed
   with tree-sitter ASTs; docs are parsed structurally. The LLM appears only in
   the *answer* layer, and even there the system degrades to a deterministic
   fallback without a key.
3. **Honest edges.** Every relationship carries `EXTRACTED` (read from source),
   `INFERRED` (resolved with a score), or `AMBIGUOUS` (needs review). Downstream
   consumers can always tell evidence from guesswork.
4. **One identity recipe.** All node IDs flow through a single normalization
   function (`ids.normalize_id`), so independently-produced fragments merge
   instead of splitting entities into ghost duplicates.
5. **Fail soft per file, fail loud per stage.** A single broken file yields an
   empty fragment with an `error` key; it never kills a corpus run. Schema
   violations, however, are surfaced, not swallowed.

## Module map

```
knowledgegraph/
├── detect.py            Stage 1  filesystem walk → typed file lists
├── extract.py           Stage 2a dispatcher + parallel execution
├── extractors/
│   ├── code_extractor.py  tree-sitter AST extraction (py/js/ts/go/rust)
│   ├── markdown.py        heading/link extraction for documents
│   ├── engine.py          shared node/edge factories
│   └── ts_loader.py       grammar pack loader
├── validate.py          extraction schema gate
├── build.py             Stage 3  fragments → NetworkX graph + dedup
├── symbol_resolution.py cross-file call resolution (second pass)
├── cluster.py           Stage 4  community detection + labeling
├── analyze.py           Stage 5  god nodes, surprises, gaps, questions
├── report.py            Stage 6  GRAPH_REPORT.md rendering
├── cache.py             SHA-256 content-hash extraction cache
├── pipeline.py          stage orchestrator
├── query.py             traversal primitives (query/path/explain/affected)
├── ids.py               canonical node-ID normalization
├── paths.py             output locations
├── security.py          label sanitization, file-type normalization
├── __main__.py          the `kg` CLI
└── agents/
    ├── graph.py         LangGraph: supervisor, workers, synthesizer
    ├── state.py         AgentState + list-merge reducers
    ├── tools.py         graph tools exposed to agents
    ├── llm.py           OpenAI SDK wrapper (optional)
    └── __main__.py      the `kg-agent` CLI / REPL
```

## Data contracts

### Extraction fragment (stage 2 output)

```json
{
  "nodes": [
    {"id": "authservice", "label": "AuthService", "file_type": "code",
     "source_file": "auth.py", "source_location": "L5"}
  ],
  "edges": [
    {"source": "auth_py", "target": "authservice", "relation": "contains",
     "confidence": "EXTRACTED", "source_file": "auth.py",
     "source_location": "L5", "weight": 1.0}
  ],
  "raw_calls":   [{"caller": "auth_py", "name": "query_user", "location": "L10"}],
  "definitions": {"authservice": "authservice"}
}
```

`validate.py` enforces: required fields per node/edge, `file_type` in the
canonical set, `confidence` in `{EXTRACTED, INFERRED, AMBIGUOUS}`, and edge
endpoints resolving to node ids *within the same fragment*. Fragments that fail
are dropped with a warning — one bad fragment cannot poison the graph.

### The graph artifact (`graph.json`)

NetworkX node-link format. Node attributes: `id`, `label`, `file_type`,
`source_file`, `source_location`, `community`. Edge attributes: `relation`,
`confidence`, optional `confidence_score`, `source_file`, `source_location`,
`weight`. Graph-level attributes: `community_labels`, `cohesion`, `hyperedges`.

## Node identity

Three independent producers (AST extractors, markdown extractor, semantic
stubs) must agree on node IDs. The recipe, in `ids.py`:

```
NFKC normalize → non-word runs → "_" → collapse "_" → strip "_" → casefold
```

Two guarantees make cross-file resolution safe:
- File-level nodes always get the id `{parent_dir}_{stem}` (top-level: `{stem}`).
- `stub` nodes emitted for externally-referenced symbols are upgraded in place
  when the real definition arrives in another fragment (same id, same recipe).

## Confidence model

| Label | Score | How it is produced |
|---|---|---|
| `EXTRACTED` | 1.0 | read directly from source (import, call site, heading, link) |
| `INFERRED` 0.85 | cross-file call resolved to exactly one definition |
| `INFERRED` < 0.85 | weaker evidence tiers (reserved for future resolvers) |
| `AMBIGUOUS` | — | multiple plausible targets; flagged, not guessed |

The resolution pass is **conservative by design**: an ambiguous call name adds
no edge rather than a wrong one.

## Concurrency

Extraction fans out through a `ProcessPoolExecutor` (bypasses the GIL for
tree-sitter parsing). Results keep input order for deterministic caching and
merging. Clustering and analysis are single-process; community detection is
seeded (`seed=42`) so partitions are reproducible run-to-run.

## Error handling

| Layer | Policy |
|---|---|
| one file fails to parse | empty fragment + `error` key; run continues |
| fragment fails schema | dropped with warning; run continues |
| edge references unknown node | dropped at build time (endpoints must exist) |
| graph missing at query time | CLI exits 1 with "run `kg build` first" |
| agent LLM call fails | falls back to deterministic evidence digest |

## Security

- File discovery skips credential stores: sensitive parent dirs (`.ssh`,
  `secrets/`, …), key/cert extensions, and load-bearing keyword filenames.
- Ignore files (`.gitignore` + `.kgignore`) are honored, last-match-wins with
  `!` negation.
- Node labels pass through `sanitize_label` (control chars stripped, length
  capped, HTML-escaped) before entering the graph or any LLM prompt.
- The corpus path is the only filesystem input; the query layer reads only
  `graph.json`.
