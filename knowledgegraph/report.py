"""Stage 6 - render GRAPH_REPORT.md, the human-readable audit trail."""
from __future__ import annotations

from datetime import date

import networkx as nx


def generate(
    G: nx.Graph,
    communities: dict[int, list[str]],
    cohesion_scores: dict[int, float],
    community_labels: dict[int, str],
    god_node_list: list[dict],
    surprise_list: list[dict],
    detection_result: dict,
    root: str,
    suggested_questions: list[str] | None = None,
    gap_list: list[dict] | None = None,
) -> str:
    today = date.today().isoformat()

    confidences = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
    total = len(confidences) or 1
    ext_pct = round(confidences.count("EXTRACTED") / total * 100)
    inf_pct = round(confidences.count("INFERRED") / total * 100)
    amb_pct = round(confidences.count("AMBIGUOUS") / total * 100)

    inf_scores = [
        d.get("confidence_score", 0.5)
        for _, _, d in G.edges(data=True)
        if d.get("confidence") == "INFERRED"
    ]
    inf_avg = round(sum(inf_scores) / len(inf_scores), 2) if inf_scores else None

    lines: list[str] = [
        f"# Graph Report - {root}  ({today})",
        "",
        "## Corpus Check",
    ]
    if detection_result.get("warning"):
        lines.append(f"- {detection_result['warning']}")
    else:
        lines += [
            f"- {detection_result['total_files']} files, ~{detection_result['total_words']:,} words",
            "- Verdict: corpus is large enough that graph structure adds value.",
        ]
    if detection_result.get("skipped_sensitive"):
        n = len(detection_result["skipped_sensitive"])
        lines.append(f"- Skipped {n} likely-secret file(s) (never indexed).")

    lines += [
        "",
        "## Headline Numbers",
        f"- **{G.number_of_nodes()}** nodes, **{G.number_of_edges()}** edges",
        f"- **{len(communities)}** communities (hub-labeled)",
        f"- Confidence mix: {ext_pct}% EXTRACTED / {inf_pct}% INFERRED / {amb_pct}% AMBIGUOUS",
    ]
    if inf_avg is not None:
        lines.append(f"- Average inferred-edge score: {inf_avg}")

    lines += ["", "## God Nodes", ""]
    if god_node_list:
        lines.append("| Concept | Connections |")
        lines.append("|---|---|")
        for g in god_node_list[:10]:
            lines.append(f"| `{g['label']}` | {g['degree']} |")
    else:
        lines.append("- No connected abstractions found.")

    lines += ["", "## Communities", ""]
    for cid in sorted(communities):
        members = communities[cid]
        coh = cohesion_scores.get(cid)
        coh_str = f" (cohesion {coh:.2f})" if coh is not None else ""
        lines.append(f"- **{community_labels.get(cid, f'Community {cid}')}** - {len(members)} nodes{coh_str}")

    lines += ["", "## Surprising Connections", ""]
    if surprise_list:
        for s in surprise_list:
            src = s.get("source_label", s.get("source", "?"))
            tgt = s.get("target_label", s.get("target", "?"))
            rel = s.get("relation", "?")
            conf = s.get("confidence", "EXTRACTED")
            detail = ""
            if s.get("from_file"):
                detail = f"  [{s['from_file']} -> {s['to_file']}]"
            lines.append(f"- `{src}` --{rel}--> `{tgt}` **[{conf}]**{detail}")
    else:
        lines.append("- No unexpected cross-links; the graph matches the file layout.")

    if gap_list:
        lines += ["", "## Knowledge Gaps", ""]
        for gap in gap_list:
            src = gap.get("source_file", "")
            lines.append(f"- `{gap['label']}` - nothing references it{(' (' + src + ')') if src else ''}")

    if suggested_questions:
        lines += ["", "## Suggested Questions", ""]
        for q in suggested_questions:
            lines.append(f"- {q}")

    lines += [
        "",
        "## How to read this",
        "- **EXTRACTED** edges were read directly from the source (imports, calls, headings).",
        "- **INFERRED** edges were resolved by the pipeline with a confidence score.",
        "- **AMBIGUOUS** edges are flagged for human review.",
        "",
        "Query the graph without re-reading files:",
        "```",
        'kg query "what connects X to Y?"',
        'kg path "A" "B"',
        'kg explain "A"',
        'kg ask "plain-language question"   # multi-agent, needs OPENAI_API_KEY',
        "```",
        "",
    ]
    return "\n".join(lines)
