# Alignment Guide — Steering Kaggle Company

This is the single comprehensive reference for understanding and operating
Kaggle Company. If you only read one document about how this system works,
read this one.

It covers: what the company is, why it's built the way it is, every lever
the CEO has to steer it, the workflow layer that controls coordination
between agents, the control dashboard design, and the decision framework
for choosing which lever to pull when.

---

## Part 1 — What Kaggle Company Is

### Mission

Kaggle Company is not a Kaggle bot. It is a **company staffed by AI agents
that makes money by competing autonomously on Kaggle**. The agents are
employees — a VP, workers, and temporary hires — operating under a CEO
(Sharma) who sets strategy through Slack. The CEO never writes code and
never micromanages execution. Agents decide what to work on, how to work
on it, and when to report.

### The Singular Success Metric

**Net profit.** Kaggle prize money earned, minus all compute and API costs.
Medals, rankings, and learning are side effects, not objectives. Every design
decision should be evaluated against the question: *"does this increase
expected profit?"*

### Kaggle Is the First Vertical, Not the Only One

The reusable infrastructure — agent runtime, communication, memory, workflow
layer, self-improvement — is an "agentic OS" intended to deploy to any
domain. Kaggle is the proving ground because its success metric is
quantitative, its environment is bounded, and its rewards are real money.
Once it works here, the same infrastructure should deploy to research
monitoring, real estate analysis, idea generation, or any other vertical.

Domain-specific parts (tools, strategies, success metrics, agent types)
are configuration. Standing up a new vertical should take days, not months.
This shapes every design decision: anything Kaggle-specific lives in
configuration files, not in the runtime.

### What This Is NOT

- **Not a framework.** No LangChain, CrewAI, AutoGen, or LlamaIndex. Built
  directly on the Anthropic SDK because the level of control required
  conflicts with framework abstractions.
- **Not multi-provider.** Anthropic Claude is the sole AI provider. No
  OpenAI, Gemini, local models, or "provider abstraction layer." The
  Anthropic SDK is the abstraction layer.
- **Not a reactive bot.** Agents are proactive employees. The VP runs
  continuously and decides what to scout, when to check on workers, when
  to report. Workers determine their own experiment cadence. No agent
  waits to be told what to do next.
- **Not single-agent.** The VP / worker / subagent hierarchy is structural,
  not optional. A worker that needs parallel research spawns subagents.
  The VP that needs multiple competitions creates multiple workers.

---

## Part 2 — The Architecture

### Six Components

The system is built from six well-separated components. Knowing what each
does and where it lives is the prerequisite for everything else:

| Component | Path | What it does |
|-----------|------|--------------|
| **Agent Runtime** | `src/runtime/` | The core loop every agent runs: load context → call Claude API → execute tool calls → repeat. VP, worker, and subagent are all instances of the same runtime with different config. |
| **Orchestrator** | `src/orchestrator/` | Deterministic lifecycle manager. Creates and terminates agents, routes messages, manages heartbeat scheduling. Pure Python control flow — no AI involved. |
| **Memory System** | `src/memory/` | Three-layer: structured state JSON (always loaded), markdown knowledge files (loaded on demand), JSONL transcripts (searched, never bulk-loaded). Treats context as scarce. |
| **Tools** | `src/tools/` | Async Python functions mapped to Claude's tool-use format. Grouped by category: kaggle, execution, research, communication, agent management, GPU. Role-locked. |
| **Slack Bot** | `src/comms/` | Slack Bolt SDK over Socket Mode. The CEO's *only* interface to the system. Channels are structured by purpose. |
| **Budget Controller** | `src/budget/` | Deterministic spend enforcement. Runs before any API call or GPU provisioning. Hard code that blocks overspend — not a suggestion in a prompt. |

### The Agent Hierarchy

```
CEO (Sharma, via Slack)
  └── VP Agent (always-on; scouts competitions, manages portfolio,
       │         creates and kills workers, reports up)
       │
       ├── Worker Agent (one per competition; runs experiments,
       │   │             spawns subagents, synthesizes findings)
       │   │
       │   └── Subagent (ephemeral; single task, auto-terminates
       │                  when done)
       │
       └── Consolidation Agent (periodic; reads experiment evidence
                                and updates strategy library)
```

The hierarchy is intentionally shallow. Three levels of agents (VP, worker,
subagent) plus one orthogonal periodic role (consolidation). Going deeper
would multiply coordination cost without adding capability.

### Foundational Invariants

These are load-bearing architectural decisions. Violating any of them
produces code that is *architecturally wrong* even if it compiles and
runs. Do not propose alternatives.

1. **Anthropic Claude is the sole AI provider.** Every AI call goes
   through the Anthropic API. No multi-provider abstraction layer.

2. **No frameworks.** Built from scratch on the Anthropic SDK.

3. **Slack is the only human interface.** No web dashboards for the CEO,
   no CLI monitoring tools, no admin panels for *human* use. (The
   monitoring dashboard and the planned control dashboard are *internal*
   tools the CEO uses to inspect and edit configuration — they are not
   the CEO's day-to-day interface to running operations. Day-to-day is
   Slack.)

4. **Budget enforcement is deterministic code, not prompts.** Agents
   cannot modify, bypass, or negotiate with the budget controller. An
   agent hitting its budget limit must request more from the VP or CEO —
   the controller never says "well, this seems important, I'll allow it."

5. **Agents are proactive employees, not reactive tools.** The VP runs
   continuously via heartbeat. Workers decide their own cadence. No agent
   waits to be told what to do next.

