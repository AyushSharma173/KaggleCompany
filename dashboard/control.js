/**
 * Kaggle Company — Control Dashboard
 *
 * Four tabs:  Agents | Workflows | Files | Tools
 * Agent-centric default. Same dark theme as the monitoring dashboard.
 * Vanilla JS, no frameworks.
 */

/* ================================================================
   STATE
   ================================================================ */

let currentTab = "agents";          // agents | workflows | files | tools
let selectedAgent = null;           // agent id
let inspectorTab = "constitution";  // constitution | resources | tools | info
let selectedFile = null;            // {dir, filename, path}
let editorDirty = false;            // unsaved changes in an editor

// Cached data from API
let constitutions = [];   // [{name, filename, path, size}]
let skills = [];
let strategies = [];
let workflowConfig = {};  // {event_type: [{handler, enabled, description}]}
let toolManifest = [];    // [{name, description, allowed_roles}]
let agents = [];          // from state/agents/*.json
let fileContents = {};    // path -> content (cached)

const ROLES = ["vp", "worker", "subagent", "consolidation"];
const ROLE_COLORS = {
    ceo: "#ef5350",
    vp: "#4fc3f7",
    worker: "#66bb6a",
    subagent: "#ffb74d",
    consolidation: "#ce93d8",
};

const TABS = [
    { id: "agents",    label: "Agents" },
    { id: "workflows", label: "Workflows" },
    { id: "files",     label: "Files" },
    { id: "tools",     label: "Tools" },
];

/* ================================================================
   DATA FETCHING
   ================================================================ */

async function fetchAll() {
    const [cRes, sRes, stRes, wfRes, tmRes, agRes] = await Promise.all([
        fetch("/api/files?dir=constitutions").then(r => r.json()),
        fetch("/api/files?dir=skills").then(r => r.json()),
        fetch("/api/files?dir=strategies").then(r => r.json()),
        fetch("/api/workflow-config").then(r => r.json()),
        fetch("/api/tool-manifest").then(r => r.json()),
        fetch("/api/agents").then(r => r.json()),
    ]);
    constitutions = cRes.files || [];
    skills = sRes.files || [];
    strategies = stRes.files || [];
    workflowConfig = wfRes;
    toolManifest = tmRes;
    agents = agRes.agents || [];
    document.getElementById("ctrl-status").textContent =
        `${constitutions.length}C  ${skills.length}S  ${toolManifest.length}T`;
}

async function fetchFileContent(path) {
    if (fileContents[path]) return fileContents[path];
    const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (data.content !== undefined) {
        fileContents[path] = data.content;
    }
    return data.content || data.error || "";
}

