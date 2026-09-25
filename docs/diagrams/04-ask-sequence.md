# 04 — Sequence: `kg ask` Runtime

Faithful to `agents/graph.py`. Dashed box = optional LLM path with deterministic fallback.

```mermaid
sequenceDiagram
    autonumber
    actor U as User / Agent
    participant CLI as kg-agent
    participant SUP as supervisor
    participant ORI as orient
    participant EXP as explorer
    participant IMP as impact
    participant TOOLS as tools.py<br/>(graph tools)
    participant SYN as synthesizer

    U->>CLI: kg ask "what breaks if I change the db layer?"
    CLI->>SUP: invoke(state{question, graph_path})

    note over SUP: LLM available? plan via LLM.<br/>No key → _keyword_plan(question)

    SUP->>ORI: route (always worth including)
    SUP->>EXP: route (what/relates questions)
    SUP->>IMP: route (break/impact questions)

    par orient
        ORI->>TOOLS: tool_graph_stats(G)
        TOOLS-->>ORI: nodes/edges/communities
    and explorer
        EXP->>TOOLS: tool_search(question)
        TOOLS-->>EXP: matched entities
        EXP->>TOOLS: tool_explain(top hit)<br/>tool_find_path(a, b) if 2 hits
        TOOLS-->>EXP: evidence
    and impact
        IMP->>TOOLS: tool_affected(target)<br/>tool_neighbors(target)
        TOOLS-->>IMP: blast radius
    end

    ORI-->>SYN: evidence (deduped via _merge_list)
    EXP-->>SYN: evidence
    IMP-->>SYN: evidence

    alt LLM available
        SYN->>SYN: llm.chat(SYNTH_SYSTEM, evidence_block)
        SYN-->>CLI: cited, concise answer
    else no key / LLM error
        SYN->>SYN: _deterministic_answer(evidence)
        SYN-->>CLI: extractive evidence digest
    end

    CLI-->>U: answer + agent trace
```

## Notes

- The three workers run in **parallel** (`add_conditional_edges` fan-out from supervisor) — LangGraph merges their partial state via the `_merge_list` reducer, which dedups evidence payloads.
- Evidence is capped: `_slim()` truncates list payloads to 25 items before they reach the synthesizer prompt — tool output never blows the context window.
- The `needs_more` flag exists for a second exploration hop but the current topology routes straight to synthesis; the flag is plumbed and unused by design.
