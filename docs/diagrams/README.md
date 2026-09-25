# UML Diagrams

Source of truth for the system's design, in Mermaid. Start at [00-overview.md](00-overview.md).

| # | Diagram | Type | Covers |
|---|---|---|---|
| 00 | [Overview & reading order](00-overview.md) | index | how the diagrams fit together |
| 01 | [Overall architecture](01-architecture.md) | component | both systems, shared config, outputs |
| 02 | [Baseline pipeline](02-baseline-pipeline.md) | activity | the primitive no-AST/no-graph flow |
| 03 | [Graph pipeline](03-graph-pipeline.md) | activity | the seven-stage graph build flow |
| 04 | [QA sequence](04-qa-sequence.md) | sequence | one question through BOTH systems side by side |
| 05 | [Metrics & cost classes](05-metrics-classes.md) | class | RunMetrics, TokenUsage, cost math, metric sinks |
| 06 | [Metric lifecycle](06-metric-lifecycle.md) | state | what a metric record goes through from call to report |
| 07 | [Agent topology](07-agent-topology.md) | state/component | the existing LangGraph QA topology, redrawn |
| 08 | [Bench workflow](08-bench-workflow.md) | activity | how `kg-bench` runs both systems and emits the comparison |

## Rendering

Mermaid renders natively on GitHub and in VS Code (Markdown Preview Mermaid Support extension). Nothing to install.