async function saveFileContent(path, content) {
    const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`, {
        method: "POST",
        body: content,
    });
    const data = await res.json();
    if (data.saved) {
        fileContents[path] = content;
        editorDirty = false;
        showToast(`Saved ${path}`);
    } else {
        showToast(data.error || "Save failed", true);
    }
    return data;
}

async function createFile(path, content) {
    const res = await fetch(`/api/create-file?path=${encodeURIComponent(path)}`, {
        method: "POST",
        body: content,
    });
    return res.json();
}

async function saveWorkflowConfig(config) {
    const res = await fetch("/api/workflow-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
    });
    const data = await res.json();
    if (data.saved) {
        workflowConfig = config;
        showToast("Workflow config saved. Restart system for changes to take effect.");
    } else {
        showToast(data.error || "Save failed", true);
    }
}

/* ================================================================
   RENDER: MAIN
   ================================================================ */

function render() {
    renderTabs();
    renderContent();
}

function renderTabs() {
    const el = document.getElementById("ctrl-tabs");
    el.innerHTML = TABS.map(t =>
        `<button class="ctrl-tab${t.id === currentTab ? " active" : ""}"
                 onclick="switchTab('${t.id}')">${t.label}</button>`
    ).join("");
}

function renderContent() {
    const el = document.getElementById("ctrl-content");
    switch (currentTab) {
        case "agents":    return renderAgents(el);
        case "workflows": return renderWorkflows(el);
        case "files":     return renderFiles(el);
        case "tools":     return renderTools(el);
    }
}

function switchTab(tab) {
    if (editorDirty && !confirm("You have unsaved changes. Switch tab anyway?")) return;
    editorDirty = false;
    currentTab = tab;
    render();
}

/* ================================================================
   RENDER: AGENTS TAB
   ================================================================ */

function renderAgents(container) {
    // Build agent list: always show CEO + VP + known constitutions
    const agentEntries = buildAgentEntries();

    container.innerHTML = `<div class="agent-grid">
        <div class="agent-list">${agentEntries.map(a => agentItemHTML(a)).join("")}</div>
        <div class="agent-inspector" id="agent-inspector">
            ${selectedAgent ? "" : '<div class="empty-state">Select an agent to inspect its configuration.</div>'}
        </div>
    </div>`;

    if (selectedAgent) renderInspector();
}

function buildAgentEntries() {
    // Start with known agent types from constitutions
    const entries = [];

    // CEO (always)
    entries.push({ id: "ceo", name: "CEO (Sharma)", type: "ceo", role: "ceo", constitution: null });

    // Map constitutions to agent entries
    const constNames = constitutions.map(c => c.name);

    // VP
    if (constNames.includes("vp")) {
        entries.push({ id: "vp-001", name: "VP Agent", type: "vp", role: "vp", constitution: "constitutions/vp.md" });
    }

    // Workers (from constitutions that look like *-worker)
    for (const c of constitutions) {
        if (c.name.endsWith("-worker")) {
            entries.push({
                id: `type:${c.name}`, name: c.name, type: c.name,
                role: "worker", constitution: `constitutions/${c.filename}`,
            });
        }
    }

    // Subagents
    for (const c of constitutions) {
        if (c.name.endsWith("-subagent") || c.name === "subagent") {
            entries.push({
                id: `type:${c.name}`, name: c.name, type: c.name,
                role: "subagent", constitution: `constitutions/${c.filename}`,
            });
        }
    }

    // Consolidation
    if (constNames.includes("consolidation")) {
        entries.push({
            id: "type:consolidation", name: "Consolidation", type: "consolidation",
            role: "consolidation", constitution: "constitutions/consolidation.md",
        });
    }

    // Heartbeat
    if (constNames.includes("heartbeat")) {
        entries.push({
            id: "type:heartbeat", name: "Heartbeat", type: "heartbeat",
            role: "vp", constitution: "constitutions/heartbeat.md",
        });
    }

    return entries;
}

function agentItemHTML(a) {
    const sel = selectedAgent === a.id ? " selected" : "";
    const color = ROLE_COLORS[a.role] || ROLE_COLORS.worker;
    return `<div class="agent-item${sel}" onclick="selectAgent('${a.id}')">
        <div class="agent-dot" style="background:${color}"></div>
        <div class="agent-item-info">
            <div class="agent-item-name">${esc(a.name)}</div>
            <div class="agent-item-type">${esc(a.role)}</div>
        </div>
    </div>`;
}

function selectAgent(id) {
    if (editorDirty && !confirm("You have unsaved changes. Switch agent?")) return;
    editorDirty = false;
    selectedAgent = id;
    inspectorTab = "constitution";
    render();
}

async function renderInspector() {
    const panel = document.getElementById("agent-inspector");
    const entry = buildAgentEntries().find(a => a.id === selectedAgent);
    if (!entry) {
        panel.innerHTML = '<div class="empty-state">Agent not found.</div>';
        return;
    }

    const tabs = entry.id === "ceo"
        ? ["info"]
        : ["constitution", "resources", "tools", "info"];

    panel.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
            <div class="agent-dot" style="background:${ROLE_COLORS[entry.role] || "#888"};width:12px;height:12px"></div>
            <span class="section-title" style="margin:0">${esc(entry.name)}</span>
            <span class="card-badge badge-${entry.role}">${entry.role}</span>
        </div>
        <div class="inspector-tabs">
            ${tabs.map(t => `<button class="inspector-tab${t === inspectorTab ? " active" : ""}"
                onclick="switchInspectorTab('${t}')">${t.charAt(0).toUpperCase() + t.slice(1)}</button>`).join("")}
        </div>
        <div id="inspector-body"></div>
    `;

    await renderInspectorBody(entry);
}

function switchInspectorTab(tab) {
    if (editorDirty && !confirm("Unsaved changes. Switch tab?")) return;
    editorDirty = false;
    inspectorTab = tab;
    const entry = buildAgentEntries().find(a => a.id === selectedAgent);
    if (entry) renderInspectorBody(entry);
}

