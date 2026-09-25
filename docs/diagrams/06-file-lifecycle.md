# 06 — State: A File's Journey Through the Pipeline

One source file, from disk to report.

```mermaid
stateDiagram-v2
    [*] --> OnDisk
    OnDisk --> Detected : detect() classifies by suffix
    OnDisk --> Skipped : secret gate / ignore file / noise dir
    Skipped --> [*]

    Detected --> Extracting : enters process pool
    Extracting --> Extracted : AST/markdown fragment built
    Extracting --> Failed : exception → error fragment, run survives
    Failed --> [*]

    Extracted --> Validating : validate_extraction()
    Validating --> Cached : valid → ast/{hash}.json
    Validating --> Dropped : schema errors → warning, fragment discarded
    Dropped --> [*]

    Cached --> Merging : build_graph() assembly
    Merging --> Resolved : raw calls → INFERRED edges (0.85)<br/>ambiguous → no edge
    Resolved --> Deduped : label dedup (Jaro-Winkler + union-find)

    Deduped --> SemanticRead : is document/paper + key set
    Deduped --> Clustered : --no-semantic / no key / code file
    SemanticRead --> Clustered : concepts + resolved code_links applied

    Clustered --> Analyzed : community, god nodes, surprises
    Analyzed --> Reported : written into graph.json + GRAPH_REPORT.md
    Reported --> [*]

    Cached --> Cached : --update re-run<br/>(hash unchanged → replay, no re-extract)
    SemanticRead --> SemanticRead : doc re-run with unchanged content<br/>(semantic cache replay, no re-bill)
```

## Why a state diagram here

A file's treatment depends on its type, its history, and two flags — a flowchart of the whole pipeline (diagram 03) shows the happy path, but this shows the **survivorship rules**: which states a file can get stuck in (`Cached`), which are terminal failures (`Skipped`, `Failed`, `Dropped`), and which loops are *free* (cache replay) versus which cost money (semantic extraction, only on content change).
