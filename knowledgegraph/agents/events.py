"""Live pipeline introspection: a per-run event sink for agent decisions.

The web UI needs to show *how the LLM decided* — routing choices, tool calls,
tool results, synthesis mode — as the run happens. This module provides the
minimum plumbing: a per-run event sink that agent nodes emit into, plus an
SSE-ready async bridge for the server layer.

Design rules:
- Emission is fire-and-forget: a missing sink must never break a run.
- Events are plain dicts (JSON-safe) tagged with monotonically increasing
  sequence numbers so the frontend can order them deterministically.
- Two sink flavors: ``EventSink`` collects into a list (for tests and for
  replaying a finished run) and ``SSEBridge`` fans out to asyncio queues
  (for the live web UI).
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone

_seq_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(event: dict) -> dict:
    """Fill in seq/ts on an event dict (mutates and returns it)."""
    event.setdefault("seq", _next_seq())
    event.setdefault("ts", _ts())
    return event


class EventSink:
    """Collect events into a list. Thread-safe; used for tests and replay."""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def add(self, event: dict) -> dict:
        e = emit(dict(event))
        with self._lock:
            self._events.append(e)
        return e

    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with _lock_holder:
            self._events = []


_lock_holder = threading.Lock()


class SSEBridge:
    """Fan-out bridge: agent worker threads publish, asyncio SSE handlers consume.

    One bridge per ask-run. Subscribers first receive a replay of the full
    transcript (so late consumers miss nothing), then live events.

    Thread-safety: publishers run in worker threads, consumers on the event
    loop. Delivery goes through ``loop.call_soon_threadsafe`` - putting on an
    ``asyncio.Queue`` directly from another thread never wakes the awaiting
    consumer (the classic hang-then-drain bug).
    """

    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self._events: list[dict] = []  # full transcript for post-run replay

    def publish(self, event: dict) -> None:
        e = emit(dict(event))
        with self._lock:
            self._events.append(e)
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, e)
            except RuntimeError:
                pass  # loop closed mid-run - subscriber is gone

    @staticmethod
    def _put(q: asyncio.Queue, e: dict) -> None:
        try:
            q.put_nowait(e)
        except Exception:
            pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # off-loop subscriber (tests): transcript replay only
        with self._lock:
            for e in self._events:
                q.put_nowait(e)
            if loop is not None:
                self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, qq) for (l, qq) in self._subs if qq is not q]

    def transcript(self) -> list[dict]:
        with self._lock:
            return list(self._events)


# --- registry: the active bridge for the running ask --------------------------

_current_bridge: SSEBridge | None = None
_bridge_lock = threading.Lock()


def set_bridge(bridge: SSEBridge | None) -> None:
    global _current_bridge
    with _bridge_lock:
        _current_bridge = bridge


def bridge() -> SSEBridge | None:
    with _bridge_lock:
        return _current_bridge


# --- event constructors (the vocabulary the frontend renders) ------------------


def ev_run_started(question: str, mode: str, system: str, repo: str | None = None) -> dict:
    e = {"type": "run_started", "question": question, "mode": mode, "system": system}
    if repo:
        e["repo"] = repo
    return e


def ev_plan(question: str, route: list[str], source: str, raw: str | None = None) -> dict:
    e = {"type": "plan", "route": route, "source": source}  # source: llm | keyword
    if raw:
        e["raw"] = raw[:400]
    return e


def ev_tool_call(agent: str, tool: str, args: dict | None = None) -> dict:
    return {"type": "tool_call", "agent": agent, "tool": tool, "args": args or {}}


def ev_tool_result(agent: str, tool: str, ok: bool, summary: str, data: dict | None = None) -> dict:
    slim = {k: v for k, v in (data or {}).items() if k in ("matched", "nodes", "edges", "hops", "affected", "neighbors", "communities")}
    return {"type": "tool_result", "agent": agent, "tool": tool, "ok": ok,
            "summary": summary[:300], "data": _slim_data(slim)}


def ev_action(agent: str, step: int, tool: str, arg: str, out_chars: int) -> dict:
    """ReAct baseline: one action step."""
    return {"type": "react_action", "agent": agent, "step": step, "tool": tool,
            "arg": arg[:120], "out_chars": out_chars}


def ev_observation(agent: str, step: int, preview: str) -> dict:
    return {"type": "react_observation", "agent": agent, "step": step,
            "preview": preview[:400]}


def ev_synthesis(mode: str, answer_chars: int, error: str | None = None) -> dict:
    e = {"type": "synthesis", "mode": mode, "answer_chars": answer_chars}
    if error:
        e["error"] = error[:200]
    return e


def ev_done(answer: str, error: str | None = None) -> dict:
    e = {"type": "done", "answer_chars": len(answer), "answer": answer}
    if error:
        e["error"] = error[:300]
    return e


def ev_metrics(summary: dict) -> dict:
    """End-of-run metrics snapshot (from UsageLedger.summarize)."""
    return {"type": "metrics", "summary": summary}


def _slim_data(d: dict, max_items: int = 12) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = v[:max_items]
        elif isinstance(v, dict):
            out[k] = _slim_data(v, max_items)
        else:
            out[k] = v
    return out


def dumps(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, default=str)