async function renderInspectorBody(entry) {
    const body = document.getElementById("inspector-body");
    if (!body) return;

    switch (inspectorTab) {
        case "constitution":
            if (!entry.constitution) {
                body.innerHTML = '<div class="empty-state">CEO has no constitution file. Steering happens via Slack.</div>';
                return;
            }
            const content = await fetchFileContent(entry.constitution);
            body.innerHTML = `
                <div class="editor-container">
                    <textarea class="editor-textarea" id="const-editor"
                        oninput="markDirty()">${esc(content)}</textarea>
                    <div class="editor-actions">
                        <button class="btn btn-primary" onclick="saveConstitution('${entry.constitution}')">Save</button>
                        <div class="unsaved-dot" id="unsaved-dot"></div>
                        <span class="hint">Edits take effect on the agent's next turn.</span>
                    </div>
                </div>`;
            break;

        case "resources":
            const skillItems = skills.length
                ? skills.map(s => `<div class="kv-row">
                    <span class="kv-label">${esc(s.name)}</span>
                    <span class="kv-value">${esc(s.filename)} (${(s.size/1024).toFixed(1)}K)</span>
                  </div>`).join("")
                : '<div style="color:#666;font-style:italic;padding:4px 0">No skills created yet.</div>';
            const stratItems = strategies.length
                ? strategies.map(s => `<div class="kv-row">
                    <span class="kv-label">${esc(s.name)}</span>
                    <span class="kv-value">${esc(s.filename)} (${(s.size/1024).toFixed(1)}K)</span>
                  </div>`).join("")
                : '<div style="color:#666;font-style:italic;padding:4px 0">No strategies yet — will populate from experiments.</div>';
            body.innerHTML = `
                <p class="hint" style="margin-bottom:12px">Resources are loaded on demand.
                Agents discover skills via <code>list_skills</code> and strategies via <code>list_strategies</code>.</p>
                <div style="margin-bottom:16px">
                    <div style="font-size:12px;font-weight:600;color:#8888aa;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">
                        Skills <span style="font-weight:400;text-transform:none;letter-spacing:0">(procedural guides)</span></div>
                    ${skillItems}
                </div>
                <div>
                    <div style="font-size:12px;font-weight:600;color:#8888aa;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">
                        Strategies <span style="font-weight:400;text-transform:none;letter-spacing:0">(institutional knowledge)</span></div>
                    ${stratItems}
                </div>`;
            break;

        case "tools":
            const role = entry.role;
            const agentTools = toolManifest.filter(t => t.allowed_roles.includes(role));
            if (!agentTools.length) {
                body.innerHTML = '<div class="empty-state">No tools available for this role.</div>';
                return;
            }
            body.innerHTML = agentTools.map(t => `<div class="kv-row">
                <span class="kv-label" style="width:180px;font-family:'SF Mono',monospace">${esc(t.name)}</span>
                <span class="kv-value" style="font-size:11px">${esc(t.description.slice(0, 120))}${t.description.length > 120 ? "..." : ""}</span>
            </div>`).join("");
            break;

        case "info":
            const info = [
                ["ID", entry.id],
                ["Type", entry.type],
                ["Role", entry.role],
                ["Constitution", entry.constitution || "—"],
            ];
            body.innerHTML = info.map(([k, v]) =>
                `<div class="kv-row"><span class="kv-label">${k}</span><span class="kv-value">${esc(v)}</span></div>`
            ).join("");
            break;
    }
}

function markDirty() {
    editorDirty = true;
    const dot = document.getElementById("unsaved-dot");
    if (dot) dot.classList.add("visible");
}

async function saveConstitution(path) {
    const textarea = document.getElementById("const-editor");
    if (!textarea) return;
    await saveFileContent(path, textarea.value);
    const dot = document.getElementById("unsaved-dot");
    if (dot) dot.classList.remove("visible");
}

/* ================================================================
   RENDER: WORKFLOWS TAB
   ================================================================ */

function renderWorkflows(container) {
    const events = Object.keys(workflowConfig);
    if (!events.length) {
        container.innerHTML = `
            <div class="section-title">Workflow Routing</div>
            <div class="section-subtitle">Event handler chains — controls who sees what when.</div>
            <div class="empty-state">No workflow events configured.<br>
            <span class="hint">Events are registered when the system starts. Start the system first.</span></div>`;
        return;
    }

    container.innerHTML = `
        <div class="section-title">Workflow Routing</div>
        <div class="section-subtitle">Event handler chains — controls who sees what when.
            Toggle handlers on/off. Changes require a system restart.</div>
        ${events.map(evt => renderEventCard(evt)).join("")}
        <div class="restart-note">
            Toggling a handler updates <code>state/workflow_config.json</code>.
            Restart the Kaggle Company system for changes to take effect.
        </div>`;
}

