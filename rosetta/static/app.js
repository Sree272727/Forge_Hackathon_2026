const API_BASE = (location.port === "7272" || location.protocol === "file:") ? "http://localhost:2727" : "";

(() => {
  const fileInput = document.getElementById("file");
  const filenameEl = document.getElementById("filename");
  const summaryEl = document.getElementById("summary");
  const summaryList = document.getElementById("summary-list");
  const sheetsList = document.getElementById("sheets-list");
  const namedList = document.getElementById("named-list");
  const findingsList = document.getElementById("findings-list");
  const messagesEl = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const chipGroup = document.getElementById("chip-group");

  const scenarioSection = document.getElementById("scenario-section");
  const scenariosEl = document.getElementById("scenarios");
  const entitySection = document.getElementById("active-entity-section");
  const activeEntityEl = document.getElementById("active-entity");

  let workbookId = null;
  let sessionId = localStorage.getItem("rosetta.session_id") || null;

  function setStatus(state, text) {
    statusDot.className = `dot ${state}`;
    statusText.textContent = text;
  }

  function clearMessages() {
    messagesEl.innerHTML = "";
  }

  function fmt(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
      if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
      if (Number.isInteger(v)) return String(v);
      return v.toFixed(4).replace(/\.?0+$/, "");
    }
    return String(v);
  }

  function renderTrace(node) {
    if (!node) return "";
    const wrapper = document.createElement("div");
    wrapper.className = "node";
    const label = node.label ? ` <span class="label">(${escapeHtml(node.label)})</span>` : "";
    const val = ` = <span class="val">${escapeHtml(fmt(node.value))}</span>`;
    const nr = node.named_range ? ` <span class="nr">[${escapeHtml(node.named_range)}]</span>` : "";
    const hc = node.is_hardcoded ? ` <span class="hc">[hardcoded]</span>` : "";
    const vol = node.is_volatile ? ` <span class="warn">[volatile]</span>` : "";
    let head = `<div><span class="ref">${escapeHtml(node.ref)}</span>${label}${val}${nr}${hc}${vol}</div>`;
    if (node.formula) head += `<div class="fx">= ${escapeHtml(node.formula)}</div>`;
    for (const w of node.warnings || []) {
      head += `<div class="warn">⚠ ${escapeHtml(w)}</div>`;
    }
    wrapper.innerHTML = head;
    for (const c of node.children || []) {
      wrapper.appendChild(renderTrace(c));
    }
    return wrapper;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function addMessage(role, text, extras = {}) {
    // Remove empty placeholder
    const empty = messagesEl.querySelector(".empty");
    if (empty) empty.remove();

    const bubble = document.createElement("div");
    bubble.className = `bubble ${role}`;
    if (role === "assistant") {
      const meta = document.createElement("div");
      meta.className = "meta";

      // Audit status badge (green/yellow/red)
      if (extras.audit_status) {
        const auditBadge = document.createElement("span");
        auditBadge.className = `badge audit-${extras.audit_status}`;
        auditBadge.textContent = {
          "passed": "✓ grounded",
          "partial": "⚠ partial",
          "unknown": "✗ unverified",
        }[extras.audit_status] || extras.audit_status;
        meta.appendChild(auditBadge);
      }

      // Tool-call badge
      if (extras.escalated) {
        const badge = document.createElement("span");
        badge.className = "badge escalated";
        badge.textContent = "tool-calling";
        meta.appendChild(badge);
      }

      // Tool count
      if (typeof extras.tool_calls_made === "number" && extras.tool_calls_made > 0) {
        const tc = document.createElement("span");
        tc.textContent = `${extras.tool_calls_made} tool calls`;
        meta.appendChild(tc);
      }

      if (typeof extras.confidence === "number") {
        const conf = document.createElement("span");
        conf.textContent = `confidence ${extras.confidence.toFixed(2)}`;
        meta.appendChild(conf);
      }
      bubble.appendChild(meta);
    }
    const body = document.createElement("div");
    body.className = "body";
    body.textContent = text;
    bubble.appendChild(body);
    if (extras.trace) {
      const det = document.createElement("details");
      det.className = "trace";
      const summ = document.createElement("summary");
      summ.textContent = "Formula trace";
      det.appendChild(summ);
      det.appendChild(renderTrace(extras.trace));
      bubble.appendChild(det);
    }
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  function showTyping() {
    const bubble = document.createElement("div");
    bubble.className = "bubble assistant typing";
    bubble.textContent = "Thinking";
    bubble.id = "typing-indicator";
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  function hideTyping() {
    const t = document.getElementById("typing-indicator");
    if (t) t.remove();
  }

  // --- Upload ---
  fileInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    filenameEl.textContent = file.name;
    setStatus("loading", "Parsing workbook…");
    input.disabled = true;
    sendBtn.disabled = true;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`${API_BASE}/ingest`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const data = await res.json();
      workbookId = data.workbook_id;
      // Reset session when switching workbooks
      sessionId = null;
      localStorage.removeItem("rosetta.session_id");
      clearMessages();
      renderSummary(data.summary);
      summaryEl.classList.remove("hidden");
      setStatus("ready", `${file.name} loaded`);
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
      addMessage("assistant", "Workbook parsed. Ask me about any formula, value, dependency, or issue.", { confidence: 1 });
    } catch (err) {
      setStatus("error", "Upload failed");
      addMessage("assistant", `Upload failed: ${err.message}`);
    }
  });

  function renderSummary(s) {
    summaryList.innerHTML = "";
    const items = [
      ["Sheets", s.sheet_count],
      ["Total cells", s.total_cells],
      ["Formula cells", s.formula_cells],
      ["Cross-sheet refs", s.cross_sheet_references],
      ["Max dependency depth", s.max_dependency_depth],
      ["Named ranges", (s.named_ranges || []).length],
      ["Circular references", (s.circular_references || []).length],
    ];
    for (const [k, v] of items) {
      const li = document.createElement("li");
      li.innerHTML = `${k}: <b>${v}</b>`;
      summaryList.appendChild(li);
    }
    sheetsList.innerHTML = "";
    for (const sh of s.sheets || []) {
      const li = document.createElement("li");
      const hidden = sh.hidden ? " (hidden)" : "";
      li.textContent = `${sh.name}${hidden} — ${sh.rows} rows, ${sh.formulas} formulas`;
      sheetsList.appendChild(li);
    }
    namedList.innerHTML = "";
    for (const nr of s.named_ranges || []) {
      const li = document.createElement("li");
      const dyn = nr.is_dynamic ? " [dynamic]" : "";
      li.innerHTML = `<code>${escapeHtml(nr.name)}</code> → ${escapeHtml((nr.resolves_to || [])[0] || "")} (= ${escapeHtml(fmt(nr.value))})${dyn}`;
      namedList.appendChild(li);
    }
    findingsList.innerHTML = "";
    const counts = s.finding_counts || {};
    if (Object.keys(counts).length === 0) {
      findingsList.innerHTML = "<li>No issues found.</li>";
    } else {
      for (const [cat, n] of Object.entries(counts)) {
        const li = document.createElement("li");
        li.innerHTML = `${cat}: <b>${n}</b>`;
        findingsList.appendChild(li);
      }
    }
  }

  function renderScenarios(overrides) {
    const entries = Object.entries(overrides || {});
    if (entries.length === 0) {
      scenarioSection.classList.add("hidden");
      scenariosEl.innerHTML = "";
      return;
    }
    scenarioSection.classList.remove("hidden");
    scenariosEl.innerHTML = entries.map(([k, v]) =>
      `<span class="scenario-chip"><span>${escapeHtml(k)}: ${escapeHtml(fmt(v))}</span><button data-ref="${escapeHtml(k)}" class="clear-scenario" title="Clear">✕</button></span>`
    ).join("");
  }

  function renderActiveEntity(ref) {
    if (!ref) {
      entitySection.classList.add("hidden");
      return;
    }
    entitySection.classList.remove("hidden");
    activeEntityEl.textContent = ref;
  }

  scenariosEl.addEventListener("click", async (e) => {
    const btn = e.target.closest(".clear-scenario");
    if (!btn || !sessionId) return;
    const ref = btn.dataset.ref;
    try {
      const res = await fetch(`${API_BASE}/chat/${sessionId}/scenario?ref=${encodeURIComponent(ref)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        const data = await res.json();
        renderScenarios(data.scenario_overrides);
      }
    } catch (err) { /* ignore */ }
  });

  // --- Send ---
  async function sendMessage(text) {
    if (!workbookId) {
      addMessage("assistant", "Upload a workbook first.");
      return;
    }
    addMessage("user", text);
    input.value = "";
    sendBtn.disabled = true;
    showTyping();
    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ workbook_id: workbookId, message: text, session_id: sessionId }),
      });
      hideTyping();
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const data = await res.json();
      sessionId = data.session_id;
      localStorage.setItem("rosetta.session_id", sessionId);
      addMessage("assistant", data.answer, {
        trace: data.trace,
        escalated: data.escalated,
        confidence: data.confidence,
        audit_status: data.audit_status,
        tool_calls_made: data.tool_calls_made,
      });
      if (data.scenario_overrides !== undefined) renderScenarios(data.scenario_overrides);
      if (data.active_entity !== undefined) renderActiveEntity(data.active_entity);
    } catch (err) {
      hideTyping();
      addMessage("assistant", `Error: ${err.message}`);
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    sendMessage(text);
  });

  chipGroup.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    const q = btn.dataset.q;
    if (!q) return;
    if (!workbookId) {
      addMessage("assistant", "Upload a workbook first.");
      return;
    }
    sendMessage(q);
  });

  setStatus("idle", "No workbook loaded");
})();
