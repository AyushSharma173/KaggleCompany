# Alignment Guide — How to Steer the Agentic System

This documents the design decisions about how inputs flow into the system,
which levers control what, and when to use each one. Written after the first
real run (CAFA 6 deep-dive, April 2026) based on what actually worked vs
what turned out to be dead weight.

---

## Core Principle: Constitutions Drive Everything

The first run proved something important: agents never loaded a single skill
or strategy file. 206 deep_research calls, 44+ URLs fetched, a comprehensive
intelligence report produced — all guided purely by constitutions + available
tools. The agents figured out HOW to research from their role definition and
the tools at their disposal.

This means **constitutions are the primary alignment lever**. Everything else
is supplementary and should only be added when a constitution alone isn't
enough.

---

## The Document Types

### Constitutions — WHO the Agent Is

**Directory:** `constitutions/`
**Loaded:** Every API call (cached in system prompt)
**Written by:** Us (CEO + Claude Code)
**Changes:** Rarely — when agent behavior needs structural correction

This is the most powerful lever. The constitution defines the agent's identity,
authority, constraints, decision framework, and workflow. Because it's loaded
on every single API call, it shapes every decision the agent makes.

**Current constitutions:**
- `vp.md` — The VP. Most critical file in the system. Controls company operations.
- `research-worker.md` — Research coordinator. Spawns subagents, synthesizes findings.
- `research-subagent.md` — Autonomous researcher. Investigates specific questions.
- `heartbeat.md` — Lightweight periodic check logic (not yet active).
- `consolidation.md` — Knowledge curator (not yet active).

**When to edit a constitution:**
- Agent is making bad decisions → tighten the decision framework
- Agent is doing something it shouldn't → add explicit constraints
- Agent isn't doing something it should → add it to the workflow section
- Agent communicates poorly → adjust the communication/reporting rules

**When to create a new constitution:**
- When you need a new type of agent (e.g., an engineering-worker that writes
  code and runs experiments, distinct from a research-worker that investigates)

**Key insight:** Constitutions should say WHO you are and WHEN to do things.
They shouldn't over-specify HOW — let the agent figure that out from its
tools. The research-subagent constitution doesn't say "search for X then Y
then Z." It says "you are an autonomous researcher with these tools, investigate
your assigned question thoroughly." The agent decides the search strategy.

### Strategies — Accumulated Knowledge

**Directory:** `strategies/` (currently empty)
**Loaded:** On demand, when an agent calls `get_strategy` tool
**Written by:** Consolidation agent (from experimental evidence), or us
**Changes:** Regularly — as the system learns from competitions

Strategies are the company's knowledge base. They should contain things agents
can't figure out on their own — lessons learned from past experiments, domain
knowledge that isn't available on the internet, internal performance data.

**Not yet used.** In the first run, agents did deep research using the internet
and didn't need pre-written strategy files. Strategies will become valuable
once workers start running actual experiments and we accumulate internal
knowledge worth preserving.

**When to create a strategy file:**
- When the consolidation agent identifies a pattern across experiments
- When you (CEO) have domain knowledge that agents keep getting wrong
- When a technique works and you want to ensure it's reused

**When NOT to create a strategy file:**
- For general ML knowledge (agents can research this themselves)
- For one-off observations (put in Slack, not a permanent file)
- Preemptively "just in case" — only when there's real evidence

### Skills — Process Guides (Currently Removed)

**Directory:** `skills/` (currently empty)
**Loaded:** On demand (tool currently removed)
**Written by:** Us
**Changes:** When processes need updating

Skills were step-by-step procedure guides — "how to do a deep dive," "how to
evaluate a competition." The first run proved agents didn't use them. The
constitutions provided enough guidance, and agents improvised the details.

**When to add skills back:**
- When an agent repeatedly does a process poorly despite good constitution
  guidance. A skill gives more prescriptive, step-by-step instructions for
  that specific process.
- When you want to enforce a specific methodology rather than letting the
  agent figure it out.

