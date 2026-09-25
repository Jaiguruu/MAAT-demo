# 09 — Activity: ReAct Primitive Baseline (supporting: benchmark)

The comparison target: a competent developer with **no AST, no knowledge graph,
no index** — just filesystem tools and an LLM deciding what to do next.
Deliberately *not* strawmanned: it gets real tools and a real loop; what it
lacks is precomputed structure.

```mermaid
flowchart TD
    START([kg-baseline question]) --> SCAN

    SCAN["ingest: os.walk over repo<br/>(respect .gitignore)<br/>→ file list with sizes"] --> LOOP

    LOOP{"LLM: next action?<br/>(plan step, ReAct)"}

    LOOP -- "list_files" --> LF["return file list"]
    LOOP -- "read_file(path)" --> RF["return file content<br/>(clipped)"]
    LOOP -- "grep(pattern)" --> GR["regex over all files<br/>→ matching lines + paths"]

    LF --> OBS["append to observation log<br/>tokens recorded: purpose=read"]
    RF --> OBS
    GR --> OBS

    OBS --> BUDGET{"iteration &lt; max_steps?<br/>and budget not blown?"}
    BUDGET -- yes --> LOOP
    BUDGET -- no --> FORCE

    FORCE["force final answer<br/>(no more tool calls)"] --> SYNTH

    LOOP -- "finish(answer)" --> SYNTH

    SYNTH["answer step: LLM synthesizes<br/>from the observation log<br/>purpose=answer"] --> OUT([answer + metrics])

    style LOOP fill:#fff3cd,stroke:#856404
    style BUDGET fill:#fff3cd,stroke:#856404
```

## Design rules (what makes the comparison fair)

- **No memory between questions, no index between steps.** Every fact the
  baseline knows, it re-read from the filesystem this run. This is the
  property the graph system is built to eliminate.
- **`grep` is a real tool** (it's what a developer would demand) — but note
  what it cannot do: answer "what *calls* `query_user` across files with
  confidence" without the model reasoning over raw text each time.
- **Bounded**: `max_steps` and a token budget force termination, and the
  forced-answer path still counts all spend. A runaway baseline is a *finding*,
  not a crash to be hidden.
- **Same metric contract** as the graph side (`purpose` vocabulary, diagram 08)
  — identical records, identical aggregation, so the comparison is arithmetic,
  not narrative.

## What the graph side replaces, visibly

| Baseline step | Graph-side equivalent | Difference |
|---|---|---|
| scan + read_file (repeated) | `kg build` once, cached | `read` purpose drops to ~0 at ask-time |
| grep + model reasoning over matches | `find_path` / `find_affected` | deterministic traversal, no tokens |
| plan step per hop | supervisor plans once | fewer, cheaper calls |
| answer from raw observations | answer from structured evidence | same call count, smaller prompt |
