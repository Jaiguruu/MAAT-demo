"""``kg-ui`` - local web UI for the knowledge graph system.

A FastAPI app + single-page frontend (no build step) providing:

- **Settings**: provider base URL, API key, model selection (semantic/agent),
  stored in-process (never written to disk), with a live "test connection".
- **Graph view**: node-link JSON for D3 + per-node detail, build trigger with
  live progress events.
- **Ask**: one question through either system (multi-agent graph or ReAct
  baseline) with the *live decision stream* — plan, tool calls, results,
  synthesis mode — over Server-Sent Events.

Run:  kg-ui  (defaults to 127.0.0.1:8765, --reload for development)
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from knowledgegraph.agents import events
from knowledgegraph import config as kg_config
from knowledgegraph import metrics as kg_metrics
from knowledgegraph.build import load_graph
from knowledgegraph.paths import default_graph_json

app = FastAPI(title="kg-ui", docs_url=None, redoc_url=None)

_STATIC = Path(__file__).parent / "static"

# in-process runtime settings (never persisted to disk by the server)
_runtime: dict = {
    "base_url": None,
    "api_key": None,
    "model": None,
    "semantic_model": None,
    "agent_model": None,
    "max_steps": 12,
    # explicit baseline override; None = follow the graph's source repo
    "baseline_repo": None,
}
_runtime_lock = threading.Lock()


def graph_source_root() -> str | None:
    """The repo the current graph.json was built from (stamped by the pipeline)."""
    gp = default_graph_json()
    if not gp.exists():
        return None
    try:
        meta = json.loads(gp.read_text(encoding="utf-8")).get("graph", {})
        return meta.get("source_root")
    except (json.JSONDecodeError, OSError):
        return None


def baseline_root() -> Path:
    """Which folder the ReAct baseline explores.

    Precedence: explicit UI setting (KG_UI_REPO / Settings tab) > the graph's
    stamped source repo > cwd. Keeping the graph's repo as the middle layer
    means ask-mode consistency is the default, not something to remember.
    """
    if _runtime.get("baseline_repo"):
        return Path(_runtime["baseline_repo"])
    env = os.environ.get("KG_UI_REPO")
    if env:
        return Path(env)
    stamped = graph_source_root()
    if stamped:
        return Path(stamped)
    return Path.cwd()


def _apply_runtime() -> None:
    """Push runtime settings into the environment the config layer reads."""
    with _runtime_lock:
        rt = dict(_runtime)
    if rt.get("base_url"):
        os.environ["OPENAI_BASE_URL"] = rt["base_url"]
    else:
        os.environ.pop("OPENAI_BASE_URL", None)
    if rt.get("api_key"):
        os.environ["OPENAI_API_KEY"] = rt["api_key"]
        os.environ.pop("OPENROUTER_API_KEY", None)
    if rt.get("model"):
        os.environ["OPENAI_MODEL"] = rt["model"]
    if rt.get("semantic_model"):
        os.environ["KG_SEMANTIC_MODEL"] = rt["semantic_model"]
    if rt.get("agent_model"):
        os.environ["KG_AGENT_MODEL"] = rt["agent_model"]


# --- models --------------------------------------------------------------------


class SettingsIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    semantic_model: str | None = None
    agent_model: str | None = None
    baseline_repo: str | None = None


class TestIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class BuildIn(BaseModel):
    root: str
    semantic: bool = True
    resolution: float = 1.0


class AskIn(BaseModel):
    question: str
    system: str = "graph"        # graph | baseline
    max_steps: int = 12
    model: str | None = None


# --- settings endpoints -----------------------------------------------------------


@app.get("/api/settings")
def get_settings():
    with _runtime_lock:
        rt = dict(_runtime)
    return {
        **{k: v for k, v in rt.items() if k != "api_key"},
        "api_key_set": bool(rt.get("api_key")) or kg_config.api_key() is not None,
        "effective": {
            "base_url": kg_config.base_url(),
            "model": kg_config.agent_model(),
            "semantic_model": kg_config.semantic_model(),
            "llm_ready": kg_config.llm_ready(),
        },
        "repos": {
            "graph_source": graph_source_root(),
            "baseline_root": str(baseline_root()),
            "baseline_override": rt.get("baseline_repo"),
        },
    }


@app.post("/api/settings")
def post_settings(s: SettingsIn):
    with _runtime_lock:
        if s.base_url is not None:
            _runtime["base_url"] = s.base_url or None
        if s.api_key is not None:
            _runtime["api_key"] = s.api_key or None
        if s.model is not None:
            _runtime["model"] = s.model or None
        if s.semantic_model is not None:
            _runtime["semantic_model"] = s.semantic_model or None
        if s.agent_model is not None:
            _runtime["agent_model"] = s.agent_model or None
        if s.baseline_repo is not None:
            _runtime["baseline_repo"] = s.baseline_repo or None
    _apply_runtime()
    return get_settings()


@app.post("/api/settings/test")
def test_connection(t: TestIn):
    """One minimal completion against the given provider settings."""
    base_url = t.base_url if t.base_url is not None else kg_config.base_url()
    api_key = t.api_key if t.api_key is not None else kg_config.api_key()
    model = t.model or kg_config.agent_model()
    if not api_key:
        raise HTTPException(400, "no API key configured")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=model, max_tokens=5,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}])
        ms = round((time.perf_counter() - t0) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return {"ok": True, "model": model, "latency_ms": ms,
                "reply": content[:40],
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.get("/api/models")
def list_models():
    """Curated suggestions + the models the current provider actually serves."""
    curated = [
        {"id": "google/gemini-2.0-flash-001", "note": "cheap, fast (OpenRouter)"},
        {"id": "openai/gpt-4.1-mini", "note": "cheap OpenAI via OpenRouter"},
        {"id": "gpt-4.1-mini", "note": "OpenAI direct"},
        {"id": "llama-3.1-8b-instant", "note": "Groq, very fast"},
        {"id": "deepseek-chat", "note": "DeepSeek"},
        {"id": "qwen2.5-coder:7b", "note": "local Ollama"},
    ]
    served: list[str] = []
    if kg_config.llm_ready():
        try:
            from openai import OpenAI
            client = OpenAI(api_key=kg_config.api_key(), base_url=kg_config.base_url())
            served = sorted(m.id for m in client.models.list().data)[:200]
        except Exception:
            served = []
    return {"curated": curated, "provider_models": served}


# --- graph endpoints ----------------------------------------------------------------


@app.get("/api/graph")
def get_graph():
    gp = default_graph_json()
    if not gp.exists():
        raise HTTPException(404, "no graph built yet - run a build first")
    data = json.loads(gp.read_text(encoding="utf-8"))
    # node-link -> d3-friendly {nodes, links} with slim attrs
    nodes = [{k: n.get(k) for k in ("id", "label", "file_type", "community",
                                    "summary") if n.get(k) is not None}
             for n in data.get("nodes", [])]
    links = [{k: e.get(k) for k in ("source", "target", "relation", "confidence",
                                    "confidence_score") if e.get(k) is not None}
             for e in data.get("edges", data.get("links", []))]
    return {"nodes": nodes, "links": links,
            "meta": {"directed": data.get("directed", False),
                     "graph": data.get("graph", {})}}


@app.get("/api/graph/node/{node_id}")
def get_node(node_id: str):
    gp = default_graph_json()
    if not gp.exists():
        raise HTTPException(404, "no graph built")
    G = load_graph(gp)
    if not G.has_node(node_id):
        raise HTTPException(404, f"no node {node_id}")
    d = dict(G.nodes[node_id])
    neighbors = []
    for nbr in G.neighbors(node_id):
        nd = G.nodes[nbr]
        edge = G.get_edge_data(node_id, nbr) or {}
        neighbors.append({"id": nbr, "label": nd.get("label", nbr),
                          "file_type": nd.get("file_type"),
                          "relation": edge.get("relation"),
                          "confidence": edge.get("confidence")})
    return {"node": d, "neighbors": neighbors}


@app.post("/api/graph/build")
async def build_graph_endpoint(b: BuildIn):
    """Run the pipeline in a worker thread; progress goes to SSE /api/build/stream."""
    loop = asyncio.get_event_loop()
    bridge = events.SSEBridge()
    from knowledgegraph.pipeline import run_pipeline

    def _job():
        events.set_bridge(bridge)
        try:
            # record build wall time; the semantic pass records LLM tokens
            ledger = kg_metrics.UsageLedger()
            kg_metrics.set_ledger(ledger)
            t0 = time.perf_counter()
            result = run_pipeline(Path(b.root), semantic=b.semantic,
                                  resolution=b.resolution)
            ledger.record_phase("ingest", "kg_build", (time.perf_counter() - t0) * 1000)
            bridge.publish({"type": "build_done", "nodes": result.nodes,
                            "edges": result.edges,
                            "communities": result.communities,
                            "semantic_concepts": result.semantic_concepts,
                            "graph_path": str(result.graph_path)})
        except Exception as e:
            bridge.publish({"type": "build_error", "error": f"{type(e).__name__}: {e}"[:300]})
        finally:
            kg_metrics.set_ledger(None)
            events.set_bridge(None)

    loop.run_in_executor(None, _job)
    return {"started": True, "stream": "/api/build/stream"}


@app.get("/api/build/stream")
async def build_stream():
    """SSE for the most recent build job."""
    bridge = events.bridge()

    async def gen():
        if bridge is None:
            yield f"data: {events.dumps({'type': 'build_error', 'error': 'no build running'})}\n\n"
            return
        q = bridge.subscribe()
        try:
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=600)
                except asyncio.TimeoutError:
                    yield f"data: {events.dumps({'type': 'timeout'})}\n\n"
                    break
                yield f"data: {events.dumps(e)}\n\n"
                if e.get("type") in ("build_done", "build_error"):
                    break
        finally:
            bridge.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- ask endpoints (the live decision stream) ----------------------------------------


@app.post("/api/ask")
async def ask(a: AskIn):
    gp = default_graph_json()
    if not gp.exists():
        raise HTTPException(400, "no graph built yet - run a build first")

    bridge = events.SSEBridge()
    events.set_bridge(bridge)
    ledger = kg_metrics.UsageLedger()
    kg_metrics.set_ledger(ledger)
    llm_was_used = kg_config.llm_ready()

    def _publish(e: dict) -> None:
        bridge.publish(e)

    def _job_graph():
        try:
            from knowledgegraph.agents.graph import answer_question
            t0 = time.perf_counter()
            _publish(events.ev_run_started(a.question, "multi-agent graph", "graph",
                                           repo=graph_source_root()))
            answer = answer_question(a.question, graph_path=str(gp))
            ledger.record_phase("answer", "graph_ask", (time.perf_counter() - t0) * 1000)
            _publish(events.ev_synthesis("llm" if llm_was_used else "extractive",
                                         len(answer)))
            _publish(events.ev_metrics(ledger.summarize("graph-ask")))
            _publish(events.ev_done(answer))  # done LAST: consumers stop on it
        except Exception as e:
            _publish(events.ev_done("", f"{type(e).__name__}: {e}"[:300]))
        finally:
            kg_metrics.set_ledger(None)
            events.set_bridge(None)

    def _job_baseline():
        try:
            from knowledgegraph.baseline.react import iter_react
            root = baseline_root()
            _publish(events.ev_run_started(a.question, "ReAct baseline", "baseline",
                                           repo=str(root)))
            result = None
            for e in iter_react(a.question, root, max_steps=a.max_steps,
                                model=a.model):
                _publish(e)
                if e.get("type") == "result":
                    result = e["result"]
            _publish(events.ev_metrics(ledger.summarize("baseline-ask")))
            _publish(events.ev_done(result.answer if result else ""))  # done LAST
        except Exception as e:
            _publish(events.ev_done("", f"{type(e).__name__}: {e}"[:300]))
        finally:
            kg_metrics.set_ledger(None)
            events.set_bridge(None)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _job_graph if a.system == "graph" else _job_baseline)

    async def gen():
        # subscribe at stream start: the bridge replays the full transcript,
        # so events published before the consumer connected are not lost
        q = bridge.subscribe()
        try:
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=600)
                except asyncio.TimeoutError:
                    yield f"data: {events.dumps({'type': 'timeout'})}\n\n"
                    break
                yield f"data: {events.dumps(e)}\n\n"
                if e.get("type") in ("done",):
                    # keep draining briefly for trailing events, then stop
                    await asyncio.sleep(0.3)
                    break
        finally:
            bridge.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _bench_repo_root() -> Path:
    """The repo the baseline explores (kept for backward compat in tests)."""
    return baseline_root()


# --- run summary (metrics from the last ask) -------------------------------------------


@app.get("/api/last-run-metrics")
def last_run_metrics():
    # metrics are recorded into per-run ledgers owned by the ask job; expose
    # the bench files if present
    p = Path("kg-out/bench/graph-side.json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["summary"]
    return {"message": "no metrics yet - run kg-bench or an ask"}


@app.get("/")
def index():
    return HTMLResponse((_STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/app.js")
def app_js():
    return HTMLResponse((_STATIC / "app.js").read_text(encoding="utf-8"),
                        media_type="application/javascript")


@app.get("/style.css")
def style_css():
    return HTMLResponse((_STATIC / "style.css").read_text(encoding="utf-8"),
                        media_type="text/css")


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="kg-ui", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repo", default=None,
                        help="repo the baseline explores (default: cwd)")
    args = parser.parse_args(argv)

    if args.repo:
        os.environ["KG_UI_REPO"] = str(Path(args.repo).resolve())

    import uvicorn
    print(f"kg-ui: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