**The bar for adding a skill:** You've seen the agent do the task wrong at
least twice, and constitution edits didn't fix it. Then a skill file with
explicit steps is warranted.

### Tasks — Dissolved

There is no tasks directory. Tasks are dynamic, not predefined.

The first-boot directive is inlined in `main.py`. The daily briefing is
inlined in `scheduler.py`. All other work is generated dynamically: the CEO
sends a message on Slack, the VP interprets it, and the VP creates workers
with natural-language directives. No task files needed.

---

## How Work Flows Through the System

```
CEO sends Slack message
    "Let's deep dive CAFA 6"
         │
         ▼
VP receives message (constitution shapes interpretation)
    VP decides: create a research-worker for CAFA 6
         │
         ▼
VP creates worker with natural-language directive
    "Investigate CAFA 6 protein function prediction competition.
     Produce an intelligence report covering..."
         │
         ▼
Research worker executes (its constitution shapes approach)
    Worker spawns subagents for parallel research
         │
         ▼
Subagents do deep research (their constitution shapes depth)
    Each returns findings to worker
         │
         ▼
Worker synthesizes → posts report to Slack
VP relays to CEO
```

No task files, no skill files, no strategy files were involved.
The constitutions + tools were sufficient.

---

## The Alignment Levers (Ordered by Power)

### 1. Constitutions (strongest, always active)

Shapes every decision. Use when you want to change fundamental behavior.

**Rigidity: High.** What's in the constitution is always in the agent's mind.
Good for hard constraints, identity, authority boundaries, reporting rules.

### 2. CEO Messages via Slack (dynamic, on-demand)

The CEO's words become the VP's task. The VP interprets and delegates.
This is how you steer the company day-to-day without changing any files.

**Rigidity: Low.** It's a conversation, not a rule. The VP uses judgment
to interpret. Good for: "focus on X," "stop doing Y," "what's the status?"

### 3. Tools (what agents CAN do)

Agents can only act through their tools. If a tool doesn't exist, the agent
can't do it. If a tool exists, the agent might use it.

**Rigidity: Absolute.** Tools are code — they define the boundaries of what's
possible. Adding or removing a tool is a hard capability change.

Tools are also filtered by agent role. The VP can create workers but subagents
cannot. This is enforced in code, not in prompts.

### 4. Strategies (knowledge, loaded on demand)

Provides domain knowledge the agent wouldn't otherwise have. Only useful
once you have knowledge worth preserving (from experiments, failures, etc.).

