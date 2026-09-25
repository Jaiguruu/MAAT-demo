# 00 — Overview & Reading Order

One system, two questions to answer. The diagrams split accordingly.

## The KG system (primary)

| # | Diagram | UML type | What it nails down |
|---|---|---|---|
| [01](01-use-case.md) | Actors & goals | **Use-case** | who uses `kg`, what each command is *for*, what needs a key |
| [02](02-component.md) | System architecture | **Component** | packages, data-flow boundaries, the `graph.json` contract |
| [03](03-build-activity.md) | `kg build` workflow | **Activity** | detect → extract → build → semantic gate → cluster → analyze → report, incl. cache replay & no-key branch |
| [04](04-ask-sequence.md) | `kg ask` runtime | **Sequence** | supervisor → workers → synthesizer, exactly as `agents/graph.py` wires it |
| [05](05-domain-classes.md) | Domain model | **Class** | Fragment, Node/Edge, Graph, Community, ToolResult, AgentState |
| [06](06-file-lifecycle.md) | A file's journey | **State** | detected → extracted → cached → merged → clustered → analyzed → reported |
| [07](07-agent-state.md) | AgentState machine | **State** | question → plan → evidence → answer transitions inside the LangGraph run |

## Supporting (the benchmark, where it touches the workflow)

| # | Diagram | UML type | What it nails down |
|---|---|---|---|
| [08](08-metric-lifecycle.md) | Metric record lifecycle | **State** | emitted → aggregated → persisted → compared |
| [09](09-react-baseline.md) | ReAct primitive baseline | **Activity** | the no-AST/no-graph tool loop being compared against |
| [10](10-bench-workflow.md) | `kg-bench` run | **Activity** | scenario fan-out → run both systems → emit comparison |

## Conventions

- Mermaid only; renders natively on GitHub and VS Code.
- Diagrams describe *real* code: module names match `knowledgegraph/`, node names match function names in `agents/graph.py`.
- When code changes, update the diagram in the same PR. A wrong diagram is worse than none.
