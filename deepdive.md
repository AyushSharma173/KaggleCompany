# Kaggle Company System - Deep Dive

## System Overview

An autonomous multi-agent system where AI agents (powered by Claude) operate as employees of a company that competes on Kaggle. Runs 24/7 in Docker, communicates via Slack, governed by deterministic budget controls. Built from scratch — no frameworks (LangChain, CrewAI, etc.).

**Current State:** 1 VP agent running, 0 workers, 0 subagents. System is operational — VP scouts competitions, responds to CEO messages in Slack, posts briefings and decision requests.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────┐
│                    CEO (You, Sharma)                      │
│                 Interacts via Slack                       │
└────────────┬────────────────────────────┬────────────────┘
             │ Messages                    │ Decisions
             ▼                             ▼
┌────────────────────────┐    ┌──────────────────────────┐
│      VP Agent          │    │   #decisions channel     │
│   (vp-001, always on)  │    │   Approve/Reject buttons │
│                        │    └──────────────────────────┘
│ - Scouts competitions  │
│ - Manages portfolio    │
│ - Creates/kills workers│
│ - Posts briefings      │
│ - Escalates decisions  │
└───────┬────────────────┘
        │ Creates when competition approved
        ▼
┌──────────────────────┐    ┌──────────────────────┐
│  Worker Agent        │    │  Worker Agent         │
│  (worker-comp-slug)  │    │  (worker-comp-slug2)  │
│                      │    │                       │
│  - Runs experiments  │    │  - Independent        │
│  - Submits to Kaggle │    │  - Own budget         │
│  - Reports to VP     │    │  - Own workspace      │
└───────┬──────────────┘    └───────────────────────┘
        │ Spawns for subtasks
        ▼
┌──────────────────────┐
│  Subagent (ephemeral)│
│  - Does one task     │
│  - Returns results   │
│  - Auto-terminates   │
└──────────────────────┘

┌──────────────────────────────────────────────────┐
│  Consolidation Agent (runs daily, not always on) │
│  - Reviews experiment logs                        │
│  - Updates strategy library                       │
│  - Posts changelog to #research                   │
└──────────────────────────────────────────────────┘
```

---

## Agent Hierarchy & Roles

| Agent | Who Creates It | Reports To | Authority | Lifetime |
|-------|---------------|------------|-----------|----------|
| **VP** | System (auto on first boot) | CEO (you) | Create/kill workers, allocate budget (<$20 autonomous), escalate decisions | Always running |
| **Worker** | VP (via `create_worker_agent` tool) | VP | Choose ML approaches, run experiments, spawn subagents, submit predictions | Until competition ends or VP terminates |
| **Subagent** | Worker (via `spawn_subagent` - not yet implemented) | Worker | Execute one task, return results | Ephemeral (terminates after task) |
| **Consolidation** | Scheduler (daily trigger) | Nobody | Read experiment logs, update strategy files | Runs once, terminates |

**Currently only the VP agent exists.** Workers are created when the VP calls `create_worker_agent` after you approve a competition. Subagents and consolidation are coded but not yet triggered in practice.

---

## How the Agent Loop Works

Every agent (VP, Worker, Subagent) uses the same runtime engine (`src/runtime/agent.py`). The differences come from:
1. **Constitution** (system prompt loaded from markdown file)
2. **Tool access** (role-based filtering)
3. **Heartbeat interval** (VP: 5min, Worker: 10min, Subagent: none)

### Per-Task Flow

```
1. Agent receives task (from CEO message, heartbeat, or another agent)
2. Context cleared, task set as first user message
3. Dynamic state injected (portfolio, budget, active agents)
4. LOOP:
   a. Call Claude API with: system prompt (constitution + state) + message history + tools
   b. If Claude calls tools → execute them, append results, go to (a)
   c. If Claude says "end_turn" → task done, run reflection, return text
   d. If max_tokens hit → response cut off, task ends (BUG: no continuation)
   e. If max_turns (200) reached → force stop
