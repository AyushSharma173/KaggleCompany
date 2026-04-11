/* Kaggle Company Dashboard — vanilla JS, manual SVG layout, no D3.
 *
 * State machine:
 *   DATA = whole index.json
 *   currentDate = which date's run we're viewing
 *   currentStep = index into the timeline (0-based)
 *   selectedAgent = id of the agent in the detail panel (or null)
 *
 * Render flow:
 *   loadData() -> render()
 *   render() -> renderHeader, renderOrgChart, renderSlider, renderDetail
 */

// === State ===
let DATA = null;
let currentDate = null;
let currentRun = null;       // DATA.runs[currentDate]
let currentStep = 0;
let selectedAgent = null;
let currentTab = "summary";
let agentDetailCache = {};
let eventLookup = {};        // eventId -> event object, for modal lookups
let nextEventId = 0;
let lastGeneratedAt = null;  // For change detection
const POLL_INTERVAL_MS = 3000;

// === Colors ===
const COLORS = {
    ceo: "#ef5350",
    vp: "#4fc3f7",
    worker: "#66bb6a",
    subagent: "#ffb74d",
    consolidation: "#ce93d8",
};

const TOOL_ICONS = {
    web_fetch: "🌐",
    web_search: "🔍",
    deep_research: "🧠",
    send_slack_message: "💬",
    create_worker_agent: "👤",
    spawn_research: "👥",
    spawn_subagents: "👥",
    save_report: "📄",
    report_progress: "📊",
};

// === Helpers ===
function colorOf(role) { return COLORS[role] || "#888"; }

function fmtTime(ts) {
    if (!ts) return "--";
    const m = ts.match(/T(\d{2}:\d{2}:\d{2})/);
    return m ? m[1] : ts.slice(11, 19);
}

function fmtCost(c) {
    return c == null ? "--" : "$" + Number(c).toFixed(2);
}

function fmtTokens(n) {
    if (!n) return "0";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(n);
}

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function el(tag, attrs = {}) {
    // SVG namespace required for SVG elements
    const SVG_NS = "http://www.w3.org/2000/svg";
    const SVG_TAGS = new Set(["svg", "g", "rect", "text", "line", "path", "circle"]);
    const node = SVG_TAGS.has(tag)
        ? document.createElementNS(SVG_NS, tag)
        : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === "text") {
            node.textContent = v;
        } else if (k === "html") {
            node.innerHTML = v;
        } else if (k === "on") {
            for (const [evt, handler] of Object.entries(v)) {
                node.addEventListener(evt, handler);
            }
        } else if (k === "class") {
            node.setAttribute("class", v);
        } else {
            node.setAttribute(k, v);
        }
    }
    return node;
}

// === Data loading ===
async function loadData() {
    console.log("[dashboard] Loading data...");
    const data = await fetchIndex();
    if (!data) return;

    DATA = data;
    lastGeneratedAt = data.generated_at;
    console.log("[dashboard] Loaded:", DATA.dates, "default:", DATA.default_date);

    if (!DATA.dates || DATA.dates.length === 0) {
        showWaiting();
        return;
    }

    currentDate = DATA.default_date;
    currentRun = DATA.runs[currentDate];
    currentStep = currentRun.timeline.length > 0 ? currentRun.timeline.length - 1 : 0;
    selectedAgent = null;
    render();

    // Start polling for live updates
    startPolling();
}

async function fetchIndex() {
    try {
        const resp = await fetch("data/index.json", { cache: "no-store" });
        if (!resp.ok) {
            showError("Failed to load data/index.json: " + resp.status);
            return null;
        }
        return await resp.json();
    } catch (e) {
        console.error("[dashboard] Fetch failed:", e);
        return null;
    }
}