6. **Multi-agent from day one.** The hierarchy is structural. Never
   collapse to single-agent for "simplicity."

7. **Agents cannot modify their own governance.** No agent can edit
   constitutions, the orchestrator, or the budget controller. Strategy
   files are writable only by the consolidation agent. Experiment logs
   are append-only. Workspaces are isolated.

### Tech Stack

- **Language:** Python 3.11+ (async throughout, type hints)
- **AI:** Anthropic API (`anthropic` package, direct SDK)
- **Messaging:** Slack Bolt SDK, Socket Mode
- **Deployment:** Docker + docker-compose on VPS
- **GPU:** Kaggle free GPUs (preferred) + RunPod pay-per-minute (overflow)
- **Data:** Kaggle API (`kaggle` package)
- **State:** JSON files (structured state), markdown files (constitutions,
  skills, strategies), JSONL (transcripts, experiments)
- **No database.** File-based state is intentional — simple, inspectable,
  version-controllable.
- **No web framework.** No Flask, no FastAPI. The system has no HTTP
  endpoints except Slack's socket connection.

---

## Part 3 — The Alignment Layers (Map)

The CEO steers the company through six layers, each answering a different
question. Understanding which layer answers which question is the most
important mental model in the system.

| Layer | Question it answers | Where it lives | When to edit |
|-------|---------------------|----------------|--------------|
| **Constitutions** | WHO each agent IS | `constitutions/*.md` | Identity, authority, hard constraints |
| **Skills** | HOW to perform a procedure | `skills/*.md` *(planned)* | When agents repeatedly do a multi-step process poorly |
| **Strategies** | WHAT we have learned | `strategies/*.md` *(planned)* | When the consolidation agent extracts a pattern from experiments |
| **Tools** | WHAT IS POSSIBLE for an agent | `src/tools/*.py` | Adding or removing capabilities |
| **Workflows** | WHO sees what WHEN, what gates what | `workflows/*.yaml` *(planned)* | Coordination, routing, approvals, lifecycle |
| **CEO Slack messages** | WHAT to do *right now* | Slack | Day-to-day steering; no file edits |

The first four layers describe the **agent**: identity, procedures,
knowledge, capabilities. These are the *brain*. The fifth layer
(workflows) describes the **rails around the agent**: who triggers it,
what its outputs route to, what it's allowed to do, what gates exist.
This is the *nervous system and bureaucracy*. Slack messages are the
real-time conversational steering layer on top of all of it.

The brain vs nervous-system distinction is the most important conceptual
move in this whole document. Hold it tightly. The workflow layer never
tells an agent *what to think* — only what fires it and what catches its
outputs. If you find yourself wanting to put content-aware reasoning into
a workflow rule, that decision belongs in a constitution or skill, not in
workflows.

---

## Part 4 — Layer 1: Constitutions (WHO an Agent Is)

**Directory:** `constitutions/`
**Loaded:** Every API call (cached in the system prompt for cost efficiency)
**Written by:** CEO + Claude Code, manually
**Changes:** Rarely — when agent behavior needs structural correction

### What a Constitution Contains

A constitution defines an agent's identity, authority, hard constraints,
decision framework, and high-level workflow. Because it is loaded on every
API call, it shapes every decision the agent makes. It is the most
powerful alignment lever per token of guidance.

**A good constitution answers, in order:**
1. **Who are you?** (role identity)
2. **What is your objective?** (the metric you optimize)
3. **What decisions can you make alone, and what must you escalate?**
   (authority matrix)
4. **What must you never do?** (hard constraints)
5. **How do you communicate and report?**

### What a Constitution Should NOT Contain

Constitutions should describe **WHO you are** and **WHEN to do things**,
not **HOW to do them**. The HOW belongs in skills (for repeatable
procedures) or in the agent's own judgment (for everything else).

- **Good:** "When a CEO message arrives, finish your current task before
  responding."
- **Bad:** "When a CEO message arrives, first check message length, then
  parse for competition names, then look up each competition…"

The first run of the system proved this empirically. The research subagent
constitution doesn't say "search for X then Y then Z." It says "you are an
autonomous researcher with these tools, investigate your assigned question
thoroughly." Subagents produced thorough, structured reports without
step-by-step procedural guidance. **Default to vague.** Let the agent use
judgment. Add specificity only when judgment fails repeatedly.

### Current Constitutions

| File | Role | Status |
|------|------|--------|
| `vp.md` | The VP — the most critical file in the system | Active |
| `research-worker.md` | Research coordinator; spawns subagents, synthesizes | Active |
| `research-subagent.md` | Autonomous researcher | Active |
| `consolidation.md` | Knowledge curator | Not yet active |
| `heartbeat.md` | Lightweight periodic check logic | Not yet active |

### Typed Agents — Always Separate Constitutions

Never share a constitution between agent types. If you need a new kind of
worker (e.g., an `engineering-worker` that runs experiments instead of
researching), create a new constitution file for it. Sharing constitutions
across types removes the fine-grained control that makes constitutions
useful in the first place.

The architecture separates **role** (an enum: VP, WORKER, SUBAGENT,
CONSOLIDATION) from **type** (a string: `research-worker`,
`engineering-worker`, etc.). Role controls tool-access permissions; type
controls which constitution file is loaded.

### When to Edit a Constitution

- The agent is making bad strategic decisions → tighten the decision
  framework or add a constraint
- The agent is doing something it shouldn't → add an explicit "Never…"
- The agent isn't doing something it should → add it to the workflow
  section
