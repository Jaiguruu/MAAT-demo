# Benchmarking: Graph System vs ReAct Baseline

`kg-bench` answers one question with data instead of opinions: **does
precomputing a knowledge graph beat a competent agent with only raw
filesystem tools?**

The baseline (`knowledgegraph/baseline/react.py`) is a ReAct tool-loop with
`list_files` / `read_file` / `grep` — no AST parsing, no graph, no index. It
is deliberately *not* strawmanned: real tools, bounded steps, same model as
the graph side. What it lacks is exactly what the graph system precomputes:
**structure**.

## Quick start

```bash
# one question, both systems
kg-bench examples/sample_repo --question "how does auth connect to the database"

# built-in scenario set (4 questions)
kg-bench examples/sample_repo --all

# reuse an existing graph (build cost already paid)
kg-bench repo/ --all --skip-build

# outputs land in kg-out/bench/
#   COMPARISON.md     human-readable comparison (generated, never hand-edited)
#   runs.json         append-only log of every run
#   graph-side.json   raw metric records, graph system
#   baseline-side.json raw metric records, baseline
```

## What is measured

Every LLM call on **both** sides is recorded by the same ledger
(`knowledgegraph/metrics.py`) under a shared purpose vocabulary:

| purpose | baseline (ReAct) | graph system |
|---|---|---|
| `ingest` | file listing/scanning | `kg build` incl. semantic pass |
| `plan` | each "next action" call | supervisor routing |
| `read` | each `read_file`/`grep` | — (0: structure is precomputed) |
| `answer` | final synthesis | synthesizer call |

Per run the report shows: LLM calls, prompt/completion/total tokens, estimated
cost (USD), wall time, failed calls, per-purpose breakdown, per-question
baseline effort (steps + tools used), and a **break-even analysis**: how many
questions until the one-time graph build cost is amortized by the per-question
saving.

Failed calls still count (tokens 0, error recorded) — the cost of failure is
part of the comparison, never silently dropped.

## Reading the numbers honestly

- **Tiny repos favor the baseline.** A 4-file sample has nothing to index; the
  graph's ask-time prompt is *bigger* than the baseline's observations. The
  advantage appears with corpus size: the baseline's `read` tokens grow with
  the codebase, the graph's do not.
- **Build cost is one-time and cached** (content-hash keyed, both structural
  and semantic caches). Baseline cost is per-question and never cached.
- **Costs are estimates** from a built-in price table. Unknown models price at
  $0 rather than guessing — set `KG_PRICE_OVERRIDES` (see `.env.example`) for
  any model not covered.
- **Rate limits are real failures.** Free-tier models (`:free` on OpenRouter)
  have daily caps that kill mid-bench runs with 429s. The ReAct loop retries
  with backoff, but for clean numbers use a paid model (see below).
- **Wall time is dominated by provider latency**, not our code — compare calls
  and tokens for the structural story; treat wall time as secondary.

## Switching providers / models

Everything routes through `knowledgegraph/config.py` reading `.env`
(see `.env.example` for OpenRouter, OpenAI, Groq, DeepSeek, Together, and
local Ollama recipes):

```bash
cp .env.example .env    # then fill in one provider block
```

- `OPENAI_MODEL` sets both layers; `KG_SEMANTIC_MODEL` / `KG_AGENT_MODEL`
  override individually.
- `OPENAI_BASE_URL` points at any OpenAI-compatible endpoint.
- `KG_PRICE_OVERRIDES` (JSON) prices models missing from the built-in table.

For bench runs prefer a cheap paid model (Gemini 2.0 Flash ≈ $0.10/1M input;
the whole 4-question bench on the sample repo costs a fraction of a cent).

## Design decisions

1. **Same model, same questions, same order** on both sides — the comparison
   is arithmetic, not narrative.
2. **Graph build cost is reported separately** (`ingest`), never hidden, so
   the break-even computation is honest.
3. **The baseline gets a step budget** (`--max-steps`, default 12) and a
   forced-answer path — a budget exhaustion is a *finding* (the model couldn't
   navigate the repo), and all spend up to that point still counts.
4. **`runs.json` is a log, not a snapshot** — re-running the bench appends
   evidence; nothing is overwritten.

## Diagrams

- `docs/diagrams/09-react-baseline.md` — the baseline loop (activity)
- `docs/diagrams/10-bench-workflow.md` — the bench run end to end (activity)
- `docs/diagrams/08-metric-lifecycle.md` — the metric record contract (state)