**Rigidity: Medium.** The agent chooses whether and when to load a strategy.
The constitution can hint ("read the tabular-methods strategy before starting
experiments") but can't force it.

### 5. New Agent Types (structural capability change)

Creating a new agent type adds a fundamentally different kind of worker to the
company. This is the lever for when you need agents that think and behave
differently — not just different instructions, but a different role.

**How to add a new agent type:**
1. Write `constitutions/your-type.md` with the agent's identity, workflow,
   and constraints
2. That's it. The VP creates it with `worker_type="your-type"` or a worker
   spawns subagents with `subagent_type="your-type"`

The architecture separates **role** (VP, WORKER, SUBAGENT, CONSOLIDATION)
from **type** (research-worker, engineering-worker, etc.). Role controls
tool access permissions. Type controls constitution and behavioral identity.
All workers share the same tool set regardless of type — the constitution
guides how they use those tools.

**Agent config flags:**
- `ephemeral=True` — no heartbeat, auto-cleanup after task (subagents)
- `skip_reflection=True` — no post-task reflection step (subagents)

**Rigidity: High.** A new constitution creates a fundamentally different agent.
The VP needs to know about it (update VP constitution to mention new types).

### 6. Skills (process guides, loaded on demand — currently disabled)

Step-by-step instructions for how to do something. More prescriptive than
a constitution's workflow section. Only needed when agents repeatedly get
a process wrong.

**Rigidity: Medium.** Like strategies, the agent chooses when to load them.
But once loaded, they're detailed enough to follow step-by-step.

---

## Decision Framework: Which Lever to Pull

| Situation | Lever | Example |
|-----------|-------|---------|
| Agent makes a bad strategic decision | Constitution | Add constraint: "Never enter competitions with <30 days remaining" |
| Agent needs to do something new right now | CEO Slack message | "Deep dive CAFA 6" |
| Agent needs a new capability | Tool (code change) | Add a `download_dataset` tool |
| Agent keeps using wrong ML approach | Strategy file | Write `tabular-methods.md` with proven techniques |
| Agent does a multi-step process poorly | Skill file | Write a step-by-step guide for that process |
| Agent shouldn't do something at all | Tool removal or constitution constraint | Remove the tool or add "Never..." to constitution |
| Agent needs different authority level | Constitution | Edit decision authority matrix |
| Agent reports are too verbose/sparse | Constitution | Edit reporting/communication section |
| Need a fundamentally different kind of worker | New agent type | Write `constitutions/engineering-worker.md` |

---

## Flexibility vs Rigidity

A key design tension: **how much do you prescribe vs let agents figure out?**

Our first run proved that agents are surprisingly good at figuring things out.
The research subagents were given vague directives ("investigate evaluation
metrics for CAFA 6") and produced thorough, structured reports without being
told how.

**Default to vague.** Let the agent use judgment. Only add specificity when:
1. The agent gets it wrong repeatedly
2. The cost of getting it wrong is high (budget, CEO time, competition deadline)
3. You have specific knowledge the agent can't discover on its own

**Constitution guidance should be about WHAT and WHEN, not HOW:**
- Good: "When a CEO message arrives, finish your current task before responding"
- Bad: "When a CEO message arrives, first check message length, then parse for
  competition names, then look up each competition..."

The exception: hard constraints. Budget limits, authority boundaries, things
agents must never do — these should be as specific as possible.

---

## Adding a New Agent Type

When you need agents that behave fundamentally differently:

1. Create `constitutions/new-type.md` with role, authority, constraints, workflow
2. Update the VP constitution to mention the new type so it knows to use it
3. No other files or code changes needed

The system uses `agent_type` (a string like "research-worker") separately from
`role` (an enum: VP, WORKER, SUBAGENT, CONSOLIDATION). The type selects the
constitution file; the role controls tool access. All workers share the same
tools regardless of type — the constitution shapes how they use those tools.

For subagent types, workers use `spawn_subagents` with the `subagent_type`
parameter to select the right constitution.

Example: when we're ready for engineering workers that run experiments:
- Create `constitutions/engineering-worker.md`
- The VP creates workers with `worker_type="engineering-worker"`
- The constitution guides the agent; tools provide capabilities
- Engineering workers can spawn their own subagent types via
  `spawn_subagents(subagent_type="coding-subagent")`

---

## What the CEO Controls

| Control | Mechanism | Persistence |
|---------|-----------|------------|
| Company direction | Slack messages to VP | Per-conversation (VP remembers in transcript) |
| Agent behavior rules | Constitution edits | Permanent (until changed) |
| What agents can do | Tool code changes | Permanent (until changed) |
| Domain knowledge | Strategy files | Semi-permanent (evolves with evidence) |
| Budget | `.env` config | Permanent (until changed) |
| Which model agents use | `.env` config | Permanent (until changed) |

---

## Current System State (Post-Cleanup, April 2026)

```
constitutions/          WHO agents are
  vp.md                   VP — the critical file
  research-worker.md      Research coordinator
  research-subagent.md    Autonomous researcher
  heartbeat.md            Periodic check logic (not yet active)
  consolidation.md        Knowledge curator (not yet active)

strategies/             EMPTY — will fill as we run experiments
skills/                 EMPTY — will add when agents need process guidance
.env                    Budget, API keys, model selection
```

Everything else is code (`src/`), which you modify through Claude Code
conversations, not directly.
