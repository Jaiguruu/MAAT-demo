# 02 — Component: System Architecture

```mermaid
graph TB
    subgraph CLI["CLI layer"]
        MAIN["kg<br/>__main__.py"]
        AGENTCLI["kg-agent<br/>agents/__main__.py"]
    end

    subgraph PIPELINE["Build pipeline"]
        DETECT["detect"]
        EXTRACT["extract<br/>+ extractors/ (AST, markdown, manifest)"]
        BUILD["build (+ symbol_resolution)"]
        SEMANTIC["semantic<br/>(LLM pass)"]
        CLUSTER["cluster"]
        ANALYZE["analyze"]
        REPORT["report"]
    end

    subgraph QA["Query layer (offline, no LLM)"]
        QUERY["query / path / explain / affected"]
    end

    subgraph AGENTS["Agent layer (LangGraph)"]
        GRAPH["agents/graph.py"]
        TOOLS["agents/tools.py"]
        LLMW["agents/llm.py"]
    end

    subgraph SUPPORT["Shared"]
        IDS["ids / security / validate / config / cache / paths"]
    end

    ARTIFACT[("graph.json + GRAPH_REPORT.md<br/>kg-out/")]

    MAIN --> PIPELINE
    MAIN --> QA
    AGENTCLI --> GRAPH
    GRAPH --> TOOLS
    GRAPH --> LLMW
    TOOLS --> QUERY
    PIPELINE --> IDS
    QA --> IDS
    AGENTS --> IDS
    LLMW --> CONFIG_EXT["OpenAI-compatible endpoint"]
    SEMANTIC --> CONFIG_EXT
    PIPELINE --> ARTIFACT
    TOOLS -. reads .-> ARTIFACT
```

## Key boundaries

- **`graph.json` is the contract** between build and query/agent layers. One direction: build writes, everything else reads.
- **The only two modules that touch the network** are `semantic` (build-time) and `agents/llm` (ask-time). Everything else is local.
- `SUPPORT` modules (`ids`, `security`, `validate`, `config`, `cache`, `paths`) are dependency-free leaves; every layer imports them, they import nothing from the layers.
