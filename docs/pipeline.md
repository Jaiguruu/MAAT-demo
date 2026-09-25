# The Pipeline, Stage by Stage

    detect → extract → build → cluster → analyze → report → graph.json

Each stage is a single function with a typed input and output. Run them all
with `kg build <path>`, or import the stages directly — there is no hidden
state.

---

## Stage 1 — `detect`: what will the graph even look at?

**In:** a corpus root path. **Out:** typed file lists + corpus health stats.

- Walks the tree with `os.walk`, pruning noise directories in place
  (`node_modules`, venvs, build outputs, our own `kg-out/`).
- Honors `.gitignore` and a project-local `.kgignore` with gitignore
  semantics: last-match-wins, `!` negation, parent-exclusion.
- **Classifies** every remaining file by suffix into `code | document | paper |
  image`. A `.md` file that smells like an academic paper (≥3 signals in the
  first 3000 chars: arXiv ids, DOIs, "we propose", numbered citations) is
  reclassified as `paper`.
- **Skips secrets** at three gates: sensitive parent dirs (`.ssh`, `secrets/`,
  …), credential filename patterns (`.env`, `*.pem`, `id_rsa`, …), and
  load-bearing keyword names — `api_token.txt` is skipped, but
  `tokenizer.py` and `docs/token-economics.md` are not (the keyword must end
  the stem or the name must be short).
- Emits corpus health: total files, word count, and a warning when the corpus
  is too small for a graph to pay off.

Every file dropped here is dropped forever — that is the point. Extraction
never sees a venv, a lockfile, or a credential store.

---

## Stage 2 — `extract`: files → node/edge fragments

**In:** the typed file lists. **Out:** one *fragment* per file
(`{nodes, edges, raw_calls, definitions}`).

Two extractor families:

**Structural (tree-sitter AST, no LLM, no network).** For Python, JS/TS, Go
and Rust:

| Extracted | Becomes |
|---|---|
| file itself | file anchor node (`{dir}_{stem}` id) |
| class / struct / trait / interface | node, `contains` from the file |
| function / method | node (`name()`), `contains` from the file |
| import statements | `imports` edge to a module node |
| base classes / trait impls | `inherits` edge |
| Go receivers | `method` edge from receiver type |
| call sites | recorded in `raw_calls` for the resolution pass |
| type annotations | `references` edges to user-defined types |

**Document (markdown).** Headings become nodes chained with `contains`
(file → H1 → H2 …); `[text](target.md)` links and `[[wikilinks]]` become
`references` edges between document nodes, so cross-doc structure survives.

Key robustness properties:

- Every edge target that this fragment doesn't define gets a **stub node**, so
  a fragment is always self-consistent and passes schema validation. When the
  real definition arrives from its own file's fragment (same canonical id),
  the stub is upgraded in place.
- One file failing to parse produces an empty fragment with an `error` key.
- Files run through a **process pool** (order-preserving), so tree-sitter
  parsing parallelizes past the GIL.

**Package manifests** (`pyproject.toml`, `package.json`, `go.mod`) get a
deterministic extractor: one package hub node per package plus `depends_on`
edges — a dependency referenced from many manifests collapses to one node.

---

## Stage 3 — `build`: fragments → the graph

**In:** validated fragments. **Out:** a NetworkX graph.

1. **Schema gate.** Every fragment passes `validate.py`: required fields,
   legal `file_type` / `confidence` values, edge endpoints resolving inside
   the fragment. Invalid fragments are dropped with a warning.
2. **Assembly.** Nodes and edges are added idempotently; a definition with a
   concrete `source_location` outranks a stub at the same id.
3. **Cross-file call resolution (second pass).** Stage 2 recorded raw call
   sites (`caller`, `name`) without guessing targets. Now, with the whole
   corpus in one graph, a label index of *code-class* symbols is built and
   each raw call resolves:
   - exactly one candidate → `calls` edge, `INFERRED`, score **0.85**
   - zero or multiple candidates → no edge (conservative: never guess)
   This is where `login()` in `auth.py` gains an edge to the `query_user()`
   defined in `db.py`.