- The agent communicates poorly → adjust the reporting rules

### When to Create a New Constitution

When you need a fundamentally different *kind* of agent — not just
different instructions for an existing kind. Different identity, different
authority, different constraints. New constitution.

---

## Part 5 — Layer 2: Skills (HOW to Perform a Procedure)

**Directory:** `skills/` *(currently empty; infrastructure planned)*
**Loaded:** On demand, via a `load_skill(name)` tool the agent calls when needed
**Written by:** CEO + Claude Code
**Changes:** When a procedure needs to evolve

### Why Skills Exist as a Separate Layer

A constitution should describe WHO an agent is, not be a procedural manual
for every task. When a procedure is rich enough to need its own document
(more than a few sentences), embedding it in the constitution has three
costs:

1. **It bloats every API call.** The constitution is cached and loaded on
   every turn. A 3-page procedure that's only relevant for one type of
   task should not be in every prompt.
2. **It tangles identity with procedure.** A constitution that's 60%
   procedural details is hard to read, hard to edit, and hard to reason
   about as a description of the agent's role.
3. **It's not composable.** If two different agents (e.g., the VP and the
   research worker) both need the same procedure, they each need a copy
   of it in their constitution. The two copies will drift.

Skills solve all three. They are loaded on demand, they keep procedures
out of identity files, and any agent can load any skill.

### How Skills Get Loaded

A `load_skill(skill_name)` tool. The agent calls it when it needs a
procedure; the skill content comes back as a tool result and lives in
that agent's context for the rest of the task. Lazy, honest about cost,
symmetric across roles. The cost is one extra turn before the procedural
work begins, which is well worth the cleanup.

### What Belongs in a Skill

A skill is a rich, procedural document for a multi-step workflow that:
- Multiple agents may need to perform
- Has a definable output structure (not just "do good work")
- Has flavor variations worth documenting (e.g., initial scout vs.
  in-progress review vs. post-mortem)
- Is too long for a constitution to absorb without bloating

### Example: The Deep-Dive Skill (Planned)

The first skill the system will need is `skills/deep-dive.md`. It will
contain:

- **Purpose** — what a deep dive is for (reducing uncertainty enough to
  commit real money and weeks of agent-time to a competition)
