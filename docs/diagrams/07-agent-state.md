# 07 — State: AgentState Through One `kg ask` Run

The `AgentState` TypedDict is the only channel between agent nodes. This diagram
tracks *the state object*, not the agents.

```mermaid
stateDiagram-v2
    [*] --> Received

    Received: question, graph_path set<br/>evidence=[], trace=[]
    Received --> Planning : app.invoke(state)

    Planning: plan = [workers]<br/>trace += "supervisor -> route"
    Planning --> Dispatching : plan non-empty

    Dispatching --> Collecting : conditional edges fire<br/>all selected workers in parallel

    Collecting: evidence += per-worker ToolResults<br/>trace += "worker: ran …"
    Collecting --> Synthesizing : all worker edges joined<br/>(_merge_list dedups payloads)

    Synthesizing: answer = LLM(cited) or extractive<br/>trace += "synthesizer: …"
    Synthesizing --> Done

    Done: final state read out<br/>answer + trace
    Done --> [*]

    note right of Planning
        LLM plan on success
        keyword router on no-key/error
        same state shape either way
    end note

    note right of Collecting
        worker failures produce ok=false
        evidence, not exceptions:
        the state machine never aborts
    end note
```

## Field evolution

| Phase | `plan` | `evidence` | `answer` |
|---|---|---|---|
| Received | — | `[]` | — |
| Planning | `["orient", "explorer"]` | `[]` | — |
| Collecting | set | 1–5 entries, deduped | — |
| Synthesizing | set | set | extractive or LLM |
| Done | set | set | final string |

The invariants worth keeping when extending this: nodes are **pure functions** of state → partial state; evidence entries always carry `{tool, ok, text, data}` so the deterministic synthesizer can render them without special cases; and `trace` is append-only, which is what makes the REPL output auditable.
