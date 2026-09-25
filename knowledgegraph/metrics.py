"""Observability ledger for LLM work across both systems.

One record per LLM call (or per wall-clock phase), keyed by a shared
``purpose`` vocabulary so the graph side and the ReAct baseline aggregate
identically:

    ingest  - building the corpus view (kg build / file scan)
    plan    - deciding what to do next (supervisor / ReAct step)
    read    - pulling source content (ReAct read/grep; 0 on the graph side)
    answer  - the final synthesis call (both systems)

Records are plain dicts so they serialize without ceremony. The module is
thread-safe (the semantic pass and ReAct loop both run calls concurrently)
and deliberately dumb: no DB, just JSON in ``kg-out/bench/``.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PURPOSES = ("ingest", "plan", "read", "answer")

# default $/1M tokens; overridable per model via COST_TABLE
DEFAULT_COST_TABLE: dict[str, tuple[float, float]] = {
    # model_prefix: (input $/1M, output $/1M)
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "google/gemini-2.0-flash-001": (0.10, 0.40),
}

_lock = threading.Lock()


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                  table: dict | None = None) -> float:
    """Best-effort $ cost. Unknown models price at zero rather than guessing."""
    tbl = table or DEFAULT_COST_TABLE
    for prefix, (in_p, out_p) in tbl.items():
        if model.startswith(prefix):
            return round(prompt_tokens / 1e6 * in_p + completion_tokens / 1e6 * out_p, 6)
    return 0.0


class UsageLedger:
    """Thread-safe collector of metric records for one bench run."""

    def __init__(self) -> None:
        self._records: list[dict] = []
        self._t0 = time.perf_counter()

    # -- recording ---------------------------------------------------------

    def record_llm(self, purpose: str, model: str, resp_usage=None,
                  wall_ms: float = 0.0, error: str | None = None,
                  extra: dict | None = None) -> dict:
        """Record one LLM call. ``resp_usage`` is the OpenAI usage object;
        missing/None counts as zero tokens (failed calls still count)."""
        assert purpose in PURPOSES, f"unknown purpose {purpose!r}"
        prompt = getattr(resp_usage, "prompt_tokens", 0) or 0
        completion = getattr(resp_usage, "completion_tokens", 0) or 0
        rec: dict = {
            "kind": "llm",
            "purpose": purpose,
            "model": model,
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "cost_usd": estimate_cost(model, int(prompt), int(completion)),
            "wall_ms": round(wall_ms, 1),
            "error": error,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            rec.update(extra)
        with _lock:
            self._records.append(rec)
        return rec

    def record_phase(self, purpose: str, name: str, wall_ms: float,
                     extra: dict | None = None) -> dict:
        """Record a non-LLM phase (e.g. graph build wall time, file scan)."""
        assert purpose in PURPOSES, f"unknown purpose {purpose!r}"
        rec: dict = {
            "kind": "phase",
            "purpose": purpose,
            "name": name,
            "wall_ms": round(wall_ms, 1),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            rec.update(extra)
        with _lock:
            self._records.append(rec)
        return rec

    # -- aggregation ---------------------------------------------------------

    def records(self) -> list[dict]:
        with _lock:
            return list(self._records)

    def summarize(self, label: str) -> dict:
        """Aggregate all records into the comparison-ready summary block."""
        recs = self.records()
        out: dict = {"label": label, "wall_ms": round((time.perf_counter() - self._t0) * 1000, 1),
                     "llm_calls": 0, "failed_calls": 0,
                     "prompt_tokens": 0, "completion_tokens": 0,
                     "cost_usd": 0.0,
                     "by_purpose": {p: {"calls": 0, "prompt_tokens": 0,
                                        "completion_tokens": 0, "wall_ms": 0.0}
                                    for p in PURPOSES}}
        for r in recs:
            if r["kind"] != "llm":
                continue
            out["llm_calls"] += 1
            if r.get("error"):
                out["failed_calls"] += 1
            out["prompt_tokens"] += r["prompt_tokens"]
            out["completion_tokens"] += r["completion_tokens"]
            out["cost_usd"] += r["cost_usd"]
            bp = out["by_purpose"][r["purpose"]]
            bp["calls"] += 1
            bp["prompt_tokens"] += r["prompt_tokens"]
            bp["completion_tokens"] += r["completion_tokens"]
            bp["wall_ms"] += r["wall_ms"]
        for p in PURPOSES:
            out["by_purpose"][p]["wall_ms"] = round(out["by_purpose"][p]["wall_ms"], 1)
        out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
        out["cost_usd"] = round(out["cost_usd"], 6)
        out["phase_wall_ms"] = round(
            sum(r["wall_ms"] for r in recs if r["kind"] == "phase"), 1)
        return out

    def save(self, path: Path, label: str) -> dict:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summarize(label)
        payload = {"summary": summary, "records": self.records()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return summary


# --- global hook points ---------------------------------------------------

_global_ledger: UsageLedger | None = None


def set_ledger(ledger: UsageLedger | None) -> None:
    """Install the ledger that llm/semantic instrumentation records into."""
    global _global_ledger
    _global_ledger = ledger


def current_ledger() -> UsageLedger | None:
    return _global_ledger