function renderEventCard(eventType) {
    const handlers = workflowConfig[eventType] || [];
    return `<div class="event-card">
        <div class="event-header">
            <span class="event-name">${esc(eventType)}</span>
            <span class="hint">${handlers.filter(h => h.enabled).length}/${handlers.length} active</span>
        </div>
        ${handlers.map((h, i) => renderHandlerRow(eventType, h, i)).join("")}
    </div>`;
}

function renderHandlerRow(eventType, handler, index) {
    const onClass = handler.enabled ? "on" : "";
    return `<div class="handler-row">
        <div class="handler-order">${index + 1}</div>
        <div class="handler-info">
            <div class="handler-name">${esc(handler.handler)}</div>
            <div class="handler-desc">${esc(handler.description || "")}</div>
        </div>
        <div class="toggle ${onClass}" onclick="toggleHandler('${eventType}', ${index})">
            <div class="toggle-knob"></div>
        </div>
    </div>`;
}

async function toggleHandler(eventType, index) {
    const handlers = workflowConfig[eventType];
    if (!handlers || !handlers[index]) return;
    handlers[index].enabled = !handlers[index].enabled;
    await saveWorkflowConfig(workflowConfig);
    renderContent();
}

/* ================================================================
   RENDER: FILES TAB
   ================================================================ */

function renderFiles(container) {
    const dirs = [
        { key: "constitutions", label: "Constitutions", files: constitutions },
        { key: "skills",        label: "Skills",        files: skills },
        { key: "strategies",    label: "Strategies",    files: strategies },
    ];

    container.innerHTML = `<div class="file-tree">
        <div class="file-list">
            ${dirs.map(d => `
                <div class="file-dir">
                    <span>${d.label}</span>
                    <button class="new-file-btn" onclick="promptNewFile('${d.key}')">+ New</button>
                </div>
                ${d.files.length
                    ? d.files.map(f => {
                        const sel = selectedFile && selectedFile.path === f.path ? " selected" : "";
                        return `<div class="file-entry${sel}" onclick="selectFile('${d.key}','${f.filename}','${f.path}')">
                            <span class="file-icon">${d.key === "constitutions" ? "\u{1F4DC}" : d.key === "skills" ? "\u{1F6E0}" : "\u{1F4D6}"}</span>
                            ${esc(f.filename)}
                        </div>`;
                    }).join("")
                    : `<div class="file-entry" style="color:#666;cursor:default;font-style:italic">
                        No files yet</div>`
                }
            `).join("")}
        </div>
        <div class="file-editor-pane" id="file-editor-pane">
            ${selectedFile
                ? '<div class="empty-state">Loading...</div>'
                : '<div class="empty-state">Select a file to view or edit.</div>'}
        </div>
    </div>`;

    if (selectedFile) loadFileEditor();
}

async function selectFile(dir, filename, path) {
    if (editorDirty && !confirm("Unsaved changes. Switch file?")) return;
    editorDirty = false;
    selectedFile = { dir, filename, path };
    render();
}

async function loadFileEditor() {
    const pane = document.getElementById("file-editor-pane");
    if (!pane || !selectedFile) return;

    const content = await fetchFileContent(selectedFile.path);
    pane.innerHTML = `
        <div class="file-editor-header">
            <span class="file-editor-path">${esc(selectedFile.path)}</span>
            <div class="editor-actions" style="margin:0">
                <button class="btn" onclick="renameCurrentFile()">Rename</button>
                <button class="btn btn-danger" onclick="deleteCurrentFile()">Delete</button>
                <button class="btn btn-primary" onclick="saveCurrentFile()">Save</button>
                <div class="unsaved-dot" id="file-unsaved-dot"></div>
            </div>
        </div>
        <textarea class="editor-textarea" id="file-editor" style="flex:1;min-height:calc(100vh - 160px)"
            oninput="markFileDirty()">${esc(content)}</textarea>`;
}

function markFileDirty() {
    editorDirty = true;
    const dot = document.getElementById("file-unsaved-dot");
    if (dot) dot.classList.add("visible");
}

async function saveCurrentFile() {
    if (!selectedFile) return;
    const textarea = document.getElementById("file-editor");
    if (!textarea) return;
    await saveFileContent(selectedFile.path, textarea.value);
    const dot = document.getElementById("file-unsaved-dot");
    if (dot) dot.classList.remove("visible");
}

