# 08 — State: Metric Record Lifecycle (supporting: benchmark)

One unit of observability — a single LLM call's token/time record — from the
moment the OpenAI SDK returns until it lands in a comparison. This is the
contract between the instrumentation (`baseline/usage.py`, `agents/llm.py`,
`semantic.py`) and the bench reporter.

```mermaid
stateDiagram-v2
    [*] --> Emitted

    Emitted: SDK response parsed<br/>usage.prompt_tokens, completion_tokens,<br/>wall_ms, model, purpose
    Emitted --> Aggregated : call returns OK
    Emitted --> Failed : exception / unparseable

    Failed: record kept, tokens=0<br/>error string attached
    Failed --> Aggregated : errors count in,<br/>but never vanish

    Aggregated: summed per run by purpose<br/>(ingest / plan / read / answer)<br/>+ per-run wall time, call count
    Aggregated --> Persisted : RunMetrics written<br/>to kg-out/bench/*.json

    Persisted --> Compared : reporter joins graph-side<br/>and baseline-side records
    Compared --> [*]

    note right of Aggregated
        purpose enum fixes the comparison:
        same label means same job in both systems
    end note

    note right of Persisted
        JSON only - diffable, greppable,
        no DB, same spirit as graph.json
    end note
```

## The `purpose` vocabulary (the comparison's backbone)

| purpose | baseline (ReAct) | graph system |
|---|---|---|
| `ingest` | file listing/scanning | `kg build` (detect→extract→build) |
| `plan` | agent decides next tool | supervisor routing |
| `read` | each `read_file` call | — (0: graph replaces re-reading) |
| `answer` | final synthesis call | synthesizer call |

**Why a state diagram:** records can't be allowed to silently disappear. A failed call must still be counted (cost of failure is part of the comparison), and a record that never reaches `Compared` is a bug in the harness, not a missing data point. This diagram is the acceptance test for the metrics layer.
