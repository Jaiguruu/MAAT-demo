# Operations Guide

How to run the system, switch providers, and read every report and metric.
(Model choice is yours — nothing here assumes a specific model.)

---

## 1. Starting the web UI

```bash
kg-ui                        # http://127.0.0.1:8765
kg-ui --port 9000            # different port
kg-ui --repo path/to/repo    # repo the ReAct baseline explores (default: cwd)
```

Open **http://127.0.0.1:8765** in a browser. Three tabs:

| Tab | What you do there |
|---|---|
| **Ask** | Run one question through either system and watch the live decision stream |
| **Graph** | Build the graph for any folder; explore it as a force-directed view |
| **Settings** | Base URL, API key, model selection — all in-memory, nothing written to disk |

### Ask tab — the live decision stream

1. Pick the **system**: `graph` (multi-agent over the knowledge graph) or
   `baseline` (primitive ReAct tool-loop, no graph).
2. Type a question, press **Run**.
3. The **Live decisions** pane shows, in order:
   - `run started` — which system, which model override
   - `supervisor routed to …` — the routing decision and *how* it was made
     (`llm` vs `keyword` fallback) — graph system only
   - `orient/explorer/impact ✓/✗` — each tool call result with a summary
   - `step N read_file …` — each ReAct action and observation preview — baseline only
   - `synthesizer mode: llm|extractive` — how the final answer was produced
   - `done`
4. The **Answer** pane shows the final answer plus per-run metrics
   (baseline: steps taken, tools used, finish reason).

The stream is Server-Sent Events — events appear as they happen, not after.

### Graph tab

- Enter an absolute repo path, toggle the **semantic pass** (LLM) on/off, hit
  **Build graph**. Progress and the result (`done: N nodes, M edges (+K concepts)`)
  appear in the status badge; errors show inline.
- The force view colors nodes by type (code=blue, document=green,
  concept=amber, paper=purple). **Click a node** for its detail: source file,
  location, summary, and every connection with relation + confidence.
- Dashed edges are `INFERRED`, solid are `EXTRACTED`.

### Settings tab

- **Base URL** — any OpenAI-compatible endpoint (blank = default; OpenRouter
  when an OpenRouter key is set). Saved values apply immediately, in-process.
- **API key** — write-only: the UI never echoes it back, only shows whether
  one is set. Keys entered here live in server memory until restart; the
  `.env` file is the persistent alternative.
- **Model** — one box for both layers, or split `semantic` / `agent` models.
  The datalist autocompletes from the provider's live `/models` endpoint
  once a key is saved, plus a curated list of known-good ids.
- **Test connection** — one minimal completion; reports model, latency, and
  the exact provider error on failure.
- `KG_UI_REPO` env var (or `kg-ui --repo`) sets the folder the baseline
  explores.

> Note: values set in Settings override `.env` for the running server only.
> Restarting resets to `.env`. Environment variables still beat everything.

---

## 2. CLI — same engines without the browser

```bash
kg build path/to/repo              # graph build (semantic pass if key present)
kg build path/to/repo --no-semantic # structural only, zero network
kg build path/to/repo --update     # re-extract only changed files
kg query "…" / kg path A B / kg explain A / kg affected A   # offline queries
kg ask "…"                         # multi-agent answer (uses graph.json)
kg-bench path/to/repo --all        # comparison bench (below)
```

---

## 3. Benchmarks — how to run, what lands where

```bash
kg-bench path/to/repo --all                 # built-in 4-question scenario set
kg-bench path/to/repo -q "q1" -q "q2"       # custom questions (repeatable)
kg-bench path/to/repo --all --skip-build    # reuse existing graph.json
kg-bench repo/ --max-steps 12 --model X     # baseline step budget / model override
```

### Output files (all under `kg-out/bench/`)

| File | Contents |
|---|---|
| `COMPARISON.md` | The generated report (regenerated every run, never hand-edit) |
| `runs.json` | Append-only log: one entry per bench run with full summaries |
| `graph-side.json` | Raw metric records, graph system (every LLM call + phases) |
| `baseline-side.json` | Raw metric records, ReAct baseline |

### Reading COMPARISON.md

**Headline table** — LLM calls, total/prompt/completion tokens, estimated
cost, wall time, failed calls, for both systems side by side.

**Per-purpose breakdown** — tokens and calls by the shared vocabulary:

| purpose | graph system | baseline |
|---|---|---|
| `ingest` | kg build (incl. semantic pass) | file scan |
| `plan` | supervisor routing | every "next action" decision |
| `read` | 0 (structure precomputed) | every read/grep |
| `answer` | synthesizer | final synthesis |

`read` tokens are the structural story: the baseline pays them every run,
the graph system zero. Watch how the gap grows with repo size.

**Per-question baseline effort** — steps and tools the baseline needed per
question. `llm_error` rows are real failures (rate limits, timeouts) and
count — they are part of the comparison, not noise to discard.

**Break-even** — the arithmetic that matters:

```
build_tokens      one-time graph build cost (semantic pass), cached afterwards
saving/question   baseline ask-cost − graph ask-cost  (tokens)
amortized after   build_tokens ÷ saving/question      (questions)
```

If the corpus is tiny, expect "no per-question saving at this corpus size" —
that is an honest result, not a bug. The baseline's cost scales with the
codebase; the graph's ask-cost does not.

### Metrics contract

Every LLM call on both sides is recorded by the same ledger
(`knowledgegraph/metrics.py`): purpose, model, prompt/completion tokens,
estimated cost, wall time, error, timestamp. Failed calls are recorded with
zero tokens — nothing silently disappears. Costs come from a built-in price
table; unknown models price at $0.00 rather than guessing. Add prices via:

```bash
# .env
KG_PRICE_OVERRIDES={"your-model-prefix": [0.10, 0.40]}
```

---

## 4. Provider switching (you choose the model)

Everything reads `.env` (copy `.env.example`). The layers the model choice
affects:

| Variable | Controls |
|---|---|
| `OPENAI_MODEL` | default for both layers |
| `KG_SEMANTIC_MODEL` | build-time semantic extraction only |
| `KG_AGENT_MODEL` | supervisor + synthesizer + baseline loop |
| `OPENAI_BASE_URL` | any OpenAI-compatible endpoint |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` | OpenRouter first, then OpenAI |

For the UI, the Settings tab does the same in-memory. For bench runs, set
`.env` before launching so both sides pick the same model.

---

## 5. Troubleshooting quick reference

| Symptom | Meaning | Fix |
|---|---|---|
| `429 free-models-per-day` | OpenRouter free-tier daily cap hit mid-run | switch to a paid model (or add credits) and re-run |
| `$0.0000` cost in reports | model not in the price table | add `KG_PRICE_OVERRIDES` |
| `no graph built yet` | graph.json missing | `kg build …` first (or Graph tab → Build) |
| baseline dies at step 2 | provider rate limit | the loop retries 3× with backoff; if it still fails, the model is exhausted |
| `extractive (no llm)` synthesis | no API key visible to the run | set key in Settings or `.env`, save, re-run |
| UI shows `keyword` routing | supervisor fell back (LLM plan failed or no key) | check Settings → Test connection |

## 6. Test suite (always offline)

```bash
python -m pytest tests/ -q     # 90 tests, no network, ~2.5s
```

The suite forces `api_key -> None` and fails loudly if any code path attempts
a real LLM call. Never trust benchmark numbers produced by test runs — they
are hermetic by design.