async function renameCurrentFile() {
    if (!selectedFile) return;
    const newName = prompt("New filename (without .md):", selectedFile.filename.replace(".md", ""));
    if (!newName) return;
    const clean = newName.replace(/[^a-zA-Z0-9_-]/g, "").toLowerCase();
    if (!clean) { showToast("Invalid filename", true); return; }
    const newPath = `${selectedFile.dir}/${clean}.md`;
    if (newPath === selectedFile.path) return;
    const res = await fetch(`/api/rename-file?from=${encodeURIComponent(selectedFile.path)}&to=${encodeURIComponent(newPath)}`, {
        method: "POST",
    });
    const data = await res.json();
    if (data.renamed) {
        showToast(`Renamed to ${newPath}`);
        delete fileContents[selectedFile.path];
        selectedFile = { dir: selectedFile.dir, filename: `${clean}.md`, path: newPath };
        editorDirty = false;
        await fetchAll();
        render();
    } else {
        showToast(data.error || "Rename failed", true);
    }
}

async function deleteCurrentFile() {
    if (!selectedFile) return;
    if (!confirm(`Delete ${selectedFile.path}? This cannot be undone.`)) return;
    const res = await fetch(`/api/delete-file?path=${encodeURIComponent(selectedFile.path)}`, {
        method: "POST",
    });
    const data = await res.json();
    if (data.deleted) {
        showToast(`Deleted ${selectedFile.path}`);
        delete fileContents[selectedFile.path];
        selectedFile = null;
        editorDirty = false;
        await fetchAll();
        render();
    } else {
        showToast(data.error || "Delete failed", true);
    }
}

async function promptNewFile(dir) {
    const name = prompt(`New ${dir.slice(0, -1)} filename (without .md):`);
    if (!name) return;
    const clean = name.replace(/[^a-zA-Z0-9_-]/g, "").toLowerCase();
    if (!clean) { showToast("Invalid filename", true); return; }
    const path = `${dir}/${clean}.md`;
    const result = await createFile(path, `# ${clean}\n\n`);
    if (result.created) {
        showToast(`Created ${path}`);
        await fetchAll();
        selectedFile = { dir, filename: `${clean}.md`, path };
        editorDirty = false;
        fileContents[path] = `# ${clean}\n\n`;
        render();
    } else {
        showToast(result.error || "Create failed", true);
    }
}

/* ================================================================
   RENDER: TOOLS TAB
   ================================================================ */

function renderTools(container) {
    if (!toolManifest.length) {
        container.innerHTML = `
            <div class="section-title">Tool Permissions</div>
            <div class="section-subtitle">Which roles can access which tools.</div>
            <div class="empty-state">No tool manifest found.<br>
            <span class="hint">Start the system once to generate state/tool_manifest.json.</span></div>`;
        return;
    }

    const roleHeaders = ROLES.map(r =>
        `<th><span style="color:${ROLE_COLORS[r] || '#888'}">${r}</span></th>`
    ).join("");

    const rows = toolManifest.map(t => {
        const cells = ROLES.map(r => {
            const has = t.allowed_roles.includes(r);
            return `<td>${has
                ? '<span class="perm-check">\u2713</span>'
                : '<span class="perm-none">\u2014</span>'}</td>`;
        }).join("");
        return `<tr>
            <td title="${esc(t.description)}">${esc(t.name)}</td>
            ${cells}
        </tr>`;
    }).join("");

    container.innerHTML = `
        <div class="section-title">Tool Permissions</div>
        <div class="section-subtitle">Which roles can access which tools. Read-only — tool permissions are set in code.</div>
        <table class="tool-grid">
            <thead><tr><th>Tool</th>${roleHeaders}</tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

/* ================================================================
   UTILITIES
   ================================================================ */

function esc(s) {
    if (s == null) return "";
    const div = document.createElement("div");
    div.textContent = String(s);
    return div.innerHTML;
}

function showToast(message, isError = false) {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = "toast" + (isError ? " error" : "");
    setTimeout(() => { toast.className = "toast hidden"; }, 3000);
}

/* ================================================================
   KEYBOARD SHORTCUTS
   ================================================================ */

document.addEventListener("keydown", (e) => {
    // Cmd/Ctrl+S to save current editor
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (currentTab === "agents" && inspectorTab === "constitution") {
            const entry = buildAgentEntries().find(a => a.id === selectedAgent);
            if (entry && entry.constitution) saveConstitution(entry.constitution);
        } else if (currentTab === "files" && selectedFile) {
            saveCurrentFile();
        }
    }
});

/* ================================================================
   INIT
   ================================================================ */

(async function init() {
    await fetchAll();
    render();
})();
