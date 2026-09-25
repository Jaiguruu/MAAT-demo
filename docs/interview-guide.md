# Interview Guide: Explaining This System to a Third Person

A complete walkthrough you can give in 5 minutes, with a 30-second elevator
pitch, the detailed narrative, and answers to likely follow-up questions.

---

## 30-second pitch

> "The system turns a folder of code and documents into a knowledge graph —
> files, classes, functions, doc headings as nodes; calls, imports,
> inheritance, references as edges — built entirely with deterministic parsing,
> no LLM in the build path. Then a LangGraph multi-agent system answers
> natural-language questions by traversing that graph instead of re-reading
> the source. Every edge is honest: tagged EXTRACTED if it was read from the
> source, INFERRED with a confidence score if the pipeline resolved it. The
> result is a queryable artifact: `kg path 'AuthService' 'DatabasePool'` shows
> exactly how two concepts connect, and `kg ask` runs a supervisor-worker agent
> team over the same graph."

---

## The 5-minute walkthrough

### 1. The problem

> "If you want to know how a codebase works, your options today are grep —
> which gives you text matches, not structure — or feeding files into an LLM,
> which is expensive, lossy, and non-deterministic. And every question re-reads
> the same files. The insight is that a codebase is already a graph; you just
> have to extract it once."

### 2. The pipeline — six stages

> "Building the graph is a staged pipeline where each stage is one pure
> function:
>
> **Detect** walks the folder, respects gitignore, skips secrets and noise, and
> classifies every file — code, document, paper, image. This stage decides what
> the rest of the pipeline ever sees.
>
> **Extract** parses each file. Code goes through tree-sitter ASTs — fully
> deterministic, no API calls — producing class/function nodes, import edges,
> call sites. Markdown headings and links become nodes and reference edges.
> Each file yields a small *fragment*; extraction fans out over a process pool.
>
> **Build** validates every fragment against a schema, then assembles the
> graph. The interesting part is the *second pass*: call sites were recorded
> without guessing targets during extraction. Now that the whole corpus is one
> graph, a label index resolves each call to its definition — but only when
> there's exactly one candidate. Ambiguous calls get no edge rather than a
> wrong edge. Then a fuzzy dedup pass merges same-entity duplicates across
> fragments.
>
> **Cluster** runs community detection — Louvain, optionally Leiden — to find
> subsystems, splits oversized communities, and names each one after its
> structural hub. So you get human-readable subsystems like 'auth.py' and
> 'db.py' instead of 'Community 7'.
>
> **Analyze** finds the god nodes — the most-connected real abstractions —
> surprising cross-file connections, knowledge gaps like unreferenced code, and
> suggested questions.
>
> **Report** writes a human-readable audit trail plus `graph.json`, which is
> the *only* artifact everything downstream needs."

### 3. The honesty model

> "Every edge carries a confidence tag. EXTRACTED means we read it from the
> source — an import statement, a call site. INFERRED means the pipeline
> resolved it, with a numeric score. AMBIGUOUS means flagged for review. That
> matters because a graph that silently guesses relationships poisons every
> answer built on it."

### 4. The query layer

> "On top of the graph sit four traversal primitives: query returns a scored
> subgraph for a natural-language question; path finds how two entities
> connect; explain gives one concept plus its neighborhood; affected computes
> blast radius via BFS. All four are pure graph operations on graph.json —
> re-reading source files is never necessary. That's the whole value
> proposition: build once, query many times."

### 5. The agent system

> "Questions that need *composition* of those primitives — 'what breaks if I
> change the database layer?' — are handled by a LangGraph multi-agent system.
> A supervisor routes the question; workers each own one concern: orient
> grounds the answer in graph shape, explorer finds what-is and
> how-does-it-connect using search/explain/path, impact computes blast radius.
> All workers write tool outputs into a shared typed state with reducers that
> dedup-merge parallel writes. A synthesizer then turns collected evidence into
> the answer through the OpenAI SDK, with strict grounding rules: cite entities,
> distinguish EXTRACTED from INFERRED, and state what's missing rather than
> filling gaps.
>
> Two things I'd highlight. First, every LLM touchpoint has a deterministic
> twin — the supervisor falls back to a keyword router, the synthesizer to an
> extractive digest — so the system runs offline and in CI with reduced quality
> but zero failures. Second, the same tool layer backs both the CLI and the
> agents: one implementation of query/path/explain, two consumers."

### 6. Why this beats RAG-with-embeddings here

> "Embedding-based RAG finds *similar text*; this finds *actual structure*. 'How
> does X connect to Y' is a graph path question — an embedding similarity
> search can't answer it, a BFS can, deterministically and for free. Also, no
> embeddings means no vector store to maintain and no drift when code changes;
> the graph is rebuilt from source and the relationships are always literally
> true."

---

## Likely follow-up questions

**"How do you keep node identity consistent across extractors?"**
> "One canonical recipe in one module: NFKC normalization, non-word runs to
> underscores, casefold. Three producers — AST extractors, the markdown
> extractor, cross-file stubs — all call the same function. Stubs for
> externally-referenced symbols are emitted at the canonical id and upgraded in
> place when the real definition arrives. Without this you get ghost
> duplicates: the same class appearing as two disconnected nodes."

**"Why conservative call resolution?"**
> "A wrong edge is worse than a missing edge. Everything downstream — god
> nodes, blast radius, agent answers — trusts the edges. So a call name that
> matches multiple definitions gets no edge and, at higher tiers, an AMBIGUOUS
> flag instead of a guess."

**"How does it scale?"**
> "Extraction is embarrassingly parallel — process pool over files. The fuzzy
> dedup blocks candidates by normalized-label buckets, so it's near-linear, not
> O(n²) over nodes. Community detection is seeded for reproducibility. The
> artifact is a single JSON file, which is also its limit — for very large
> corpora you'd move to a graph store, but the stage boundaries wouldn't
> change."

**"How is the incremental update safe?"**
> "Content-hash fingerprints, not timestamps: a stat index of size+mtime avoids
> re-reading untouched files, and fragments are stored under their SHA-256.
> Rebuild replays cached fragments byte-identically and re-extracts only
> changed files, so graphs are diffable across commits."

**"Why a supervisor pattern rather than one ReAct loop?"**
> "Predictability, parallelism, and cost. The plan is inspectable state; workers
> fan out in one LangGraph superstep; the tool budget is fixed per run instead
> of an unbounded think-act loop. And since each worker owns one concern, the
> failure modes are isolated — one worker finding nothing doesn't corrupt the
> others' evidence."

**"What would you add next?"**
> "Semantic extraction for docs via an LLM pass with the same fragment schema
> and confidence tags; a hyperedge layer for group relationships; an HTML
> force-directed visualization; and MCP exposure so any IDE assistant can call
> the same six tools."

---

## Demo script (2 minutes, terminal)

```bash
kg build examples/sample_repo        # six stages, prints node/edge/community counts
kg query "how does auth connect to the database"
kg path "AuthService" "DatabasePool" # the actual connection path
kg explain "AuthService"             # concept + neighborhood
kg affected "auth.py"                # blast radius, indented by depth
kg ask "what breaks if I change AuthService?"   # multi-agent, shows the trace
```

Open `kg-out/GRAPH_REPORT.md` at the end — god nodes, communities, surprising
connections, and suggested questions are all there in one page.