5. Final text returned to orchestrator
6. If triggered by CEO message → auto-posted back to Slack channel
```

### Heartbeat (What Happens When Agent Is Idle)

Every 5 minutes (VP) or 10 minutes (Worker):
1. Cheap/fast model gets minimal context: agent status, portfolio, budget, pending decisions
2. Decides: IDLE (do nothing), WAKE (start a task), CHECK_GPU, or REPORT
3. If WAKE → starts a full task with the description from heartbeat

**Current issue:** Heartbeat sees pending decisions and keeps waking the VP with "REVIEW ALL 12 PENDING CEO DECISIONS" on loop.

---

## Constitutions (Editable Agent Personalities)

These markdown files define each agent's behavior. **Editing these is the primary way to change how agents behave.**

| File | Agent | Key Points |
|------|-------|------------|
| `constitutions/vp.md` | VP | Identity: "VP of Operations, CEO is Sharma". Objectives: maximize profit, diversify portfolio. Autonomous for <$20 decisions, escalates >$50. Daily briefings. Portfolio max 3 competitions. |
| `constitutions/worker.md` | Worker | Identity: "ML Engineer". 5-phase workflow: Research→Baseline→Iterate→Ensemble→Submit. Report >10% rank jumps. Don't report routine iterations. Use cross-validation. |
| `constitutions/subagent.md` | Subagent | Temporary specialist. Can't create agents or contact CEO. Return structured output. Try one alternative before giving up. |
| `constitutions/consolidation.md` | Consolidation | Knowledge curator. Reviews experiments, updates strategy files with evidence. Can ONLY modify strategy files. Dates additions, flags uncertainty. |

**How to improve agent behavior:** Edit these files. They are the system prompts. If the VP is making bad decisions or being too verbose, modify `constitutions/vp.md`. Changes take effect on next container restart.

---

## Tools (32 Total)

### Full Tool List by Category

#### Kaggle Tools (`src/tools/kaggle_tools.py`)
| Tool | What It Does | VP | Worker | Sub | Consol |
|------|-------------|:--:|:------:|:---:|:------:|
| `list_competitions` | List active competitions (CSV from kaggle CLI) | Y | Y | Y | - |
| `get_competition_details` | Get details + data files for a competition | Y | Y | Y | - |
| `download_data` | Download competition data to workspace | - | Y | Y | - |
| `submit_prediction` | Submit prediction CSV | - | Y | - | - |
| `get_leaderboard` | Get leaderboard (CSV) | Y | Y | - | - |
| `list_submissions` | List our past submissions | Y | Y | - | - |
| `search_discussions` | Get discussion forum URL | Y | Y | Y | - |

#### Execution Tools (`src/tools/execution_tools.py`)
| Tool | What It Does | Worker | Sub |
|------|-------------|:------:|:---:|
| `run_python` | Execute Python in subprocess (max 5min, workspace-confined) | Y | Y |
| `run_shell` | Execute shell command (dangerous patterns blocked) | Y | Y |
| `read_file` | Read file from workspace (max 500 lines) | Y | Y |
| `write_file` | Write file to workspace | Y | Y |
| `edit_file` | Find-and-replace in file | Y | Y |
| `list_files` | List workspace directory | Y | Y |

#### Research Tools (`src/tools/research_tools.py`)
| Tool | What It Does | VP | Worker | Sub | Consol |
|------|-------------|:--:|:------:|:---:|:------:|
| `web_fetch` | Fetch URL, extract content (JS-rendered pages, PDFs; max 100K chars) | Y | Y | Y | - |
| `arxiv_search` | Search arXiv papers (max 10) | Y | Y | Y | Y |
| `get_strategy` | Read a strategy doc | Y | Y | Y | Y |
| `list_strategies` | List available strategies | Y | Y | Y | Y |
| `update_strategy` | Write/update strategy doc | - | - | - | Y |

#### Communication Tools (`src/tools/communication_tools.py`)
| Tool | What It Does | VP | Worker |
|------|-------------|:--:|:------:|
| `send_slack_message` | Post to Slack channel | Y | Y |
| `report_progress` | Post to #system-log | Y | Y |
| `request_ceo_decision` | Submit decision to #decisions with buttons | Y | - |
| `request_budget_increase` | Request more budget (calls request_ceo_decision) | Y | Y |
| `send_to_agent` | Send async message to another agent | Y | Y |

#### Agent Management Tools (`src/tools/agent_mgmt_tools.py`)
| Tool | What It Does | VP |
|------|-------------|:--:|
| `create_worker_agent` | Create worker for competition | Y |
| `terminate_agent` | Kill an agent | Y |
| `list_agents` | List all agents + status | Y |
| `get_agent_output` | Get agent's latest status | Y |
| `reassign_budget` | Move budget between agents | Y |

#### GPU Tools (`src/tools/gpu_tools.py`)
| Tool | What It Does | Worker | Sub |
|------|-------------|:------:|:---:|
| `gpu_execute` | Start GPU job (Kaggle free or RunPod paid), non-blocking | Y | Y |
| `check_gpu_job` | Poll job status | Y | Y |
| `terminate_gpu` | Stop instance, record cost | Y | Y |
| `list_gpu_instances` | List active instances | Y | - |

### How Tool Access Is Determined

Tool access is **hard-coded per role** in each tool's `allowed_roles` set. The `ToolExecutor` checks `role in tool.allowed_roles` before every call. If denied, the agent gets an error message: "You do not have permission to use the 'X' tool."

To change which roles can use which tools, edit the `allowed_roles` set in each tool's `ToolDefinition` in the relevant `src/tools/*_tools.py` file.

---

## Strategies (Editable Knowledge Library)

These markdown files contain accumulated ML knowledge that agents read before making decisions. The **Consolidation agent** is designed to update them based on experiment results, but you can also edit them directly.

| File | Topic | Summary |
|------|-------|---------|
| `strategies/competition-selection.md` | Which competitions to enter | Score by expected value, feasibility, strategic value. Prefer tabular, avoid vision unless big prize. Max 3 active. |
| `strategies/tabular-methods.md` | Tabular ML playbook | Start with LightGBM defaults. Feature engineering checklist. Model selection guide. Optuna tuning. Cross-validation rules. |
| `strategies/nlp-methods.md` | NLP competition approaches | Start with DeBERTa-v3. Training recipe (AdamW, 2e-5 lr, cosine). Adversarial training, pseudo-labeling, multi-task. |
| `strategies/vision-methods.md` | Computer vision approaches | EfficientNet for baselines, Swin for SOTA. Augmentation guide. Budget warning: vision is expensive. |
| `strategies/ensembling.md` | How to ensemble | Diversity is key. Start with simple averaging of 3 models. Rank averaging, stacking, blending. Max 5-7 models. |
| `strategies/notebook-strategy.md` | Making money from notebooks | Post EDA early (first 3 days), baselines first week. Structure for upvotes. Low cost, high reputation value. |
| `strategies/kaggle-meta.md` | How Kaggle works | Competition types, submission limits, public vs private LB, medal thresholds, progression system, common mistakes. |

**To improve agent strategy:** Edit these files directly. Agents read them via `get_strategy` tool. Add lessons from your own Kaggle experience.

---

## Message Flow: CEO to Response

```
You type in Slack (#vp-agent, #ceo-briefing, or any channel)
    ↓
SlackBot receives event via Socket Mode
    ↓
Calls message_callback → manager.handle_ceo_message(channel, text, thread_ts)
    ↓
Manager cancels any running VP task (ISSUE: interrupts work)
    ↓
Starts new task: agent.run_task("CEO says: {your message}")
    ↓
VP agent loops: calls Claude → uses tools → calls Claude → ...
    ↓
Agent returns final_text
    ↓
Manager auto-posts final_text back to the same Slack channel
    ↓
You see the response (~10-30 seconds later)
```

---

## Budget System

**Deterministic enforcement in Python code — NOT in prompts.** The agent literally cannot overspend because `check_budget()` is called before every API call and tool execution.

| Setting | Default | Where Set |
|---------|---------|-----------|
| Daily global budget | $50.00 | `.env` → `GLOBAL_DAILY_BUDGET_USD` |
| Per-agent budget | $100.00 | `.env` → `DEFAULT_AGENT_BUDGET_USD` |
| Warning threshold | 80% | Hard-coded in `src/budget/tracker.py` |

**API Cost Tracking:** Every Claude API call is priced using token counts × per-model pricing table. Pricing is hard-coded in `src/budget/tracker.py`:
- Sonnet: $3/M input, $15/M output
- Opus: $15/M input, $75/M output
- Haiku: $0.80/M input, $4/M output

**GPU Cost Tracking:** RunPod costs tracked by hours × rate (e.g., RTX 4090 = $0.69/hr). Kaggle GPU is free.

---

## Slack Channels

| Channel | Purpose | Who Posts |
|---------|---------|----------|
| `#ceo-briefing` | Daily summaries, strategic updates | VP |
| `#decisions` | Decision requests with Approve/Reject buttons | VP |
| `#alerts` | Urgent notifications (budget, failures) | VP, System |
| `#vp-agent` | Direct conversation with VP | CEO ↔ VP |
| `#research` | Research findings, papers, strategy updates | VP, Consolidation |
| `#system-log` | Agent lifecycle events | Orchestrator |
| `#comp-{slug}` | Per-competition channel (created dynamically) | Worker |

---

## State & Persistence

```
state/                          ← JSON files, gitignored, persists across restarts
  agents/vp-001.json            ← Agent config + status
  budget/daily.json             ← Today's spend tracking
  decisions/pending.json        ← Unresolved CEO decisions
  decisions/resolved.json       ← Past decisions
  portfolio/active.json         ← Active competitions

transcripts/                    ← JSONL logs per agent per day
  vp-001/2026-04-04.jsonl      ← Every API call, tool use, reflection

workspaces/                     ← Per-agent isolated directories
  vp-001/                       ← VP's workspace (mostly empty)
  worker-comp-X/                ← Worker's data, scripts, models

strategies/                     ← Markdown knowledge library (editable)
constitutions/                  ← Agent system prompts (editable)
```

---

## Known Issues & Improvement Areas

### 1. Responses Cut Off Mid-Sentence
**What:** Agent hits `max_tokens=4096` and stops abruptly.
**Where:** `src/runtime/agent.py` line 183
**Fix needed:** When `stop_reason == "max_tokens"`, continue the conversation with "Please continue from where you left off" instead of breaking.

### 2. Decision Buttons Fail (">76 characters")
**What:** Slack rejects decision request posts because button text exceeds 75 char limit.
**Where:** `src/comms/slack_bot.py` → `post_decision_request()`
**Fix needed:** Truncate button text to 75 chars.

### 3. CEO Messages Cancel Running Tasks
**What:** Every message you send interrupts whatever the VP is doing.
**Where:** `src/orchestrator/manager.py` → `run_agent_task()` line 161-166
**Fix needed:** Queue CEO messages instead of cancelling current task, or only cancel for urgent messages.

### 4. Heartbeat Loops on Pending Decisions
**What:** VP keeps getting woken up with "REVIEW ALL 12 PENDING CEO DECISIONS" every 5 minutes.
**Where:** Heartbeat context includes pending decision count → heartbeat recommends waking → same decisions still pending → loop.
**Fix needed:** Track "last_reviewed" timestamp for decisions, skip if reviewed recently.

### 5. Agent Looks at Old/Irrelevant Competitions
**What:** Kaggle CLI returns historical competitions mixed with current ones.
**Where:** `src/tools/kaggle_tools.py` → `list_competitions()`
**Fix needed:** Filter by deadline > today, or add date filtering to the tool.

### 6. No Conversation Memory Between Tasks
**What:** Each CEO message starts a completely fresh context. The VP doesn't remember what you talked about 2 minutes ago.
**Where:** `src/runtime/agent.py` → `run_task()` line 124-126 clears history
**Fix needed:** Maintain a rolling conversation history for CEO interactions instead of clearing each time.

---

## Files You'd Want to Edit

| Priority | File | Why |
|----------|------|-----|
| **High** | `constitutions/vp.md` | Change VP behavior, decision-making, communication style |
| **High** | `strategies/*.md` | Improve ML strategies agents follow |
| **High** | `.env` | Budget limits, model selection, heartbeat intervals |
| **Medium** | `src/runtime/agent.py` | Fix max_tokens continuation, context management |
| **Medium** | `src/orchestrator/manager.py` | Fix task cancellation, message queueing |
| **Medium** | `src/comms/slack_bot.py` | Fix decision button length, improve formatting |
| **Medium** | `src/tools/kaggle_tools.py` | Filter old competitions, improve data quality |
| **Low** | `constitutions/worker.md` | Refine worker behavior (matters when workers are created) |
| **Low** | `src/runtime/heartbeat.py` | Fix decision review loop, improve heartbeat intelligence |
| **Low** | `src/budget/tracker.py` | Adjust pricing, add alerts |