4. **Fuzzy label dedup.** Same-entity-different-fragment duplicates are merged:
   normalized-label blocking → Jaro-Winkler verification → union-find merge
   with edge rewiring. Code nodes anchored to different source locations never
   merge (a class in `a.py` and a same-named doc heading are *not* duplicates).

---

## Stage 3b — `semantic`: an LLM reads the documents

**In:** document/paper paths + the built graph. **Out:** concept nodes and
INFERRED edges folded into the graph.

Structural extraction never reads *prose*. The semantic pass closes that gap:
an LLM reads each document inside an untrusted-source wrapper and returns a
JSON fragment of the same schema every extractor uses:

- **concept nodes** — ideas, patterns, decisions, constraints named in prose
- **intra-concept edges** — `references` / `semantically_similar_to`, always
  `INFERRED` with a 0.0–1.0 confidence score
- **raw `code_links`** — claims like "this document's Token Flow section is
  about `AuthService`". These are stored *unresolved* in the per-document
  cache and resolved against the built graph at apply time: exact bare-name
  match against the code-label index, ambiguous names skipped, missing
  targets skipped. Keeping them raw means a code rename does not invalidate
  the document's cached fragment.

Every fragment passes the same `validate.py` gate as structural output — the
LLM is a contributor under contract, not a privileged one. Invalid fragments
are dropped. The whole pass is skipped silently when no API key is configured,
and per-document results cache under the content hash so re-runs re-bill
nothing. Disable with `--no-semantic`.

## Stage 4 — `cluster`: subsystems

**In:** the graph. **Out:** `{community_id: [node_ids]}` + labels annotated on
the graph.

- Community detection via **Louvain** (networkx builtin) with a fixed seed;
  the module transparently upgrades to **Leiden** (graspologic) when installed.
- **Oversized communities** (>25% of nodes, min 10) are re-split with a second
  pass on their subgraph.
- **Isolates** become single-node communities; excluded super-hubs are
  reattached by majority vote of their neighbors.
- Each community is **named after its structural hub** (highest-degree member,
  ties broken by id) — deterministic, LLM-free labels like `auth.py` or
  `DatabasePool` instead of "Community 7".
- Per-community **cohesion** (internal-edge fraction) is recorded for the
  report.

---

## Stage 5 — `analyze`: what does the graph say?

**In:** graph + communities. **Out:** analysis dict.

- **God nodes** — the most-connected *real* entities. File anchors, method
  stubs, builtin-type names, and JSON key noise are excluded so the ranking
  names actual abstractions.
- **Surprising connections** — cross-file, cross-community edges, ranked
  `AMBIGUOUS → INFERRED → EXTRACTED` (surprise first). In single-source
  corpora it falls back to high-betweenness bridge edges.
- **Knowledge gaps** — code nodes nothing references (candidate dead code or
  missing wiring).
- **Suggested questions** — derived from god nodes and community names; the
  questions this graph is uniquely positioned to answer.
- **Confidence split** — the EXTRACTED/INFERRED/AMBIGUOUS mix, so a reader
  always knows how much of the graph is fact vs inference.

---

## Stage 6 — `report` + persistence

**In:** everything above. **Out:** `kg-out/GRAPH_REPORT.md` + `kg-out/graph.json`.

The report is the human-readable audit trail: corpus verdict, headline
numbers, god nodes table, community listing with cohesion, surprising
connections with files, knowledge gaps, suggested questions, and a legend
explaining the confidence tags.

`graph.json` is the machine artifact in node-link format — the *only* input
the query layer and the agent system ever need. Re-reading the corpus for a
question is never necessary.

---

## Incremental rebuilds (`--update`)

Every file is fingerprinted by SHA-256; a stat index (size + mtime_ns → hash)
skips re-reading untouched files. Fragments are persisted under their content
hash, so:

```
run 1: extract 3 files
run 2: replay 3 cached fragments (0 parses)
run 3 after editing b.py: replay 2, extract 1
```

Changed fragments replace their old nodes on rebuild; unchanged ones are
byte-identical, which makes graphs diffable across commits.
