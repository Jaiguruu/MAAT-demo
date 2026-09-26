/* kg-ui frontend: tabs, SSE ask stream with live decision timeline,
   D3 force graph, settings management. No build step, no dependencies
   except D3 from CDN (loaded lazily). */
"use strict";

const $ = (sel) => document.querySelector(sel);

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-page").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "graph") initGraphTab();
    if (btn.dataset.tab === "settings") initSettingsTab();
  };
});

// ---------- settings ----------
let settingsCache = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

async function initSettingsTab() {
  settingsCache = await api("/api/settings");
  $("#set-base-url").value = settingsCache.base_url || "";
  $("#set-model").value = settingsCache.model || "";
  $("#set-semantic-model").value = settingsCache.semantic_model || "";
  $("#set-agent-model").value = settingsCache.agent_model || "";
  if (settingsCache.repos) {
    const bi = document.createElement("label");
    // baseline repo override lives in Settings too (advanced)
    bi.innerHTML = `Baseline explore folder <span class="hint">(blank = follow the graph's source repo)</span>
      <input id="set-baseline-repo" placeholder="${(settingsCache.repos.baseline_root || "").replace(/"/g, "&quot;")}">`;
    const pane = document.querySelector("#tab-settings .pane");
    if (!document.getElementById("set-baseline-repo")) pane.insertBefore(bi, pane.querySelector(".row"));
  }
  // never echo the key back; placeholder shows whether one is set
  $("#set-api-key").value = "";
  $("#set-api-key").placeholder = settingsCache.api_key_set ? "•••••• (key set)" : "sk-…";
  renderEffective(settingsCache.effective);
  refreshModels();
  refreshConnBadge();
}

function renderEffective(eff) {
  $("#settings-effective").textContent =
    `effective base_url: ${eff.base_url || "(default)"}\n` +
    `effective model:     ${eff.model}\n` +
    `semantic model:      ${eff.semantic_model}\n` +
    `llm_ready:           ${eff.llm_ready}`;
}

async function refreshModels() {
  try {
    const m = await api("/api/models");
    const dl = $("#model-list");
    dl.innerHTML = "";
    const seen = new Set();
    for (const mo of [...m.curated.map(c => c.id), ...m.provider_models]) {
      if (seen.has(mo)) continue;
      seen.add(mo);
      dl.appendChild(new Option(mo));
    }
    $("#model-catalog").textContent =
      m.curated.map(c => `${c.id}  —  ${c.note}`).join("\n") +
      (m.provider_models.length
        ? `\n\nprovider serves ${m.provider_models.length} models (first 200 listed in the datalist)`
        : "\n\n(could not list provider models — set a key in Settings and save first)");
  } catch (e) {
    $("#model-catalog").textContent = `error: ${e.message}`;
  }
}

async function refreshConnBadge() {
  const s = await api("/api/settings");
  const b = $("#conn-badge");
  if (s.effective.llm_ready) { b.textContent = "LLM ready"; b.className = "badge ok"; }
  else { b.textContent = "offline mode (no key)"; b.className = "badge warn"; }
}

