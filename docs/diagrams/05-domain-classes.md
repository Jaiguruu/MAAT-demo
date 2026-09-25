# 05 — Class: Domain Model

Core data shapes across the pipeline and agent layers.

```mermaid
classDiagram
    direction LR

    class Fragment {
        <<schema>>
        +List~Node~ nodes
        +List~Edge~ edges
        +Dict raw_calls
        +Dict definitions
        +str error
        +validate() List~str~
    }

    class Node {
        <<schema>>
        +str id
        +str label
        +str file_type
        +str source_file
        +str source_location
        +str summary
        +bool stub
    }

    class Edge {
        <<schema>>
        +str source
        +str target
        +str relation
        +str confidence
        +float confidence_score
        +float weight
    }

    class KnowledgeGraph {
        <<networkx Graph>>
        +Graph~Node, Edge~ G
        +Dict community_labels
        +Dict cohesion
        +List hyperedges
        +save_graph(path)
        +load_graph(path)
    }

    class Extractor {
        <<interface>>
        +extract(path) Fragment
    }

    class CodeExtractor {
        -_FileScope scope
        +extract_code(path) Fragment
    }

    class MarkdownExtractor {
        +extract_markdown(path) Fragment
    }

    class SemanticExtractor {
        +extract_semantic_fragment(path) Fragment
        +extract_semantic_batch(paths) Dict
        +apply_semantic_fragments(G) Dict
    }

    class CallResolver {
        +resolve_raw_calls(G, raw) List
        +build_label_index(G) Dict
    }

    class ToolResult {
        +str name
        +bool ok
        +str text
        +Dict data
    }

    class AgentState {
        <<TypedDict>>
        +str question
        +str graph_path
        +List~str~ plan
        +List~Dict~ evidence
        +List~str~ trace
        +bool needs_more
        +str answer
    }

    class LLMWrapper {
        +chat(system, user) str
        +llm_available() bool
    }

    Fragment *-- Node
    Fragment *-- Edge
    KnowledgeGraph ..> Fragment : assembled from
    Extractor <|.. CodeExtractor
    Extractor <|.. MarkdownExtractor
    Extractor <|.. SemanticExtractor
    SemanticExtractor --> CallResolver : code_links via
    KnowledgeGraph --> ToolResult : queried into
    AgentState *-- ToolResult : evidence list
    LLMWrapper ..> AgentState : plans/synthesizes
```

## Schema invariants (enforced by `validate.py`)

- `file_type ∈ {code, document, paper, image, rationale, concept}`
- `confidence ∈ {EXTRACTED, INFERRED, AMBIGUOUS}`
- Every edge endpoint must resolve to a node id **within the same fragment** — which is why cross-fragment producers either emit stubs (`CodeExtractor`) or defer resolution (`SemanticExtractor.code_links`).
- IDs: one normalization recipe in `ids.py`; two producers of the same entity must converge on the same id or the graph splits into ghosts.
