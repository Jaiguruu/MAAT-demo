"""The primitive baseline: a ReAct tool-loop over the raw filesystem.

No AST. No knowledge graph. No index. The agent gets three real developer
tools - ``list_files``, ``read_file``, ``grep`` - and an LLM decides what to
do next until it chooses to answer or the step budget forces it to.

This is the honest comparison target for the graph system: same model, same
question, but every fact must be pulled from source *this run*. The graph
system replaces re-reading with precomputed structure; the baseline pays that
cost on every single question.

All LLM calls route through :func:`knowledgegraph.agents.llm.chat` with a
``purpose`` tag, so the metrics ledger sees identical records from both sides.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from knowledgegraph.agents import llm
from knowledgegraph.agents.llm import LLMConfig
from knowledgegraph import metrics as kg_metrics

# --- tool implementations (plain functions over the filesystem) ---------------

_IGNORE_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "kg-out", ".kg", ".idea", ".vscode",
}
_MAX_FILE_BYTES = 200_000
_MAX_LIST = 500
_MAX_GREP_HITS = 80


def tool_list_files(root: Path) -> str:
    """Walk the repo, return relative paths (gitignore-ish dirs skipped)."""
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(root)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{rel} ({size}B)")
            if len(lines) >= _MAX_LIST:
                lines.append("... (truncated)")
                return "\n".join(lines)
    return "\n".join(lines) or "(no files)"


def tool_read_file(root: Path, rel_path: str) -> str:
    """Read one file, clipped. Path is relative to the repo root."""
    p = (root / rel_path).resolve()
    if not str(p).startswith(str(root.resolve())):
        return "error: path escapes repo root"
    if not p.is_file():
        return f"error: no such file: {rel_path}"
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return f"error: {e}"
    if len(text) > _MAX_FILE_BYTES:
        text = text[:_MAX_FILE_BYTES] + "\n... (truncated)"
    return text


def tool_grep(root: Path, pattern: str) -> str:
    """Regex search across text files. Returns path:line: matches."""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"error: bad regex: {e}"
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    rel = p.relative_to(root)
                    hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                    if len(hits) >= _MAX_GREP_HITS:
                        hits.append("... (truncated)")
                        return "\n".join(hits)
    return "\n".join(hits) or "(no matches)"


# --- the ReAct loop --------------------------------------------------------------

_REACT_SYSTEM = """You are investigating a codebase using tools. You have NO
precomputed index - only the raw filesystem.

Tools (reply with ONLY a JSON object):
{"tool": "list_files"}                          - list every file (paths + sizes)
{"tool": "read_file", "path": "src/x.py"}       - read one file's content
{"tool": "grep", "pattern": "regex"}            - regex search over all files
{"tool": "finish", "answer": "..."}             - you have enough; give the final answer

Rules:
- Start with list_files unless you already have the layout.
- Code questions usually need: locate the file (grep/list), read it, read what
  it imports or calls. Do not guess - verify in source.
