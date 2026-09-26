"""Offline tests for the baseline package, metrics ledger, and bench harness."""
from __future__ import annotations

import json

import pytest

from knowledgegraph import metrics
from knowledgegraph.baseline import react


@pytest.fixture(autouse=True)
def _no_llm_network(monkeypatch):
    """Block any real LLM call in every test in this module.

    If code under test reaches the real client instead of a fake, the test
    fails loudly rather than spending tokens.
    """
    def _forbidden(*args, **kwargs):  # pragma: no cover - safety net
        raise AssertionError("real LLM call attempted in offline test")

    monkeypatch.setattr("knowledgegraph.agents.llm.chat_full", _forbidden)
    monkeypatch.setattr("knowledgegraph.agents.llm.chat", _forbidden)


# --- baseline tools ----------------------------------------------------------

def test_tool_list_files(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.md").write_text("hi\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()  # must be skipped
    out = react.tool_list_files(tmp_path)
    assert "a.py" in out and f"sub{chr(92)}b.md" in out.replace("/", chr(92)) or "sub/b.md" in out
    assert "__pycache__" not in out


def test_tool_read_file(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    out = react.tool_read_file(tmp_path, "a.py")
    assert "def f():" in out
    assert "error" in react.tool_read_file(tmp_path, "missing.py")
    # path traversal is blocked
    assert "error" in react.tool_read_file(tmp_path, "../outside.py")


def test_tool_grep(tmp_path):
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = hello\n", encoding="utf-8")
    out = react.tool_grep(tmp_path, r"\bhello\b")
    assert "a.py:1" in out and "b.py:1" in out
    assert "(no matches)" in react.tool_grep(tmp_path, "zzz_nothing")
    assert "error" in react.tool_grep(tmp_path, "(")  # bad regex


# --- metrics ledger ------------------------------------------------------------

def test_ledger_records_and_aggregates():
    ledger = metrics.UsageLedger()

    class FakeUsage:
        prompt_tokens = 100
        completion_tokens = 20

    ledger.record_llm("plan", "gpt-4.1-mini", resp_usage=FakeUsage(), wall_ms=50.0)
    ledger.record_llm("answer", "gpt-4.1-mini", resp_usage=FakeUsage(), wall_ms=30.0)
    ledger.record_llm("answer", "gpt-4.1-mini", resp_usage=None, wall_ms=5.0, error="boom")
    ledger.record_phase("ingest", "kg_build", 1200.0)

    s = ledger.summarize("test")
    assert s["llm_calls"] == 3 and s["failed_calls"] == 1
    assert s["prompt_tokens"] == 200 and s["completion_tokens"] == 40
    assert s["total_tokens"] == 240
    assert s["by_purpose"]["plan"]["calls"] == 1
    assert s["by_purpose"]["answer"]["calls"] == 2
    assert s["phase_wall_ms"] == 1200.0
    # cost: priced model → nonzero; tokens counted; failed call bills nothing
    assert s["cost_usd"] > 0


def test_mark_zeroed_suppresses_provisional_row():
    ledger = metrics.UsageLedger()

    class FakeUsage:
        prompt_tokens = 500
        completion_tokens = 50

    ledger.record_llm("plan", "gpt-4.1-mini", resp_usage=FakeUsage(), wall_ms=10.0)
    ledger.mark_zeroed("plan")
    s = ledger.summarize("t")
    assert s["total_tokens"] == 0 and s["cost_usd"] == 0.0
    assert s["llm_calls"] == 1  # call still counted


def test_react_retags_finish_call_to_answer(tmp_path, monkeypatch):
    """The finish call's tokens must land under purpose=answer, not plan."""
    from knowledgegraph import metrics as kg_metrics

    ledger = kg_metrics.UsageLedger()
    kg_metrics.set_ledger(ledger)
    try:
        calls: list[str] = []
        _patch_chat(monkeypatch, [
            json.dumps({"tool": "grep", "pattern": "x"}),
            json.dumps({"tool": "finish", "answer": "found"}),
        ], calls)
        result = react.run_react("q", tmp_path, max_steps=5)
        assert result.finish_reason == "finish"
        s = ledger.summarize("t")
        # step 1: plan (12 tokens). step 2: provisional plan row zeroed,
        # re-recorded as answer (12 tokens)
        assert s["by_purpose"]["plan"]["prompt_tokens"] == 10
        assert s["by_purpose"]["plan"]["completion_tokens"] == 2
        assert s["by_purpose"]["answer"]["prompt_tokens"] == 10
        assert s["by_purpose"]["answer"]["completion_tokens"] == 2
        assert s["total_tokens"] == 24  # 2 calls x 12, no double-billing
    finally:
        kg_metrics.set_ledger(None)


def test_ledger_unknown_model_costs_zero():
    ledger = metrics.UsageLedger()
    ledger.record_llm("answer", "mystery-model-v9", resp_usage=None, wall_ms=1.0)
    s = ledger.summarize("t")
    assert s["cost_usd"] == 0.0
    assert s["total_tokens"] == 0  # failed/no-usage calls count but bill nothing


def test_price_overrides_env(monkeypatch):
    monkeypatch.setenv("KG_PRICE_OVERRIDES",
                       json.dumps({"mystery-model-v9": [1.0, 2.0]}))
    ledger = metrics.UsageLedger()

    class FakeUsage:
        prompt_tokens = 1_000_000
        completion_tokens = 1_000_000

    ledger.record_llm("answer", "mystery-model-v9", resp_usage=FakeUsage(), wall_ms=1.0)
    s = ledger.summarize("t")
    assert s["cost_usd"] == 3.0  # 1.0 + 2.0


def test_price_overrides_invalid_json_ignored(monkeypatch):
    monkeypatch.setenv("KG_PRICE_OVERRIDES", "not json{")
    ledger = metrics.UsageLedger()
    ledger.record_llm("answer", "gpt-4.1-mini", resp_usage=None, wall_ms=1.0)
    assert ledger.summarize("t")["cost_usd"] == 0.0  # no tokens anyway


def test_ledger_persists_json(tmp_path):
    ledger = metrics.UsageLedger()
    ledger.record_phase("ingest", "scan", 10.0)
    summary = ledger.save(tmp_path / "side.json", "graph")
    payload = json.loads((tmp_path / "side.json").read_text(encoding="utf-8"))
    assert payload["summary"]["label"] == "graph"
    assert len(payload["records"]) == 1
    assert summary["label"] == "graph"


def test_rejects_unknown_purpose():
    ledger = metrics.UsageLedger()
    with pytest.raises(AssertionError):
        ledger.record_llm("vibes", "m", wall_ms=1.0)


# --- ReAct loop (fake LLM) -------------------------------------------------------

def _patch_chat(monkeypatch, script: list[str], calls: list):
    """Patch agents.llm.chat_full to pop scripted replies, recording purposes."""
    idx = {"i": 0}

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 2

    class FakeResp:
        usage = FakeUsage()

    def fake_chat_full(system, user, config_=None, purpose="answer"):
        calls.append(purpose)
        reply = script[min(idx["i"], len(script) - 1)]
        idx["i"] += 1
        return (reply, FakeResp(), {"model": "fake", "wall_ms": 1.0})

    monkeypatch.setattr(react.llm, "chat_full", fake_chat_full)


def test_react_finishes_when_llm_finishes(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("y = 2\n", encoding="utf-8")
    calls: list[str] = []
    _patch_chat(monkeypatch, [
        json.dumps({"tool": "finish", "answer": "it is a.py with y=2"}),
    ], calls)

    result = react.run_react("what is in a.py", tmp_path, max_steps=5)
    assert result.finish_reason == "finish"
    assert "a.py" in result.answer
    assert calls == ["plan"]  # provisional tag; ledger correction tested separately


def test_react_budget_forces_stop(tmp_path, monkeypatch):
    calls: list[str] = []
    # always demand list_files - never finishes
    _patch_chat(monkeypatch, [json.dumps({"tool": "list_files"})], calls)

    result = react.run_react("tell me everything", tmp_path, max_steps=3)
    assert result.finish_reason == "budget"
    assert len(result.steps) == 3
    assert calls == ["plan", "plan", "answer"]  # final forced call is the answer


def test_react_uses_read_file(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("TOKEN = 'abc'\n", encoding="utf-8")
    calls: list[str] = []
    _patch_chat(monkeypatch, [
        json.dumps({"tool": "read_file", "path": "a.py"}),
        json.dumps({"tool": "finish", "answer": "TOKEN=abc in a.py"}),
    ], calls)

    result = react.run_react("where is TOKEN", tmp_path, max_steps=5)
    assert result.finish_reason == "finish"
    assert result.steps[0]["action"] == "read_file"
    assert result.steps[0]["out_chars"] > 0


def test_react_survives_unparseable_and_unknown(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_chat(monkeypatch, [
        "I will not produce JSON",                     # unparseable
        json.dumps({"tool": "rm_rf", "path": "/"}),    # unknown tool
        json.dumps({"tool": "finish", "answer": "done"}),
    ], calls)
    result = react.run_react("q", tmp_path, max_steps=6)
    assert result.finish_reason == "finish"
    actions = [s["action"] for s in result.steps]
    assert "unparseable" in actions and "rm_rf" in actions


def test_react_llm_error_is_recorded(tmp_path, monkeypatch):
    def boom(system, user, config_=None, purpose="answer"):
        raise RuntimeError("api down")
    monkeypatch.setattr(react.llm, "chat_full", boom)
    result = react.run_react("q", tmp_path, max_steps=3)
    assert result.finish_reason == "error"
    assert "api down" in result.answer


# --- bench harness (fake everything) ---------------------------------------------

def test_run_bench_offline(tmp_path, monkeypatch):
    """Full bench with no API key: graph side falls back to extractive,
    baseline side errors per-call - both must still produce a report."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from knowledgegraph.bench import run_bench
    report = run_bench(repo, ["what does f do"], skip_build=True,
                       graph_path="kg-out/graph.json", out_dir=tmp_path / "kg-out" / "bench")

    # report structure exists even when both sides are degraded
    assert report["graph"]["llm_calls"] == 0
    assert report["baseline"]["label"] == "baseline"
    runs = json.loads((tmp_path / "kg-out" / "bench" / "runs.json").read_text(encoding="utf-8"))
    assert len(runs) == 1 and runs[0]["questions"] == ["what does f do"]
    assert (tmp_path / "kg-out" / "bench" / "COMPARISON.md").exists()


def test_render_comparison_arithmetic():
    from knowledgegraph.bench import render_comparison
    report = {
        "ts": "t", "repo": "r", "questions": ["q1"], "max_steps": 5,
        "graph": {"label": "graph", "wall_ms": 1000.0, "llm_calls": 2, "failed_calls": 0,
                  "prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600,
                  "cost_usd": 0.001, "phase_wall_ms": 0.0,
                  "by_purpose": {p: {"calls": 1, "prompt_tokens": 100,
                                     "completion_tokens": 20, "wall_ms": 1.0}
                                 for p in metrics.PURPOSES}},
        "baseline": {"label": "baseline", "wall_ms": 9000.0, "llm_calls": 6, "failed_calls": 1,
                     "prompt_tokens": 4000, "completion_tokens": 300, "total_tokens": 4300,
                     "cost_usd": 0.01, "phase_wall_ms": 0.0,
                     "by_purpose": {p: {"calls": 2, "prompt_tokens": 900,
                                        "completion_tokens": 70, "wall_ms": 1.0}
                                    for p in metrics.PURPOSES}},
        "per_question": [{"question": "q1", "graph_answer_chars": 10,
                          "baseline_answer_chars": 20, "baseline_steps": []}],
    }
    md = render_comparison(report)
    assert "| Total tokens | 600 | 4300 |" in md
    assert "Break-even" in md