function showWaiting() {
    const oc = document.getElementById("orgchart-section");
    oc.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#8888aa;text-align:center">
        <div>
            <div style="font-size:24px;margin-bottom:12px">⏳</div>
            <div>Waiting for agents to start...</div>
            <div style="font-size:11px;margin-top:8px">The dashboard polls for new data every ${POLL_INTERVAL_MS/1000}s.</div>
        </div>
    </div>`;
    // Keep polling until data shows up
    setTimeout(loadData, POLL_INTERVAL_MS);
}

function startPolling() {
    setInterval(async () => {
        const newData = await fetchIndex();
        if (!newData || !newData.generated_at) return;
        if (newData.generated_at === lastGeneratedAt) return;  // No change

        console.log("[dashboard] New data detected:", newData.generated_at);
        applyDataUpdate(newData);
    }, POLL_INTERVAL_MS);
}

function applyDataUpdate(newData) {
    const oldRun = currentRun;
    const oldStep = currentStep;
    const oldTimelineLength = oldRun ? oldRun.timeline.length : 0;
    const wasAtEnd = oldRun && oldStep >= oldTimelineLength - 1;

    DATA = newData;
    lastGeneratedAt = newData.generated_at;

    // If our current date no longer exists, switch to the new default
    if (!DATA.runs[currentDate]) {
        currentDate = DATA.default_date;
        currentStep = 0;
    }

    currentRun = DATA.runs[currentDate];
    if (!currentRun) return;

    const newLen = currentRun.timeline.length;

    // Live tail behavior: if user was at the last step before the update,
    // advance to the new last step. Otherwise stay where they were.
    if (wasAtEnd && newLen > 0) {
        currentStep = newLen - 1;
    } else {
        currentStep = Math.min(currentStep, Math.max(0, newLen - 1));
    }

    // Invalidate any cached agent detail files for the current date
    // (so the next click loads fresh data). Keep other dates cached.
    for (const key of Object.keys(agentDetailCache)) {
        if (key.endsWith(`__${currentDate}`)) {
            delete agentDetailCache[key];
        }
    }
    // Clear event lookup since events may have new ids
    eventLookup = {};
    nextEventId = 0;

    render();

    // If user has an agent selected, refresh its detail panel content
    if (selectedAgent) {
        renderDetail();
    }
}

async function loadAgentDetail(agentId) {
    const key = `${agentId}__${currentDate}`;
    if (agentDetailCache[key]) return agentDetailCache[key];
    try {
        const resp = await fetch(`data/agents/${key}.json`);
        if (!resp.ok) return null;
        const detail = await resp.json();
        agentDetailCache[key] = detail;
        return detail;
    } catch (e) {
        console.error("Failed to load agent detail:", e);
        return null;
    }
}

function showError(msg) {
    document.body.innerHTML = `<div style="padding:40px;color:#ef5350;font-family:monospace">${escapeHtml(msg)}</div>`;
}

function registerEvent(event) {
    const id = "evt_" + (nextEventId++);
    eventLookup[id] = event;
    return id;
}

function openEventById(id) {
    const event = eventLookup[id];
    if (event) openEventModal(event);
}
window.openEventById = openEventById;

// === Render entry point ===
function render() {
    renderHeader();
    renderOrgChart();
    renderSlider();
    renderDetail();
}

// === Header (date pills + cost) ===
function renderHeader() {
    const pills = document.getElementById("date-pills");
    pills.innerHTML = "";
    for (const date of DATA.dates) {
        const btn = el("button", {
            class: "date-pill" + (date === currentDate ? " active" : ""),
            text: date,
            on: {
                click: () => {
                    currentDate = date;
                    currentRun = DATA.runs[date];
                    currentStep = currentRun.timeline.length > 0 ? currentRun.timeline.length - 1 : 0;
                    selectedAgent = null;
                    render();
                },
            },
        });
        pills.appendChild(btn);
    }

    document.getElementById("cost-display").textContent = fmtCost(currentRun.total_cost);
}

// === Org chart (manual SVG layout) ===
function buildVisibleHierarchy() {
    // Determine which agents exist at the current timestamp
    const cutoff = currentRun.timeline[currentStep]?.ts || "9999";

    // First-event timestamp per agent (CEO is always present)
    const firstEvent = { ceo: "0000" };
    for (const e of currentRun.timeline) {
        if (firstEvent[e.agent_id] == null) firstEvent[e.agent_id] = e.ts;
    }

    const visible = new Set();
    for (const aid of Object.keys(currentRun.agents)) {
        const fe = firstEvent[aid];
        if (fe != null && fe <= cutoff) visible.add(aid);
    }
    if (visible.size === 0) visible.add("ceo");

    // Build hierarchy starting from ceo
    function build(id) {
        const a = currentRun.agents[id];
        if (!a) return null;
        const node = {
            id, role: a.role, name: a.name,
            children: [],
        };
        for (const childId of (a.children || [])) {
            if (visible.has(childId)) {
                const childNode = build(childId);
                if (childNode) node.children.push(childNode);
            }
        }
        return node;
    }
    return build("ceo");
}

function layoutTree(root) {
    // Compute BFS levels
    const levels = [];
    function walk(node, depth) {
        if (!levels[depth]) levels[depth] = [];
        levels[depth].push(node);
        node._depth = depth;
        for (const c of node.children) walk(c, depth + 1);
    }
    walk(root, 0);

    // Compute layout: each level evenly distributed horizontally, evenly spaced vertically
    const NODE_W = 130;
    const NODE_H = 44;
    const V_GAP = 60;
    const H_GAP = 16;

    const maxLevelWidth = Math.max(...levels.map(l => l.length));
    const totalWidth = Math.max(maxLevelWidth * (NODE_W + H_GAP), 600);
    const totalHeight = (levels.length - 1) * (NODE_H + V_GAP) + NODE_H + 40;

    levels.forEach((level, depth) => {
        const y = 30 + depth * (NODE_H + V_GAP) + NODE_H / 2;
        const stepX = totalWidth / (level.length + 1);
        level.forEach((node, i) => {
            node._x = stepX * (i + 1);
            node._y = y;
        });
    });

    // Flatten for easy iteration
    const allNodes = [];
    levels.forEach(l => l.forEach(n => allNodes.push(n)));

    // Build edges
    const edges = [];
    for (const node of allNodes) {
        for (const child of node.children) {
            edges.push({ source: node, target: child });
        }
    }

    return { nodes: allNodes, edges, width: totalWidth, height: totalHeight };
}

function renderOrgChart() {
    const svg = document.getElementById("orgchart");
    // Clear
    svg.innerHTML = "";

    if (!currentRun || !currentRun.timeline.length) {
        return;
    }

    const root = buildVisibleHierarchy();
    if (!root) return;

    const layout = layoutTree(root);

    // Set SVG viewBox to fit the layout
    svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    const NODE_W = 130;
    const NODE_H = 44;

    const activeAgentId = currentRun.timeline[currentStep]?.agent_id;

    // Draw edges first (so they're behind nodes)
    for (const edge of layout.edges) {
        const sx = edge.source._x;
        const sy = edge.source._y + NODE_H / 2;
        const tx = edge.target._x;
        const ty = edge.target._y - NODE_H / 2;
        const my = sy + (ty - sy) / 2;
        // Orthogonal flow chart link: down, across, down
        const d = `M${sx},${sy} L${sx},${my} L${tx},${my} L${tx},${ty}`;
        svg.appendChild(el("path", { class: "link-line", d }));
    }

    // Draw nodes
    for (const node of layout.nodes) {
        const g = el("g", {
            transform: `translate(${node._x}, ${node._y})`,
            on: {
                click: (e) => {
                    e.stopPropagation();
                    selectedAgent = (selectedAgent === node.id) ? null : node.id;
                    render();
                },
            },
        });

        const isSelected = node.id === selectedAgent;
        const isActive = node.id === activeAgentId;
        let rectClass = "node-rect";
        if (isSelected) rectClass += " selected";
        else if (isActive) rectClass += " active";

        g.appendChild(el("rect", {
            class: rectClass,
            x: -NODE_W / 2,
            y: -NODE_H / 2,
            width: NODE_W,
            height: NODE_H,
            fill: colorOf(node.role),
        }));

        g.appendChild(el("text", {
            class: "node-label",
            x: 0,
            y: -3,
            text: node.name,
        }));

        const summary = currentRun.summaries[node.id];
        const meta = summary ? `${fmtCost(summary.total_cost)} · ${node.role}` : node.role;
        g.appendChild(el("text", {
            class: "node-meta",
            x: 0,
            y: 14,
            text: meta,
        }));

        svg.appendChild(g);
    }

    console.log("[orgchart] rendered:", layout.nodes.length, "nodes,", layout.edges.length, "edges,", layout.width, "x", layout.height);
}

// === Slider ===
function renderSlider() {
    const slider = document.getElementById("slider");
    const total = currentRun.timeline.length;
    slider.max = Math.max(0, total - 1);
    slider.value = currentStep;

    const event = currentRun.timeline[currentStep];
    document.getElementById("step-label").textContent = `Step ${currentStep + 1}/${total}`;

    if (event) {
        document.getElementById("step-time").textContent = fmtTime(event.ts);
        const name = currentRun.agents[event.agent_id]?.name || event.agent_id;
        const role = currentRun.agents[event.agent_id]?.role;
        const agentEl = document.getElementById("step-agent");
        agentEl.textContent = name;
        agentEl.style.color = colorOf(role);
        let typeStr = event.type;
        if (event.tool_name) typeStr += `(${event.tool_name})`;
        document.getElementById("step-type").textContent = typeStr;
        document.getElementById("step-summary").textContent = event.summary || "";
    }

    document.getElementById("btn-prev").disabled = currentStep === 0;
    document.getElementById("btn-first").disabled = currentStep === 0;
    document.getElementById("btn-next").disabled = currentStep >= total - 1;
    document.getElementById("btn-last").disabled = currentStep >= total - 1;
}

function setStep(n) {
    const newStep = Math.max(0, Math.min(currentRun.timeline.length - 1, n));
    if (newStep === currentStep) return;
    currentStep = newStep;
    renderOrgChart();
    renderSlider();
    if (selectedAgent) renderDetail();
}

// === Detail panel ===
function renderDetail() {
    document.querySelectorAll(".tab").forEach(t => {
        t.classList.toggle("active", t.dataset.tab === currentTab);
    });

    const content = document.getElementById("detail-content");

    if (!selectedAgent) {
        const event = currentRun.timeline[currentStep];
        const agent = event ? currentRun.agents[event.agent_id] : null;
        content.innerHTML = `
            <div class="welcome">
                Click any agent in the org chart above to inspect it.<br>
                ${event ? `<br><strong style="color:#fff">Current event:</strong> <span style="color:${colorOf(agent?.role)}">${escapeHtml(agent?.name || event.agent_id)}</span> · ${escapeHtml(event.type)}${event.tool_name ? `(${escapeHtml(event.tool_name)})` : ''} at ${fmtTime(event.ts)}<br><span style="color:#8888aa;max-width:600px;display:inline-block">${escapeHtml(event.summary || '')}</span><br><br>` : ''}
                <span class="hint">← / → to step · Esc to deselect</span>
            </div>
        `;
        return;
    }

    if (selectedAgent === "ceo") {
        renderCeoDetail(content);
        return;
    }

    if (currentTab === "summary") renderSummary(content);
    else if (currentTab === "conversation") renderConversation(content);
    else if (currentTab === "events") renderEvents(content);
    else if (currentTab === "constitution") renderConstitution(content);
}

function renderCeoDetail(content) {
    const summary = currentRun.summaries.ceo || {};
    const allMsgs = summary.messages || [];
    const cutoff = currentRun.timeline[currentStep]?.ts || "9999";
    const msgs = allMsgs.filter(m => (m.ts || "") <= cutoff);

    let html = `<div class="kv" style="margin-bottom:12px">
        <div class="k">Role</div><div class="v">CEO (Sharma) — sole human operator</div>
        <div class="k">Interface</div><div class="v">Slack</div>
        <div class="k">Messages sent</div><div class="v">${msgs.length} of ${allMsgs.length}</div>
    </div>`;

    if (msgs.length === 0) {
        html += `<div style="color:#8888aa">No messages yet at this step.</div>`;
    } else {
        for (const m of msgs) {
            html += `<div class="ceo-msg">
                <div class="when">${fmtTime(m.ts)} · ${escapeHtml(m.trigger || '')}</div>
                <div>${escapeHtml(m.summary || '')}</div>
            </div>`;
        }
    }
    content.innerHTML = html;
}

function renderSummary(content) {
    const agent = currentRun.agents[selectedAgent];
    const s = currentRun.summaries[selectedAgent] || {};
    const cutoff = currentRun.timeline[currentStep]?.ts || "9999";

    // Compute "so far" stats from cached detail
    let sofar = { calls: 0, input: 0, output: 0, tools: {} };
    const detail = agentDetailCache[`${selectedAgent}__${currentDate}`];
    if (detail) {
        for (const e of detail.events) {
            if ((e.ts || "") > cutoff) continue;
            if (e.type === "api_response") {
                sofar.calls++;
                sofar.input += (e.usage || {}).input || 0;
                sofar.output += (e.usage || {}).output || 0;
            } else if (e.type === "tool_call") {
                sofar.tools[e.tool_name] = (sofar.tools[e.tool_name] || 0) + 1;
            }
        }
    }

    const toolsList = Object.entries(sofar.tools)
        .map(([n, c]) => `${TOOL_ICONS[n] || "⚙️"} ${n} ×${c}`)
        .join("<br>") || "none yet";

    content.innerHTML = `<div class="kv">
        <div class="k">Type</div><div class="v" style="color:${colorOf(agent.role)};font-weight:600">${escapeHtml(agent.agent_type || agent.name || agent.role)}</div>
        <div class="k">Agent ID</div><div class="v">${escapeHtml(agent.id)}</div>
        <div class="k">Role</div><div class="v">${escapeHtml(agent.role)}</div>
        <div class="k">Parent</div><div class="v">${escapeHtml(agent.parent || "none")}</div>
        <div class="k">Trigger</div><div class="v">${escapeHtml(s.trigger || "--")}</div>
        <div class="k">API calls (so far)</div><div class="v">${sofar.calls} of ${s.api_calls || s.turns || 0}</div>
        <div class="k">Tokens (so far)</div><div class="v">${fmtTokens(sofar.input)} in / ${fmtTokens(sofar.output)} out</div>
        <div class="k">Final cost</div><div class="v" style="color:#66bb6a">${fmtCost(s.total_cost)}</div>
        <div class="k">Tools used</div><div class="v">${toolsList}</div>
        <div class="k">Task</div><div class="v" style="font-size:11px;max-height:200px;overflow-y:auto;display:block">${escapeHtml(s.task || "--")}</div>
    </div>`;

    if (!detail) {
        loadAgentDetail(selectedAgent).then(() => {
            if (selectedAgent && currentTab === "summary") renderSummary(content);
        });
    }
}

async function renderConversation(content) {
    content.innerHTML = `<div class="welcome">Loading...</div>`;
    const detail = await loadAgentDetail(selectedAgent);
    if (!detail) {
        content.innerHTML = `<div class="welcome">No detail data.</div>`;
        return;
    }

    const cutoff = currentRun.timeline[currentStep]?.ts || "9999";
    const events = detail.events.filter(e => (e.ts || "") <= cutoff);

    // Group by turn
    const turns = {};
    for (const e of events) {
        if (e.type === "system_prompt" || e.type === "api_request_full" || e.type === "task_start" || e.type === "task_end") continue;
        const t = e.turn || 0;
        if (!turns[t]) turns[t] = [];
        turns[t].push(e);
    }

    let html = "";
    const turnKeys = Object.keys(turns).sort((a, b) => +a - +b);
    for (const tk of turnKeys) {
        if (+tk === 0) continue;
        const tEvents = turns[tk];
        const apiResp = tEvents.find(e => e.type === "api_response");
        const usage = apiResp?.usage || {};

        html += `<div class="turn">
            <div class="turn-head">
                <span><span class="num">Turn ${tk}</span> · ${fmtTime(tEvents[0]?.ts)}</span>
                <span>${fmtTokens(usage.input)} in / ${fmtTokens(usage.output)} out</span>
            </div>
            <div class="turn-body">`;
        for (const e of tEvents) {
            if (e.type === "api_response" && e.text) {
                const eid = registerEvent(e);
                const preview = e.text.length > 400 ? e.text.slice(0, 400) + "…" : e.text;
                html += `<div class="thinking" onclick="openEventById('${eid}')" title="Click for full response">${escapeHtml(preview)}</div>`;
            } else if (e.type === "tool_call") {
                const icon = TOOL_ICONS[e.tool_name] || "⚙️";
                const args = JSON.stringify(e.tool_input || {});
                const eid = registerEvent(e);
                html += `<div class="tool-call" onclick="openEventById('${eid}')" title="Click for full tool input">${icon} <span class="name">${escapeHtml(e.tool_name)}</span> <span class="args">${escapeHtml(args.slice(0, 250))}${args.length > 250 ? "…" : ""}</span></div>`;
            } else if (e.type === "tool_result") {
                const content_preview = (e.content || "").slice(0, 1000);
                const isLong = (e.content_length || 0) > 1000;
                const errCls = e.is_error ? " error" : "";
                const eid = registerEvent(e);
                html += `<div class="tool-result${errCls}" onclick="openEventById('${eid}')" title="Click for full content">
                    <div class="text">${escapeHtml(content_preview)}</div>
                    ${isLong ? `<div class="hint">${e.content_length.toLocaleString()} chars — click for full</div>` : ""}
                </div>`;
            } else if (e.type === "reflection") {
                const eid = registerEvent(e);
                const preview = (e.content || "").length > 400 ? e.content.slice(0, 400) + "…" : e.content;
                html += `<div class="thinking" style="border-left-color:#ffd700" onclick="openEventById('${eid}')" title="Click for full reflection">💭 ${escapeHtml(preview || "")}</div>`;
            }
        }
        html += `</div></div>`;
    }
    if (!html) html = `<div class="welcome">No turns yet at this step.</div>`;
    content.innerHTML = html;
}

async function renderEvents(content) {
    content.innerHTML = `<div class="welcome">Loading...</div>`;
    const detail = await loadAgentDetail(selectedAgent);
    if (!detail) {
        content.innerHTML = `<div class="welcome">No detail data.</div>`;
        return;
    }
    const cutoff = currentRun.timeline[currentStep]?.ts || "9999";
    const events = detail.events.filter(e => (e.ts || "") <= cutoff);

    let html = `<div style="margin-bottom:8px;color:#8888aa;font-size:11px">Showing ${events.length} of ${detail.events.length} events · click any row for full details</div>`;
    html += `<table class="events"><thead><tr><th>Time</th><th>Type</th><th>Turn</th><th>Detail</th></tr></thead><tbody>`;
    for (const e of events) {
        let detail_str = "";
        if (e.type === "tool_call") detail_str = `${e.tool_name}: ${JSON.stringify(e.tool_input || {}).slice(0, 80)}`;
        else if (e.type === "tool_result") {
            const status = e.is_error ? "⚠ ERROR: " : "✓ ";
            const toolPart = e.tool_name ? `${e.tool_name} → ` : "";
            detail_str = `${toolPart}${status}${(e.content || "").slice(0, 70)}`;
        }
        else if (e.type === "api_response") detail_str = `${e.stop_reason} · ${fmtTokens((e.usage || {}).output)} out`;
        else if (e.type === "task_start") detail_str = (e.task || "").slice(0, 80);
        else if (e.type === "task_end") detail_str = `${e.turns} turns`;
        else if (e.type === "reflection") detail_str = (e.content || "").slice(0, 80);
        else if (e.type === "system_prompt") detail_str = `Model: ${e.model || "?"}`;
        else if (e.type === "api_request_full") detail_str = `${e.message_count || 0} msgs · ${e.tool_count || 0} tools`;
        const eid = registerEvent(e);
        html += `<tr onclick="openEventById('${eid}')"><td>${fmtTime(e.ts)}</td><td>${escapeHtml(e.type)}</td><td>${e.turn != null ? e.turn : ""}</td><td>${escapeHtml(detail_str)}</td></tr>`;
    }
    html += `</tbody></table>`;
    content.innerHTML = html;
}

async function renderConstitution(content) {
    const detail = await loadAgentDetail(selectedAgent);
    if (!detail) {
        content.innerHTML = `<div class="welcome">No detail data.</div>`;
        return;
    }
    const sp = detail.events.find(e => e.type === "system_prompt");
    if (!sp) {
        content.innerHTML = `<div class="welcome">No constitution recorded.</div>`;
        return;
    }
    content.innerHTML = `
        <div style="font-size:11px;color:#8888aa;margin-bottom:8px">Model: ${escapeHtml(sp.model || "?")} · Dynamic: ${escapeHtml(sp.dynamic_state || "(none)")}</div>
        <div class="constitution">${escapeHtml(sp.constitution || "")}</div>
    `;
}

// === Event Detail Modal ===
function openEventModal(event) {
    const modal = document.getElementById("event-modal");
    const title = document.getElementById("modal-title");
    const body = document.getElementById("modal-body");

    let titleStr = event.type;
    if (event.tool_name) titleStr += ` — ${event.tool_name}`;
    if (event.turn != null) titleStr += ` · turn ${event.turn}`;
    titleStr += ` · ${fmtTime(event.ts)}`;
    title.textContent = titleStr;

    body.innerHTML = formatEventDetail(event);
    modal.classList.add("open");
}

function closeEventModal() {
    document.getElementById("event-modal").classList.remove("open");
}
window.openEventModal = openEventModal;
window.closeEventModal = closeEventModal;

function formatEventDetail(e) {
    switch (e.type) {
        case "system_prompt":
            return `
                <div class="kv">
                    <div class="k">Model</div><div class="v">${escapeHtml(e.model || "")}</div>
                    <div class="k">Dynamic state</div><div class="v">${escapeHtml(e.dynamic_state || "(none)")}</div>
                </div>
                <h3>Constitution</h3>
                <div class="code-block">${escapeHtml(e.constitution || "")}</div>
            `;
        case "task_start":
            return `
                <div class="kv">
                    <div class="k">Trigger</div><div class="v">${escapeHtml(e.trigger || "")}</div>
                </div>
                <h3>Task</h3>
                <div class="code-block">${escapeHtml(e.task || "")}</div>
            `;
        case "task_end":
            return `
                <div class="kv">
                    <div class="k">Turns used</div><div class="v">${e.turns || 0}</div>
                </div>
                <h3>Result preview</h3>
                <div class="code-block">${escapeHtml(e.result_preview || "")}</div>
            `;
        case "api_request_full":
            return formatApiRequest(e);
        case "api_response":
            return formatApiResponse(e);
        case "tool_call":
            return formatToolCall(e);
        case "tool_result":
            return formatToolResult(e);
        case "reflection":
            return `
                <h3>Reflection</h3>
                <div class="code-block">${escapeHtml(e.content || "")}</div>
            `;
        default:
            return `<div class="code-block">${escapeHtml(JSON.stringify(e, null, 2))}</div>`;
    }
}

function formatApiRequest(e) {
    let html = `<div class="kv">
        <div class="k">Model</div><div class="v">${escapeHtml(e.model || "")}</div>
        <div class="k">Max tokens</div><div class="v">${e.max_tokens || 0}</div>
        <div class="k">Messages</div><div class="v">${e.message_count || 0}</div>
        <div class="k">Tools available</div><div class="v">${e.tool_count || 0}</div>
    </div>`;

    // System blocks
    const sys = e.system || [];
    if (sys.length) {
        html += `<h3>System (${sys.length} block${sys.length > 1 ? "s" : ""})</h3>`;
        for (const s of sys) {
            const text = (s && typeof s === "object") ? (s.text || JSON.stringify(s)) : String(s);
            html += `<div class="code-block">${escapeHtml(text)}</div>`;
        }
    }

    // Messages
    const msgs = e.messages || [];
    if (msgs.length) {
        html += `<h3>Messages (${msgs.length})</h3>`;
        for (const m of msgs) {
            const role = m.role || "?";
            html += `<div class="message-card role-${escapeHtml(role)}">
                <div class="message-role">${escapeHtml(role)}</div>
                <div class="message-content">${formatMessageContent(m.content)}</div>
            </div>`;
        }
    }

    // Tools
    const tools = e.tools || [];
    if (tools.length) {
        html += `<h3>Tools available (${tools.length})</h3>`;
        html += `<div class="code-block">${tools.map(t => `• ${escapeHtml(t.name || "?")}${t.description ? " — " + escapeHtml(t.description) : ""}`).join("\n")}</div>`;
    }

    return html;
}

function formatMessageContent(content) {
    if (content == null) return `<pre>(empty)</pre>`;
    if (typeof content === "string") {
        return `<pre>${escapeHtml(content)}</pre>`;
    }
    if (Array.isArray(content)) {
        return content.map(block => {
            if (!block || typeof block !== "object") return `<pre>${escapeHtml(String(block))}</pre>`;
            if (block.type === "text") {
                return `<pre>${escapeHtml(block.text || "")}</pre>`;
            }
            if (block.type === "tool_use") {
                return `<div class="tool-block">
                    <div class="tool-name">🔧 ${escapeHtml(block.name || "")} <span style="color:#8888aa;font-weight:normal">(id: ${escapeHtml(block.id || "")})</span></div>
                    <pre>${escapeHtml(JSON.stringify(block.input || {}, null, 2))}</pre>
                </div>`;
            }
            if (block.type === "tool_result") {
                const txt = typeof block.content === "string" ? block.content : JSON.stringify(block.content);
                const errCls = block.is_error ? " error" : "";
                return `<div class="result-block${errCls}">
                    <div style="color:#8888aa;font-size:10px;margin-bottom:4px">↩ tool_result (id: ${escapeHtml(block.tool_use_id || "")})</div>
                    <pre>${escapeHtml(txt)}</pre>
                </div>`;
            }
            return `<pre>${escapeHtml(JSON.stringify(block, null, 2))}</pre>`;
        }).join("");
    }
    return `<pre>${escapeHtml(JSON.stringify(content, null, 2))}</pre>`;
}

function formatApiResponse(e) {
    const u = e.usage || {};
    let html = `<div class="kv">
        <div class="k">Model</div><div class="v">${escapeHtml(e.model || "")}</div>
        <div class="k">Stop reason</div><div class="v">${escapeHtml(e.stop_reason || "")}</div>
        <div class="k">Input tokens</div><div class="v">${fmtTokens(u.input)}</div>
        <div class="k">Output tokens</div><div class="v">${fmtTokens(u.output)}</div>
        <div class="k">Cache read</div><div class="v">${fmtTokens(u.cache_read)}</div>
        <div class="k">Cache write</div><div class="v">${fmtTokens(u.cache_creation)}</div>
    </div>`;

    if (e.text) {
        html += `<h3>Text response</h3><div class="code-block">${escapeHtml(e.text)}</div>`;
    }

    const blocks = e.raw_content_blocks || [];
    if (blocks.length) {
        html += `<h3>Content blocks (${blocks.length})</h3>`;
        for (const b of blocks) {
            if (b.type === "text") {
                html += `<div class="message-card role-assistant"><div class="message-role">text</div><pre>${escapeHtml(b.text || "")}</pre></div>`;
            } else if (b.type === "tool_use") {
                html += `<div class="tool-block">
                    <div class="tool-name">🔧 ${escapeHtml(b.name || "")} <span style="color:#8888aa;font-weight:normal">(id: ${escapeHtml(b.id || "")})</span></div>
                    <pre>${escapeHtml(JSON.stringify(b.input || {}, null, 2))}</pre>
                </div>`;
            }
        }
    }

    return html;
}

function formatToolCall(e) {
    return `<div class="kv">
        <div class="k">Tool name</div><div class="v" style="color:#4fc3f7">${escapeHtml(e.tool_name || "")}</div>
        <div class="k">Tool use ID</div><div class="v">${escapeHtml(e.tool_use_id || "")}</div>
    </div>
    <h3>Input</h3>
    <div class="code-block">${escapeHtml(JSON.stringify(e.tool_input || {}, null, 2))}</div>`;
}

function formatToolResult(e) {
    const errBadge = e.is_error
        ? `<span style="color:#ef5350">⚠ ERROR</span>`
        : `<span style="color:#66bb6a">✓ OK</span>`;

    // Originating call section (if we have the link)
    let callSection = "";
    if (e.tool_name) {
        const icon = TOOL_ICONS[e.tool_name] || "⚙️";
        const inputJson = JSON.stringify(e.tool_input || {}, null, 2);
        callSection = `
            <h3>In response to</h3>
            <div class="tool-block">
                <div class="tool-name">${icon} ${escapeHtml(e.tool_name)}${e.call_ts ? ` <span style="color:#8888aa;font-weight:normal">at ${fmtTime(e.call_ts)}</span>` : ""}</div>
                <pre>${escapeHtml(inputJson)}</pre>
            </div>
        `;
    }

    return `<div class="kv">
        <div class="k">Status</div><div class="v">${errBadge}</div>
        <div class="k">Tool</div><div class="v">${escapeHtml(e.tool_name || "(unknown)")}</div>
        <div class="k">Tool use ID</div><div class="v">${escapeHtml(e.tool_use_id || "")}</div>
        <div class="k">Content length</div><div class="v">${(e.content_length || 0).toLocaleString()} chars</div>
    </div>
    ${callSection}
    <h3>Result content</h3>
    <div class="code-block" style="max-height:600px">${escapeHtml(e.content || "")}</div>`;
}

// === Event handlers ===
document.querySelectorAll(".tab").forEach(t => {
    t.addEventListener("click", () => {
        currentTab = t.dataset.tab;
        renderDetail();
    });
});

document.getElementById("slider").addEventListener("input", (e) => {
    setStep(parseInt(e.target.value));
});

document.getElementById("btn-prev").addEventListener("click", () => setStep(currentStep - 1));
document.getElementById("btn-next").addEventListener("click", () => setStep(currentStep + 1));
document.getElementById("btn-first").addEventListener("click", () => setStep(0));
document.getElementById("btn-last").addEventListener("click", () => setStep(currentRun.timeline.length - 1));

document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "Escape") {
        const modal = document.getElementById("event-modal");
        if (modal.classList.contains("open")) {
            closeEventModal();
            return;
        }
        if (selectedAgent) {
            selectedAgent = null;
            render();
        }
        return;
    }
    if (e.key === "ArrowRight") { e.preventDefault(); setStep(currentStep + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); setStep(currentStep - 1); }
    else if (e.key === "Home") { e.preventDefault(); setStep(0); }
    else if (e.key === "End") { e.preventDefault(); setStep(currentRun.timeline.length - 1); }
});

// Close modal on background click and X button
document.getElementById("modal-close").addEventListener("click", closeEventModal);
document.getElementById("event-modal").addEventListener("click", (e) => {
    if (e.target.id === "event-modal") closeEventModal();
});

// === Init ===
loadData().catch(e => {
    console.error("Init failed:", e);
    showError("Init error: " + e.message);
});