$("#save-settings").onclick = async () => {
  const body = {
    base_url: $("#set-base-url").value.trim() || null,
    model: $("#set-model").value.trim() || null,
    semantic_model: $("#set-semantic-model").value.trim() || null,
    agent_model: $("#set-agent-model").value.trim() || null,
  };
  const repoInput = document.getElementById("set-baseline-repo");
  if (repoInput) body.baseline_repo = repoInput.value.trim() || null;
  const key = $("#set-api-key").value.trim();
  if (key) body.api_key = key; // only send when changed
  settingsCache = await api("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  renderEffective(settingsCache.effective);
  refreshConnBadge(); refreshModels();
};

$("#test-conn").onclick = async () => {
  const el = $("#test-result");
  el.textContent = "testing…"; el.className = "badge";
  const body = {
    base_url: $("#set-base-url").value.trim() || null,
    model: $("#set-model").value.trim() || null,
  };
  const key = $("#set-api-key").value.trim();
  if (key) body.api_key = key;
  try {
    const r = await api("/api/settings/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      el.textContent = `ok: ${r.model} in ${r.latency_ms}ms`;
      el.className = "badge ok";
    } else {
      el.textContent = `fail: ${r.error}`; el.className = "badge err";
    }
  } catch (e) { el.textContent = `fail: ${e.message}`; el.className = "badge err"; }
};

// ---------- ask tab ----------
$("#ask-btn").onclick = runAsk;

function addTimelineItem(cls, html) {
  const li = document.createElement("li");
  li.className = cls;
  const t = new Date().toLocaleTimeString();
  li.innerHTML = `<span class="t">${t}</span>${html}`;
  const ol = $("#timeline");
  ol.appendChild(li);
  ol.parentElement.scrollTop = ol.parentElement.scrollHeight;
  return li;
}

function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

async function runAsk() {
  const question = $("#question").value.trim();
  if (!question) return;
  const system = $("#system").value;
  const btn = $("#ask-btn");
  btn.disabled = true;
  $("#timeline").innerHTML = "";
  $("#answer").textContent = "(working…)";
  $("#ask-metrics").textContent = "";

  const body = {
    question, system,
    max_steps: parseInt($("#max-steps").value) || 12,
    model: $("#ask-model").value.trim() || null,
  };
  addTimelineItem("ev-plan",
    `<b>run started</b> <span class="detail">${esc(system)} system${body.model ? " · model " + esc(body.model) : ""}</span>`);

  try {
    const resp = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.status);

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", answerChars = 0, callCount = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        handleAskEvent(ev);
        }
    }
  } catch (e) {
    addTimelineItem("ev-tool_result miss", `<b>error</b> <span class="detail">${esc(e.message)}</span>`);
    $("#answer").textContent = `error: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function handleAskEvent(ev) {
  switch (ev.type) {
    case "run_started":
      $("#mode-note").textContent = `system: ${ev.mode}` +
        (ev.repo ? ` · exploring: ${ev.repo}` : "");
      refreshRepoNote();
      break;
    case "plan":
      addTimelineItem("ev-plan",
        `<span class="who">supervisor</span> routed to <b>${esc(ev.route.join(", "))}</b>` +
        ` <span class="detail">[${esc(ev.source)}]</span>` +
        (ev.raw ? `\n<span class="detail">${esc(ev.raw)}</span>` : ""));
      break;
    case "tool_call":
      addTimelineItem("", `<span class="who">${esc(ev.agent)}</span> → ${esc(ev.tool)}`);
      break;
    case "tool_result": {
      const cls = `ev-tool_result ${ev.ok ? "ok" : "miss"}`;
      addTimelineItem(cls,
        `<span class="who">${esc(ev.agent)}</span> ${esc(ev.tool)} ${ev.ok ? "✓" : "✗"}` +
        `\n<span class="detail">${esc(ev.summary)}</span>`);
      break;
    }
    case "react_started":
      addTimelineItem("ev-plan", `<b>ReAct loop</b> <span class="detail">budget ${ev.max_steps} steps · repo ${esc(ev.repo)}</span>`);
      break;
    case "react_action":
      addTimelineItem("ev-react_action",
        `<span class="who">step ${ev.step}</span> ${esc(ev.tool)}` +
        (ev.arg ? ` <span class="detail">${esc(ev.arg)}</span>` : "") +
        (ev.out_chars ? ` <span class="detail">→ ${ev.out_chars} chars</span>` : ""));
      break;
    case "react_observation":
      addTimelineItem("", `<span class="detail">└ ${esc(ev.preview)}</span>`);
      break;
    case "react_finish":
      addTimelineItem("ev-synthesis", `<span class="who">step ${ev.step}</span> <b>finish</b> — model chose to answer`);
      break;
    case "react_error":
      addTimelineItem("ev-tool_result miss", `<b>LLM error</b> <span class="detail">${esc(ev.error)}</span>`);
      break;
    case "synthesis":
      addTimelineItem("ev-synthesis",
        `<span class="who">synthesizer</span> mode: <b>${esc(ev.mode)}</b> (${ev.answer_chars} chars)`);
      break;
    case "metrics": {
      const s = ev.summary || {};
      const bp = s.by_purpose || {};
      const rows = Object.entries(bp)
        .filter(([, v]) => v.calls > 0)
        .map(([k, v]) => `  ${k}: ${v.calls} call(s), ${v.prompt_tokens + v.completion_tokens} tokens`)
        .join("\n");
      $("#ask-metrics").textContent =
        `LLM calls: ${s.llm_calls ?? 0} · tokens: ${s.total_tokens ?? 0} · ` +
        `cost: $${(s.cost_usd ?? 0).toFixed(4)} · failed: ${s.failed_calls ?? 0}\n` +
        (rows || "  (no LLM calls this run)");
      break;
    }
    case "done": {
      addTimelineItem("ev-done", `<b>done</b> (${ev.answer_chars} chars${ev.error ? " · " + esc(ev.error) : ""})`);
      const answerEl = $("#answer");
      if (ev.error && !ev.answer) {
        answerEl.textContent = `error: ${ev.error}`;
      } else {
        answerEl.textContent = ev.answer || "(no answer produced)";
      }
      break;
    }
    case "result":
      $("#answer").textContent = ev.result.answer;
      $("#ask-metrics").textContent =
        `steps: ${ev.result.steps.length} · finish: ${ev.result.finish_reason}\n` +
        ev.result.steps.map(s => `  ${s.step}. ${s.action}${s.arg ? " " + s.arg : ""}${s.out_chars ? ` (${s.out_chars}c)` : ""}`).join("\n");
      break;
  }
}

// ---------- graph tab ----------
let graphInitialized = false;

async function initGraphTab() {
  await loadGraphView();
}

async function loadGraphView() {
  const status = $("#build-status");
  try {
    const data = await api("/api/graph");
    renderMeta(data);
    await ensureD3();
    renderForceGraph(data);
    graphInitialized = true;
  } catch (e) {
    status.textContent = "no graph — build one";
    status.className = "badge warn";
    $("#graph-meta").textContent = e.message;
  }
}

function renderMeta(data) {
  const g = data.meta.graph || {};
  const counts = {};
  for (const n of data.nodes) counts[n.file_type] = (counts[n.file_type] || 0) + 1;
  $("#graph-meta").textContent =
    `${data.nodes.length} nodes · ${data.links.length} links\n` +
    Object.entries(counts).map(([k, v]) => `  ${k}: ${v}`).join("\n") +
    (g.community_labels ? `\ncommunities: ${Object.keys(g.community_labels).length}` : "");
}

$("#build-btn").onclick = async () => {
  const root = $("#build-root").value.trim();
  if (!root) { $("#build-status").textContent = "enter a repo folder"; return; }
  const btn = $("#build-btn");
  btn.disabled = true;
  $("#build-status").textContent = "building…";
  $("#build-status").className = "badge";
  try {
    await api("/api/graph/build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root, semantic: $("#build-semantic").checked }),
    });
    // after a build from a new repo, the baseline should follow it
    await api("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseline_repo: null }),
    });
    await consumeSSE("/api/build/stream", (ev) => {
      if (ev.type === "build_done") {
        $("#build-status").textContent = `done: ${ev.nodes} nodes, ${ev.edges} edges (+${ev.semantic_concepts} concepts)`;
        $("#build-status").className = "badge ok";
        loadGraphView();
        return true;
      }
      if (ev.type === "build_error") {
        $("#build-status").textContent = `error: ${ev.error}`;
        $("#build-status").className = "badge err";
        return true;
      }
      return false;
    });
  } catch (e) {
    $("#build-status").textContent = `error: ${e.message}`;
    $("#build-status").className = "badge err";
  } finally {
    btn.disabled = false;
  }
};

async function consumeSSE(url, onEvent) {
  const resp = await fetch(url);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
      if (!chunk.startsWith("data: ")) continue;
      if (onEvent(JSON.parse(chunk.slice(6)))) return;
    }
  }
}

async function ensureD3() {
  if (window.d3) return;
  await new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js";
    s.onload = res; s.onerror = () => rej(new Error("D3 failed to load (offline?)"));
    document.head.appendChild(s);
  });
}

const TYPE_COLORS = { code: "#5b9dff", document: "#4cc38a", concept: "#d9a53f", paper: "#b48cf2" };

function renderForceGraph(data) {
  const svg = d3.select("#graph-svg");
  svg.selectAll("*").remove();
  const width = svg.node().clientWidth, height = svg.node().clientHeight;

  const g = svg.append("g");
  svg.call(d3.zoom().scaleExtent([0.2, 4]).on("zoom", (e) => g.attr("transform", e.transform)));

  const nodes = data.nodes.map(n => ({ ...n }));
  const links = data.links.map(l => ({ ...l }));

  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id).distance(60).strength(0.4))
    .force("charge", d3.forceManyBody().strength(-140))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(16));

  const link = g.append("g").selectAll("line").data(links).join("line")
    .attr("class", d => d.confidence === "INFERRED" ? "link inferred" : "link");

  const node = g.append("g").selectAll("g").data(nodes).join("g")
    .attr("class", "node");

  node.append("circle")
    .attr("r", d => d.file_type === "concept" ? 8 : d.file_type === "document" || d.file_type === "paper" ? 7 : 5)
    .attr("fill", d => TYPE_COLORS[d.file_type] || "#6b7280");

  node.append("text")
    .attr("dx", 10).attr("dy", 4)
    .text(d => d.label.length > 22 ? d.label.slice(0, 21) + "…" : d.label);

  node.on("click", async (e, d) => {
    try {
      const detail = await api(`/api/graph/node/${encodeURIComponent(d.id)}`);
      const n = detail.node;
      $("#node-detail").textContent =
        `${n.label}  [${n.file_type}]\n` +
        `source: ${n.source_file || "-"} ${n.source_location || ""}\n` +
        (n.summary ? `summary: ${n.summary}\n` : "") +
        `\nconnections (${detail.neighbors.length}):\n` +
        detail.neighbors.map(x =>
          `  ${x.relation} ${x.confidence || ""} → ${x.label} [${x.file_type}]`).join("\n");
    } catch (err) {
      $("#node-detail").textContent = `error: ${err.message}`;
    }
  }).call(d3.drag()
    .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  sim.on("tick", () => {
    link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });
}

// ---------- repo consistency ----------
async function refreshRepoNote() {
  try {
    const s = await api("/api/settings");
    const r = s.repos || {};
    const el = $("#repo-note");
    const alignBtn = $("#align-repo");
    if (!r.graph_source) {
      el.textContent = "graph: (not built yet) · baseline explores: " + (r.baseline_root || "?");
      alignBtn.style.display = "none";
    } else if (r.graph_source === r.baseline_root) {
      el.textContent = "both systems target: " + r.graph_source;
      alignBtn.style.display = "none";
    } else {
      el.innerHTML = `graph built from: <b>${esc(r.graph_source)}</b><br>` +
        `baseline explores: <b>${esc(r.baseline_root)}</b> — answers will come from different repos!`;
      alignBtn.style.display = "";
    }
  } catch {}
}

$("#align-repo").onclick = async () => {
  const s = await api("/api/settings");
  const src = s.repos && s.repos.graph_source;
  if (!src) return;
  await api("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ baseline_repo: src }),
  });
  refreshRepoNote();
};

// ---------- boot ----------
refreshConnBadge();
refreshRepoNote();
$("#system").onchange = () => {
  const baseline = $("#system").value === "baseline";
  $("#max-steps").style.display = baseline ? "" : "none";
  $("#mode-note").textContent = baseline
    ? "baseline explores raw files with list_files / read_file / grep — no graph"
    : "multi-agent routes to orient / explorer / impact workers over the graph";
};
