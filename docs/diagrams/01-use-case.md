# 01 — Use-Case: Actors & Goals

```mermaid
graph LR
    subgraph Actors
        DEV((Developer))
        AGENT((AI Agent<br/>via kg-agent))
        CI((CI / Tooling))
    end

    subgraph "kg system"
        UC1([kg build])
        UC2([kg query])
        UC3([kg path / explain / affected])
        UC4([kg ask])
        UC5([kg-agent REPL])
        UC6([Graph Report])
    end

    subgraph "external"
        LLM([OpenAI-compatible LLM])
    end

    DEV --> UC1
    DEV --> UC2
    DEV --> UC3
    DEV --> UC4
    DEV --> UC6

    AGENT --> UC4
    AGENT --> UC5
    CI --> UC1
    CI --> UC2

    UC1 -. "include: semantic pass<br/>(skipped without key / --no-semantic)" .-> LLM
    UC4 -. "include: LLM plan + synthesis<br/>(keyword router + extractive fallback)" .-> LLM
```

## Notes

- **Developer** — the primary actor. Builds the graph once, queries it many times cheaply.
- **AI Agent** — consumes the graph programmatically; never touches the pipeline.
- **CI** — runs `kg build` on a schedule; must never need a network (hence `--no-semantic`).
- Dashed arrows are `<<include>>`: the LLM is invoked only when a key exists, and every LLM touchpoint has a deterministic fallback so no use-case *fails* offline.