- Keep answers concise and cite file paths.
- You have a limited step budget. Be deliberate.
"""


@dataclass
class ReActResult:
    answer: str
    steps: list[dict] = field(default_factory=list)
    finish_reason: str = "finish"   # finish | budget | error


def _exec_tool(root: Path, tool: str, action: dict) -> str:
    """Execute one ReAct tool action. Returns the observation text."""
    if tool == "list_files":
        return tool_list_files(root)
    if tool == "read_file":
        return tool_read_file(root, str(action.get("path") or ""))
    if tool == "grep":
        return tool_grep(root, str(action.get("pattern") or ""))
    return f"error: unknown tool {tool!r}"


def iter_react(question: str, root: str | Path, max_steps: int = 12,
               model: str | None = None):
    """Streaming ReAct loop: a generator yielding event dicts as it runs.

    The final yielded event is ``{"type": "result", "result": ReActResult}``.
    Consuming the generator drives the run; ``run_react`` wraps this.
    """
    root = Path(root).resolve()
    observations: list[str] = [f"repo: {root.name}", f"question: {question}"]
    steps: list[dict] = []
    finish_reason = "budget"
    answer = "(no answer produced: step budget exhausted)"

    yield events_emit({"type": "react_started", "question": question,
                       "repo": root.name, "max_steps": max_steps})

    for step_i in range(1, max_steps + 1):
        obs_block = "\n\n".join(f"--- observation {i} ---\n{o}" for i, o in enumerate(observations, 1))
        user = f"{obs_block}\n\nWhat is your next action? JSON only."
        cfg = LLMConfig(model=model) if model else None
        provisional = "answer" if step_i == max_steps else "plan"
        try:
            raw, resp, meta = _chat_with_retry(_REACT_SYSTEM, user, cfg, provisional)
        except Exception as e:
            steps.append({"step": step_i, "action": "llm_error", "error": str(e)})
            yield events_emit({"type": "react_error", "step": step_i,
                               "error": str(e)[:200]})
            yield events_emit({"type": "result",
                               "result": ReActResult(answer=f"(llm error: {e})",
                                                     steps=steps, finish_reason="error")})
            return

        action = _parse_action(raw)
        ledger = kg_metrics.current_ledger()
        if ledger:
            ledger.record_llm(provisional, meta["model"],
                              resp_usage=getattr(resp, "usage", None),
                              wall_ms=meta["wall_ms"])
            if action and action.get("tool") == "finish":
                ledger.record_llm("answer", meta["model"],
                                  resp_usage=getattr(resp, "usage", None),
                                  wall_ms=meta["wall_ms"])
                ledger.mark_zeroed(provisional)

        if action is None:
            steps.append({"step": step_i, "action": "unparseable", "raw": raw[:200]})
            yield events_emit({"type": "react_action", "step": step_i,
                               "tool": "(unparseable)", "arg": raw[:120], "out_chars": 0})
            observations.append("(unparseable action - reply with the JSON shape)")
            continue

        tool = action.get("tool")
        if tool == "finish":
            answer = str(action.get("answer") or "(empty answer)")
            steps.append({"step": step_i, "action": "finish"})
            finish_reason = "finish"
            yield events_emit({"type": "react_finish", "step": step_i})
            break

        out = _exec_tool(root, str(tool), action)
        observations.append(out)
        arg = action.get("path") or action.get("pattern") or ""
        steps.append({"step": step_i, "action": tool, "arg": str(arg)[:120],
                      "out_chars": len(out)})
        yield events_emit({"type": "react_action", "step": step_i, "tool": str(tool),
                           "arg": str(arg)[:120], "out_chars": len(out)})
        yield events_emit({"type": "react_observation", "step": step_i,
                           "preview": out[:400]})

    yield events_emit({"type": "result",
                       "result": ReActResult(answer=answer, steps=steps,
                                             finish_reason=finish_reason)})


def events_emit(event: dict) -> dict:
    """Tag an event with seq/ts (delegates to the shared event module)."""
    from knowledgegraph.agents import events as _ev
    return _ev.emit(event)


def run_react(question: str, root: str | Path, max_steps: int = 12,
              model: str | None = None,
              sink=None) -> ReActResult:
    """Run the ReAct tool loop for one question. Returns answer + step log.

    When ``sink`` is given, every step is published to it as it happens
    (the web UI's live view); when ``model`` is given it overrides the
    configured agent model for this run.
    """
    result_holder: list[ReActResult] = []

    for e in iter_react(question, root, max_steps, model):
        if e.get("type") == "result":
            result_holder.append(e["result"])
        if sink:
            sink(e)
    return result_holder[-1] if result_holder else ReActResult(
        answer="(internal error: no result)", steps=[], finish_reason="error")


def _chat_with_retry(system: str, user: str, cfg, purpose: str,
                     retries: int = 3, base_delay: float = 4.0):
    """chat_full with exponential backoff on rate-limit errors."""
    import time as _time
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return llm.chat_full(system, user, cfg, purpose=purpose)
        except Exception as e:
            is_rate_limit = "429" in str(e) or "RateLimit" in type(e).__name__
            last_exc = e
            if not is_rate_limit or attempt == retries:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"  react: rate limited, retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{retries})", flush=True)
            _time.sleep(delay)
    raise last_exc  # unreachable, keeps type-checkers happy


def _parse_action(raw: str) -> dict | None:
    """Tolerant JSON extraction for one ReAct action."""
    import json
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