- **What "alpha" means concretely** — unexplored techniques, gaps in
  public notebooks, frontier research applicability (not just "be
  comprehensive")
- **Fit assessment criteria for our specific system** — can experiments
  be automated end-to-end? What is the iteration cycle time? Are free
  Kaggle GPUs sufficient or do we need RunPod? How parallelizable is the
  experiment space?
- **What makes a good intelligence report** — required sections, not
  "comprehensive": competition mechanics, data quirks, public solution
  landscape, research frontier, identified alpha, fit verdict, resource
  estimate
- **Flavors as sections** — initial scout, active strategy review,
  post-mortem
- **Commissioning guidance for VPs** — how to write the worker task prompt
- **Evaluation guidance for VPs** — how to read a deep-dive report and
  decide whether to push back

Both the VP (commissioning, evaluating) and the research worker
(executing) load the same skill file. Each reads the sections relevant to
its role. Single source of truth.

### When to Add a Skill

The bar for adding a skill is high enough to prevent premature
abstraction. Add a skill when:

- An agent has been observed doing a multi-step procedure poorly more
  than once, AND constitution edits did not fix it
- A procedure is repeatable, structured, and has a definable output
- Multiple agents need the same procedure (composability matters)

### When NOT to Add a Skill

- Preemptively, "just in case" — wait for evidence
- For one-off procedures — describe them in the task prompt instead
- For things the agent already does well from the constitution alone

---

## Part 6 — Layer 3: Strategies (WHAT We Have Learned)

**Directory:** `strategies/` *(currently empty)*
**Loaded:** On demand, via the `get_strategy` tool
**Written by:** Consolidation agent (from experimental evidence), or CEO
**Changes:** Regularly — as the system learns

Strategies are the company's knowledge base — the institutional memory.
They contain things agents cannot figure out on their own:

- Lessons learned from past experiments
- Domain knowledge that isn't on the public internet
- Internal performance data on specific techniques
- Patterns the consolidation agent has extracted across competitions

Strategies are different from skills in a critical way: **skills are how
to do things; strategies are what we know works.** A skill says "here is
the structure of a deep-dive report." A strategy says "for tabular
competitions with <100K rows, gradient boosting consistently beats neural
methods in our experiments — see experiments 47, 51, 63."

### Not Yet Used

The first run of the system did extensive deep research using public
internet sources without needing any pre-written strategy files. Strategies
become valuable only once we have **experimental evidence** worth
preserving — i.e., once workers start running real experiments and we
accumulate internal performance data.

### When to Create a Strategy

- The consolidation agent identifies a pattern across multiple experiments
- The CEO has domain knowledge that agents keep getting wrong despite
  research
- A technique works repeatedly and we want to ensure it's reused

### When NOT to Create a Strategy

- For general ML knowledge (agents can research this themselves)
- For one-off observations (Slack, not a permanent file)
- Preemptively (only when there's experimental evidence)

### The Living Document Principle

Strategy files are *living documents*, not static config. They should
cite which experiments support each technique. The consolidation agent
updates them based on new evidence. They evolve as the company learns.
Treating them as static config (set once, forget) defeats their purpose.

---

## Part 7 — Layer 4: Tools (WHAT IS POSSIBLE)

**Directory:** `src/tools/`
**Loaded:** Embedded in the API tool-use schema sent with every Claude call
**Written by:** Claude Code
**Changes:** Adding or removing capabilities (a hard capability change)

Tools define what an agent can *physically do*. If a tool doesn't exist,
the agent cannot do that thing — full stop. Tools are code, not prompts;
they are the absolute boundary of agent capability.

### Role-Locked

Tools are filtered by agent role at the orchestrator level via
`ToolRegistry.get_tools_for_role(role)`. The VP can call
`create_worker_agent`; subagents cannot. This is enforced in code, not
suggested in prompts. An agent literally does not see tools its role is
not allowed to use.

### Current Tool Categories

| Category | Examples | Available to |
|----------|----------|--------------|
| **Agent management** | `create_worker_agent`, `spawn_subagents`, `terminate_agent` | VP (workers), workers (subagents) |
| **Communication** | `send_slack_message`, `save_report` | VP, workers |
| **Research** | `web_search`, `web_fetch`, `deep_research` | All agents |
| **Memory** | `get_strategy`, `list_strategies` | All agents (read); consolidation only (write) |
| **Kaggle** | (planned) `download_dataset`, `submit_prediction` | Workers (planned) |
| **GPU** | (planned) `provision_runpod`, `release_gpu` | Workers (planned, gated) |

### The Workflow-Layer Implication

When you remove a tool from an agent's available set, you have made a
**workflow decision** in the most absolute possible way. Removing
`send_slack_message` from subagents means subagents can never directly
talk to Slack — their findings have to flow back through their parent
worker. That is a routing decision baked into the capability layer.

Going forward, the workflow layer (Part 8) will let you make many of
these decisions *without* removing tools, by using gates instead. But
hard removal is always available as the absolute version.

---

## Part 8 — Layer 5: Workflows (the Nervous System)

This is the layer the system does not yet have, and the rest of this
document explains in detail what it is, why it's needed, what it
controls, and how it will be built.

### The Problem It Solves

Today, **decisions about who sees what when, what gates what, and what
triggers what are scattered across hardcoded tool implementations and
the orchestrator**. For example:

- `save_report` writes a file to disk *and* uploads it to Slack *and*
  notifies nobody else internally — all baked into one function.
- The VP only learns a worker has finished a report by checking Slack
  manually or by hitting heartbeat.
- "When a worker completes, who gets notified" is not a question the
  system can answer because there is no notification system at all —
  the parent only learns by blocking on the child's return value.
- Authority limits ("VP can spawn workers up to $50 without asking") are
  written in the constitution as text, which means they are *suggestions
  Claude tries to follow*, not enforced rules.

These are coordination decisions, and they live in the wrong place. They
should be **toggleable from outside the runtime**, not edits to tool
source code or constitution prose.

### The Brain vs. Nervous System Distinction

The crucial distinction, again, because it determines what belongs in
this layer and what does not:

- **Inside the agent** (constitution + skill + strategy + prompt + tools
  + Claude inference): the agent's *thinking*. What to research, how to
  structure a report, when to spawn a subagent, which technique to try,
  what to write in a Slack message. These are judgments — soft, fuzzy,
  contextual, made by Claude.

- **The workflow layer** (around the agent): the *rails*. What triggers
  the agent to wake up, what its outputs route to, what it must get
  approval for, when it gets created and destroyed. These are policies —
  hard, factual, enforced by code.

The workflow layer **never tells an agent what to think**. It only
controls what fires the agent and what catches the agent's outputs.

If you find yourself wanting to put content-aware reasoning ("if the
report says X, then Y") into a workflow rule, you are in the wrong layer.
That decision belongs in a constitution or skill. Workflow rules check
facts (event type, threshold, role, authorization) — not content.

### The Five Dimensions of the Workflow Layer

Every workflow rule in this system maps to one of exactly five things.
This is the complete taxonomy:

#### 1. Routing — "When X happens, where does the information go?"

Pure pub/sub. The atom is `event → handler chain`.

Examples:
- `report.saved` → notify VP, then post file to CEO channel
- `subagent.completed` → return result to spawning worker (default)
- `budget.threshold_crossed` → alert CEO in `#alerts`
- `worker.terminated` → log to `#decisions`

This is the most familiar dimension and the one V1 will build first.

#### 2. Gating — "Before action Y happens, who must approve?"

Pre-execution interceptors on tool calls. The atom is
`tool_call → policy_check → (allow | deny | require_approval)`.

Examples:
- `create_worker_agent` with `budget_usd > 100` → require CEO approval
- `provision_runpod` → require VP approval, always
- `send_slack_message` to `#decisions` → no gate (default)
- `terminate_agent` → no gate for VP, blocked for workers

Gating is where the *"deterministic enforcement over trust"* invariant
gets generalized. The budget controller is already a gate. Gating
extends that pattern from "just budget" to every coordination decision
that needs hard enforcement.

#### 3. Triggers — "What wakes an agent up?"

The complement to routing. Routing says "when X, deliver to Y." Triggers
say "when X, wake up Y." Same machinery, different framing in the
dashboard.

The atom is `(event | schedule) → spawn_task(agent, templated_description)`.

Examples:
- VP wakes on: heartbeat every 6h, OR `ceo.message_received`, OR
  `report.saved`
- Consolidation agent wakes on: `experiment.completed` count ≥ 3, OR weekly
- Worker wakes on: heartbeat every 24h (current behavior)

#### 4. Authority — "What can each agent decide on its own?"

This is currently encoded in constitutions as prose ("you may spawn
workers up to $50 without asking"). Today that is a *suggestion* — Claude
tries to comply. The workflow layer is where it becomes **actually
enforced** by gating rules.

Authority is **not a separate engine** — it is a *view* over gating rules
grouped by agent. The dashboard needs an authority tab because "what can
this worker actually do" is the question the CEO will ask most often, and
it should be answerable without manually cross-referencing the gating
table.

Examples (rendered as a view):
- VP authority: spawn workers up to $50 budget, kill any worker, send
  any Slack message, provision GPU up to $20
- Worker authority: spawn subagents up to $5 each, save reports without
  approval, no GPU
- Subagent authority: research only — no spawning, no saving, no Slack

#### 5. Lifecycle — "When are agents created and destroyed?"

Currently hardcoded in the orchestrator. The atom is
`condition → (create | terminate | pause | resume) agent`.

Examples:
- Research worker: created on `vp.deep_dive_requested`, terminated after
  `report.saved`
- Subagent: created on `worker.spawn_request`, terminated after
  `task.complete` (current default)
- Worker pauses on `ceo.urgent_message`, resumes after CEO answer (a
  future toggle, not current behavior)

---

That's it — five dimensions: **routing, gating, triggers, authority (a
view of gating), and lifecycle**. If a coordination decision doesn't fit
into one of these, it is not a workflow decision; it is an agent-level
decision that belongs in a constitution or skill.

### Constitutions Describe, Workflows Enforce

This is the conceptual move that makes the whole system coherent.

A constitution can say: *"You have authority to spawn workers up to $50
without CEO approval."* That sentence exists for the agent to **read and
reason about**. But the actual gate that prevents a $200 spawn lives in
the workflow layer. The two should be **consistent**, but the workflow
layer is the **ground truth** for what is allowed.

Why this matters: today the constitution is the only source of
"authority" rules, and enforcement is just hoping Claude follows the
prose. That's the budget-controller anti-pattern that the foundational
invariants explicitly call out (invariant #4). The workflow layer
extends the budget-controller fix to every coordination decision in the
system.

In V2 you may even generate constitution prose from workflow config
automatically, so they cannot drift. But the contract is: **workflow
config is the source of truth; constitution prose is the description
the agent reads.**

### What Belongs in Workflow vs. What Belongs in an Agent

The line: **if a decision requires reading and reasoning about content,
it belongs in an agent. If a decision can be made by checking a fact
(event type, threshold, role, authorization), it belongs in workflows.**

Examples on the line:

| Decision | Layer | Why |
|----------|-------|-----|
| "When the report is saved, route to VP" | Workflow | No content reasoning |
| "When the report says the competition isn't worth pursuing, kill the worker" | Agent | Requires understanding the report |
| "When budget exceeds $100, require approval" | Workflow | Threshold check |
| "When the experiments are exploring an unproductive area, pivot strategy" | Agent | Judgment |
| "When a CEO message arrives, queue or interrupt?" | Workflow | Policy choice |
| "Should this CEO message change our strategy?" | Agent | Reading and deciding |

Smell test: if a workflow rule needs an `if/else` over content, it is in
the wrong layer.

### How Event Delivery to an Agent Actually Works

A subtlety worth being explicit about: agents do not have an "inbox" in
their loop. The agent runtime is a strict request-response loop that runs
Claude turns until the current task ends. There is no moment where the
agent says "let me check if anyone messaged me."

Therefore, **delivering an event to an agent means spawning a new task on
that agent**. The handler `spawn_task` calls
`manager.run_agent_task(agent_id, task_description)`, which is the
existing mechanism. The event payload is templated into the task
description so the agent has the context it was triggered with.

This has a nice property: it reuses existing trigger and heartbeat
machinery. The VP doesn't need a new "inbox loop" — it just gets
triggered by a task with the report context, runs through its agent loop
normally, and when it's done, it's done.

### Phasing — What to Build When

The mistake would be to build all five dimensions at once. The honest
phasing:

**V1 — Build now, alongside the deep-dive skill.**
- Event bus (~30 lines, in `src/orchestrator/events.py`)
- Routing only — handlers registered as plain Python functions in
  `main.py` or `src/workflows/handlers.py`. **No YAML config files yet.**
- One event: `report.saved`
- One actual toggle the dashboard exposes: "VP reviews reports before CEO" on/off

This gives the system the actual lived experience of a workflow toggle.
You will learn what's painful and what's missing from one real toggle
faster than from a designed-in-the-abstract framework.

**V2 — Build when you have 3+ events worth routing.**
- Move handlers from Python into `workflows/*.yaml` files (declarative)
- Add the Triggers dimension — extract heartbeat schedules into config
- Build the Event Log / Dry Run dashboard view (essential as soon as
  rule chains have multiple steps)

**V3 — Build when authority becomes a real question (likely once
multiple workers are running real experiments with real spend).**
- Add the Gating dimension — generalize the budget controller's pattern
  into a policy engine
- Add the Authority view (derived from gating rules)
- This is the layer that makes the system *actually safe* for autonomous
  spending

**V4 — Build when pause/resume becomes a real need.**
- Add the Lifecycle dimension
- Probably needed when multiple competitions run concurrently and the
  CEO wants to pause one without killing it

The reason for this phasing: dimensions 1–3 are pure additions (no
existing behavior changes). Dimensions 4–5 require touching how budget
and lifecycle work today, which is more invasive and only worth doing
when there is clear value.

### YAML vs. Python Handlers — Where to Hold the Line

YAML configs become a half-baked programming language the moment you
need conditionals, loops, or non-trivial templating. The defense:

- **YAML decides who and when.** Event types, conditions (simple
  expressions only), handler names, priority order.
- **Python decides how.** A handler is a Python function in
  `src/workflows/handlers.py` that the YAML *names*. Real logic lives in
  Python, where it can be tested.

If a workflow rule needs more than a simple `when` expression and a
named handler, the rule is in the wrong layer or the handler should be
broken into smaller pieces.

### Anti-Patterns for the Workflow Layer

- **Don't put content-aware reasoning in a workflow rule.** That's an
  agent's job. Workflow rules check facts.
- **Don't bypass the workflow layer "just this once" by hardcoding a
  notification in a tool.** Once you do that twice, the layer is dead
  and you're back to scattered hardcoded routing.
- **Don't let workflow rules be edited by agents.** Workflows are
  governance. Invariant #7 (agents cannot modify their own governance)
  applies in full force.
- **Don't proliferate event types preemptively.** Only emit events you
  actually have a handler for. Speculative events are dead weight.
- **Don't build YAML before you have 3+ working Python handlers.** YAML
  is for repeated patterns, not one-offs.

---

## Part 9 — The Control Dashboard

The CEO already has a **monitoring dashboard** that shows live events,
the org chart, spawned agents, and a timeline. This part of the document
describes the **control dashboard** — the interface for *editing* the
company's DNA.

### The Core Reframe: Debugger + Editor

The control dashboard is not just an editor. It is a **debugger AND an
editor**. The CEO arrives from monitoring with a symptom — "why did the
system do that?" — and needs to trace the symptom to its cause, then
change the cause. The monitoring and control dashboards are two views of
the same system, deeply linked. Monitoring shows what happened. Control
shows why it happened and lets you change the rules so it happens
differently next time.

The principle remains: **everything that can be edited about the company
should be editable from one place, in one mental model, without needing
to find files in the filesystem or make git commits manually.** The
dashboard is a thin UI layer over the same files (`constitutions/`,
`skills/`, `strategies/`, `tools/`) that Claude Code edits. It does not
create a new source of truth.

### Information Architecture: Three Views of the Same Data

The dashboard does not use a flat tab bar of file types. Instead it
provides three views that each answer a different question:

1. **Agent view (default landing).** Org chart on top, agent inspector
   below. Click an agent to see its constitution, skills, tools,
   authority, and triggers. This is the most natural entry point when the
   question is *"I want to change how the VP works."*

2. **Workflow / Event view.** An event catalog showing registered events,
   handler chains, and toggles. This is the natural entry point when the
   question is *"why did the VP wake up?"*

3. **File view.** A tree of `constitutions/`, `skills/`, `strategies/`.
   Power-user fallback for direct editing when you already know which
   file you want to touch.

All three views are windows onto the same underlying files. Editing a
constitution in the Agent view and editing it in the File view produce
the same result — they write to the same file on disk.

### Tech Decisions

- **Vanilla JavaScript.** No React, Vue, or frontend frameworks. Matches
  the monitoring dashboard exactly.
- **Same visual identity.** Same dark theme, same color palette, same
  component patterns as the monitoring dashboard.
- **Same server.** Served from `serve.py` on the same port as the
  monitoring dashboard. The two dashboards are two pages of the same
  application.
- **File editing via REST API.** New endpoints added to `serve.py` for
  reading, writing, and listing files in the editable directories.
- **Workflow config** stored in `state/workflow_config.json`, editable
  from the dashboard.
- **Tool manifest** generated at startup and stored in
  `state/tool_manifest.json`.

### View 1 — Agents

The org chart is **shared with monitoring** — same SVG layout, different
click behavior. In monitoring, clicking an agent shows its live status
and recent events. In control, clicking an agent opens the **agent
inspector**.

The agent inspector has tabs:

- **Constitution** — Full text editor. Edit the constitution, save, and
  the next agent turn picks up the new version. No restart required.
- **Skills** — List of loadable skill files for this agent type. Click
  to view or edit.
- **Tools** — Which tools are available for this role. Read-only in V1
  (changing tool permissions requires code changes).
- **Triggers** — What wakes this agent: heartbeat schedule, event
  subscriptions, message routing rules.

### View 2 — Workflows / Events

Each registered event is rendered as a **card**. A card shows:

- The event name and description.
- The **handler chain** as rows in execution order. Each row displays
  the handler name and a description of what it does.
- A **toggle on each row** to enable or disable that handler without
  touching code. Disabling a handler skips it in the chain — the event
  still fires, the handler just does not run.

There is no YAML in the UI. Forms compose named Python handlers into
chains. The CEO never writes handler code from the dashboard — they
arrange, toggle, and reorder handlers that already exist in the
codebase.

V2 will add a live counter on each card showing how many times the event
has fired, providing a pulse indicator that connects back to monitoring.

### View 3 — Files

A tree of `constitutions/`, `skills/`, `strategies/`. Click a file to
open it in an editor. Save writes to disk immediately — **edit-in-place,
not draft mode.** Each directory has a "create new file" button for
adding new constitutions, skills, or strategies directly from the
dashboard.

Edit-in-place is the V1 choice. Simpler, more direct. The CEO edits,
saves, and the next agent turn uses the new file. V2 may add draft mode
if interleaved behavior becomes a problem (an agent reading a
half-written constitution mid-edit), but this is unlikely in practice
because agent turns are discrete.

### Tool Permissions (Read-Only in V1)

A grid view: **rows are tools, columns are roles, cells show permission
state.** This provides an at-a-glance answer to "what can each role
do?" without manually cross-referencing tool definitions and
constitutions.

Read-only in V1. Changing tool permissions requires code changes to the
tool registration. V2+ may make this editable from the dashboard once
the tool permission model is formalized.

### Versioning

Every dashboard save commits to git. Every change is auditable. The CEO
can always trace back through the history of constitution, skill, and
strategy changes to understand when and why the company's DNA changed.

V1 does not expose git history in the UI — the commit happens silently.
Surfacing version history, diffs, and rollback in the dashboard is a V2
feature.

### The Monitoring-Control Link

The two dashboards are not separate applications — they are two views
of the same system. V1 establishes the connection with a **Monitor |
Control toggle in the header** that lets the CEO switch between
dashboards while maintaining context.

V2 deepens the link with **deep linking**: clicking a monitoring event
takes you to the responsible control rule in the workflow view, and
workflow rules in the control dashboard show live firing indicators
pulled from the monitoring event stream.

### V1 Scope

What ships in V1:

- **Agents tab** — Org chart + inspector with constitution and skill
  editing.
- **Workflows tab** — Event cards with handler chain toggles.
- **Files tab** — Tree browser + editor with immediate save.
- **Tools tab** — Permissions grid, read-only.
- **Monitor | Control** toggle in the header.
- **Git commit on every save.**

What is explicitly V2+:

- Event log and dry-run simulator.
- Gating rules (per-tool approval thresholds).
- Lifecycle rules (agent create/terminate conditions).
- Authority derived view (effective permissions assembled from all rules).
- Live pulse indicators from monitoring in the control dashboard.
- Deep linking between monitoring events and control rules.
- Version history and rollback UI.

### What the Dashboard Is Not

- **Not a runtime control panel.** The CEO does not steer day-to-day
  operations from the dashboard. Day-to-day is Slack. The dashboard is
  for editing the company's standing rules — its DNA.
- **Not a way to bypass governance invariants.** The dashboard is a UI
  over the same files Claude Code edits. It is bound by the same
  invariants. Agents still cannot edit their own governance.
- **Not a source of truth on its own.** Every dashboard edit becomes a
  file change and a git commit. The repo is still the source of truth.

---

## Part 10 — Decision Framework: Which Lever to Pull

When you observe a problem with the system or want to change behavior,
this table tells you which layer to edit.

| Situation | Layer | Example |
|-----------|-------|---------|
| Agent makes a bad strategic decision | Constitution | Add: "Never enter competitions with <30 days remaining" |
| Agent needs to do something new right now | CEO Slack message | "Deep dive CAFA 6" |
| Agent needs a new capability | Tool (code change) | Add a `download_dataset` tool |
| Agent keeps using wrong ML approach | Strategy file | Write `tabular-methods.md` with proven techniques |
| Agent does a multi-step process poorly (twice) | Skill file | Write a step-by-step guide for that process |
| Agent shouldn't do something at all | Tool removal *or* Gating rule | Remove the tool, or add a `block` gate |
| Agent needs different authority level | Workflow / Gating rule | Edit the gating policy for that tool/role |
| Agent reports are too verbose / sparse | Constitution | Adjust reporting/communication section |
| Need a fundamentally different kind of worker | New agent type | Write `constitutions/engineering-worker.md` |
| One agent's outputs need to flow somewhere new | Workflow / Routing rule | Add a handler to the relevant event |
| One agent should wake up under a new condition | Workflow / Triggers rule | Add a trigger subscription |
| Need approval before a risky action | Workflow / Gating rule | Add a `require_approval` gate |
| Need an agent to be paused/resumed | Workflow / Lifecycle rule (V4) | Add a pause condition |

### Default to the Lowest-Power Lever That Solves the Problem

Constitution edits are powerful but rarely-changed; workflow rules are
toggleable but invisible to the agent's reasoning; CEO Slack messages
are dynamic but ephemeral. When more than one layer could solve a
problem, prefer the one that changes the *least*.

- A behavioral nudge for one task → CEO Slack message
- A behavioral pattern across many tasks → constitution edit
- A structural change in coordination → workflow rule
- A new physical capability → new tool
- A new kind of agent entirely → new constitution

---

## Part 11 — Flexibility vs. Rigidity: Default to Vague

A core design tension: **how much do you prescribe vs. let agents figure
out?**

The first run of the system proved that agents are surprisingly good at
figuring things out. Research subagents given vague directives produced
thorough, structured reports without being told how. The constitution
told them WHO they were and WHAT to investigate; they figured out HOW
on their own.

**Default to vague.** Let the agent use judgment. Add specificity only
when:

1. The agent gets it wrong repeatedly
2. The cost of getting it wrong is high (budget, CEO time, deadline)
3. You have specific knowledge the agent cannot discover on its own

### Constitution Guidance: WHAT and WHEN, Not HOW

- **Good:** "When a CEO message arrives, finish your current task before
  responding."
- **Bad:** "When a CEO message arrives, first check message length, then
  parse for competition names…"

### The Exception: Hard Constraints

Budget limits, authority boundaries, things agents must never do —
these should be as specific and unambiguous as possible. And, ideally,
they should be enforced in the workflow layer (gating), not just stated
in constitution prose.

---

## Part 12 — What the CEO Controls

| Control | Mechanism | Persistence |
|---------|-----------|-------------|
| Day-to-day company direction | Slack messages to VP | Per-conversation; VP remembers in transcript |
| Standing agent behavior rules | Constitution edits | Permanent until changed |
| Standing procedural guides | Skill edits | Permanent until changed |
| Institutional knowledge | Strategy files | Semi-permanent; evolves with evidence |
| Agent capabilities | Tool code changes (via Claude Code) | Permanent until changed |
| Coordination policies | Workflow rules | Permanent until changed (toggleable) |
| Budget caps | `.env` config | Permanent until changed |
| Model selection | `.env` config | Permanent until changed |
| Org structure | New constitutions / lifecycle rules | Permanent until changed |

### What the CEO Does NOT Do

- **Does not write code.** All implementation is delegated to Claude Code.
- **Does not micromanage execution.** Agents decide *how* to do their
  work. The CEO sets *what* and *why*.
- **Does not fight the budget controller.** When an agent asks for more
  budget, the CEO either grants it (one-time or by raising the cap) or
  declines. Bypassing the controller is forbidden by invariant #4.
- **Does not edit governance from inside an agent.** Even the CEO cannot
  ask the VP to edit its own constitution at runtime. Constitution
  changes go through Claude Code or the control dashboard, not through
  agent runtime.

---

## Part 13 — Current System State (April 2026)

```
constitutions/          WHO agents are
  vp.md                   The VP — most critical file
  research-worker.md      Research coordinator
  research-subagent.md    Autonomous researcher
  consolidation.md        Knowledge curator (not yet active)
  heartbeat.md            Periodic check logic (not yet active)

skills/                 EMPTY
                          Planned: deep-dive.md (V1, alongside workflow layer)
                          Loader: load_skill tool (planned)

strategies/             EMPTY
                          Will fill once experiments produce evidence

workflows/              DOES NOT EXIST YET
                          Planned V1: event bus + Python handlers
                          Planned V2: workflows/*.yaml files

src/
  runtime/                Agent loop — strict request-response
  orchestrator/           Lifecycle, message routing
                          Planned V1 addition: events.py (event bus)
  memory/                 State, strategy, transcript
  tools/                  Capabilities, role-locked
  comms/                  Slack bot, inter-agent CommHub (queue, not pub/sub)
  budget/                 Budget controller (the existing gate)

state/                  Runtime state (gitignored)
workspaces/             Per-agent working dirs (gitignored)
transcripts/            Conversation logs (gitignored)
reports/                Worker-generated reports (gitignored)

.env                    Budget caps, API keys, model selection
```

### What Works Today

- VP scouts competitions and responds to CEO Slack messages
- Research workers spawn parallel research subagents
- Subagents do iterative deep research and return structured findings
- Workers synthesize findings and save reports
- Reports upload to Slack as files
- Budget controller blocks overspend at API-call time

### What Is Planned Next

- The deep-dive skill file (`skills/deep-dive.md`) and the `load_skill` tool
- The event bus and the first workflow rule (`report.saved` →
  notify-VP-then-post-to-CEO)
- The control dashboard (Routing tab first, others as their dimensions
  are built)

---

## Part 14 — Anti-Patterns (System-Wide)

Mistakes that have been made before, or that a reasonable developer would
make without knowing the project's philosophy:

### Architecture
- **Don't add abstraction layers over the Anthropic SDK.** It IS the
  abstraction layer. Don't wrap it in a "provider interface."
- **Don't collapse to single-agent for simplicity.** The hierarchy is
  structural.
- **Don't build a database layer.** File-based state is intentional.

### Agents and Constitutions
- **Don't share constitutions across agent types.** Always create a new
  file for a new type.
- **Don't put procedural HOW in constitutions.** Constitutions describe
  identity and judgment. HOW belongs in skills.
- **Don't preemptively add skills and strategies "just in case."** Wait
  for evidence that the agent needs them.

### Budget and Governance
- **Don't let agents self-report on budget compliance.** Budget is
  checked in code before the API call, not after. An agent saying "I
  think I'm within budget" is meaningless.
- **Don't let agents modify governance.** Constitutions, the
  orchestrator, and the budget controller are off-limits to agent edits.

### Communication and Reporting
- **Don't build Slack messages without checking character limits.**
  Block Kit has specific limits. Decision buttons with long option text
  fail silently.
- **Don't impose fixed reporting schedules.** Agents report when they
  have something worth reporting, not on a timer.
- **Don't store conversation memory in the prompt.** Transcripts go to
  JSONL files and are searched on demand.

### Lifecycle and Heartbeats
- **Don't let incoming CEO messages cancel running agent tasks.** New
  messages queue, they don't interrupt.
- **Don't build a heartbeat that tight-loops on pending decisions.** If
  there's a pending decision, the heartbeat backs off.

### Workflow Layer
- **Don't put content-aware reasoning in workflow rules.** That's the
  agent's job. Workflows check facts.
- **Don't bypass workflow rules by hardcoding routing in a tool "just
  this once."** That kills the layer.
- **Don't proliferate events you have no handlers for.** Speculative
  events are dead weight.
- **Don't build YAML config until you have 3+ Python handlers.** YAML
  is for repeated patterns.

---

## Closing — The Mental Model

If you remember nothing else from this document, remember this:

> **Constitutions, skills, strategies, and tools define the agent's
> brain. The workflow layer defines the rails around the agent. Agents
> think; workflows route. Constitutions describe; workflows enforce.
> Default to vague inside the agent and to explicit in the workflow
> layer.**

Everything else in this document is an elaboration of that idea.
