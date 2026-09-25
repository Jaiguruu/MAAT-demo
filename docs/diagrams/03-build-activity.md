# 03 — Activity: `kg build` Workflow

Faithful to `pipeline.py`, including the cache and semantic gates.

```mermaid
flowchart TD
    START([kg build root]) --> DETECT

    DETECT["**Stage 1: detect**<br/>walk tree, honor .gitignore/.kgignore,<br/>bucket code/document/paper,<br/>drop secrets at 3 gates"] --> EXTRACT

    EXTRACT{"**Stage 2: extract**<br/>--update and<br/>content hash unchanged?"}
    EXTRACT -- yes --> REPLAY["replay cached fragments"]
    EXTRACT -- no --> POOL["process pool:<br/>AST extract per code file<br/>markdown extract per doc"]
    POOL --> SAVEMETA["save fragments to ast/ cache"]
    REPLAY --> MANIFEST
    SAVEMETA --> MANIFEST

    MANIFEST["fold package manifests<br/>(pyproject/package.json/go.mod)"] --> BUILD

    BUILD["**Stage 3: build**<br/>validate every fragment → assemble<br/>→ resolve raw calls (INFERRED 0.85)<br/>→ fuzzy label dedup (Jaro-Winkler)"] --> SEMKEY

    SEMKEY{"**Stage 3b: semantic**<br/>API key configured?<br/>AND not --no-semantic?"}
    SEMKEY -- "no" --> SKIP["skip silently<br/>(offline mode)"]
    SEMKEY -- "yes" --> SEMCACHE{"doc content hash<br/>in semantic/ cache?"}
    SEMCACHE -- hit --> APPLY
    SEMCACHE -- miss --> LLMCALL["parallel LLM calls<br/>untrusted-source wrapper → JSON fragment<br/→ validate gate"]
    LLMCALL --> CACHEWRITE["persist fragment<br/>under content hash"]
    CACHEWRITE --> APPLY
    APPLY["apply: add concept nodes,<br/>resolve raw code_links against<br/>label index (ambiguous → skip)"]

    SKIP --> CLUSTER
    APPLY --> CLUSTER

    CLUSTER["**Stage 4: cluster**<br/>Louvain communities +<br/>hub labels + cohesion"] --> ANALYZE

    ANALYZE["**Stage 5: analyze**<br/>god nodes, surprising connections,<br/>knowledge gaps, suggested questions"] --> REPORT

    REPORT["**Stage 6: report**<br/>graph.json (node-link)<br/>+ GRAPH_REPORT.md"] --> DONE([done])

    style SEMKEY fill:#fff3cd,stroke:#856404
    style SKIP fill:#d1ecf1,stroke:#0c5460
```

## Points worth calling out

- **Failure isolation**: any single file/fragment failure becomes an `error`-keyed empty fragment — the run never dies.
- **Two independent caches**: `ast/` (structural, hash-keyed) and `semantic/` (LLM, content-hash-keyed). Re-runs re-bill nothing on either side.
- **The semantic gate is the only branch that can cost money**, and it is double-gated: key present AND flag not passed.
