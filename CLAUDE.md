# Project Details: Kaggle Company — Autonomous AI Employee System

## What This Is

This is not a Kaggle bot. This is a **company** staffed by AI agents that makes money
by competing autonomously on Kaggle. The agents are employees — a VP, workers, and
temporary hires — operating under a CEO (Sharma) who sets strategy via Slack.
The CEO never writes code and never micromanages execution. Agents decide what to work on,
how to work on it, and when to report.

The singular success metric is **net profit**: Kaggle prize money earned minus all
compute and API costs. Medals, rankings, and learning are side effects, not objectives.
Every design decision should be evaluated against: "does this increase expected profit?"

Kaggle is the first vertical. The reusable infrastructure — agent loop, communication,
memory, self-improvement — is an "agentic OS" that should eventually deploy to any
domain (research monitoring, real estate analysis, idea generation). Domain-specific
parts (tools, strategies, success metrics) are configuration. Standing up a new vertical
should take days, not months.

## Architecture Overview

Six components. Know what each does and where it lives:

- **Agent Runtime** (`src/runtime/`) — The core loop every agent runs: load context → call Claude API → execute tool calls → repeat. VP, worker, and subagent are all instances of this runtime with different config.
- **Orchestrator** (`src/orchestrator/`) — Deterministic lifecycle manager. Creates/terminates agents, routes messages, manages heartbeat scheduling. NOT an AI — pure Python control flow.
- **Memory System** (`src/memory/`) — Three-layer: structured state JSON (always loaded), strategy markdown files (loaded on demand), JSONL transcripts (searched, never loaded). Treat context as scarce.
- **Tools** (`src/tools/`) — Async Python functions mapped to Claude's tool use format. Grouped by category: kaggle, execution, research, communication, agent management, GPU.
- **Slack Bot** (`src/comms/`) — Slack Bolt SDK, Socket Mode. CEO's only interface to the system. Channels are structured by purpose (see `#decisions`, `#ceo-briefing`, `#alerts`, `#vp-agent`, `#comp-{slug}`).
- **Budget Controller** (`src/budget/`) — Deterministic spend enforcement. Runs BEFORE any API call or GPU provisioning. Not a suggestion in a prompt — hard code that blocks overspend.

The agent hierarchy:

```
CEO (Sharma, via Slack)
  └── VP Agent (always-on, scouts competitions, manages portfolio, creates/kills workers)
        ├── Worker Agent (one per competition, runs experiments, spawns subagents)
        │     └── Subagent (ephemeral, single task, auto-terminates)
        └── Consolidation Agent (periodic, updates strategy library from evidence)
```

## Foundational Invariants

These are load-bearing decisions. If you violate any of these, the resulting code is
architecturally wrong even if it compiles and runs. Do not propose alternatives.

1. **Anthropic Claude is the sole AI provider.** No OpenAI, no Gemini, no local models,
   no multi-provider abstraction layers. Every AI call goes through the Anthropic API.

2. **No frameworks.** No LangChain, no CrewAI, no AutoGen, no LlamaIndex. Built from
   scratch on the Anthropic SDK. The overhead and abstraction of frameworks conflicts
   with the level of control this system needs.

3. **Slack is the only human interface.** No web dashboards, no CLI monitoring tools,
   no admin panels. Everything the CEO sees comes through Slack. Everything the CEO
   says goes through Slack.

4. **Budget enforcement is deterministic code, not prompts.** The budget controller
   is a Python class that runs before any spend. Agents cannot modify, bypass, or
   negotiate with the budget controller. It is outside agent authority by design.
   An agent hitting its budget limit must request more from the VP or CEO — the
   controller never says "well, this seems important, I'll allow it."

5. **Agents are proactive employees, not reactive tools.** The VP runs continuously
   via heartbeat (KAIROS pattern). It decides when to scout competitions, when to
   check on workers, when to report. Workers determine their own experiment cadence.
   No agent waits for a human to tell it what to do next.

6. **Multi-agent from day one.** Never collapse to single-agent for "simplicity."
   The VP-worker-subagent hierarchy is structural, not optional. A worker that needs
   parallel research spawns subagents. The VP that needs multiple competitions
   creates multiple workers.

7. **Agents cannot modify their own governance.** No agent can edit constitutions,
   the orchestrator, or the budget controller. Strategy files are writable only by
   the consolidation agent. Experiment logs are append-only. Workspaces are isolated.

## Design Heuristics

When you face an ambiguous implementation choice, use these to decide:

- **"Would a well-run company do it this way?"** The organizational metaphor is not decorative. If a decision doesn't make sense for a company with a CEO and employees, it probably doesn't make sense here.
- **Architectural clarity over code elegance.** The bottleneck is never code quality — it's whether the design is right. A clear, verbose implementation of the right architecture beats a clever, concise implementation of the wrong one.
- **Proactive over reactive.** If you're adding a feature that waits for a trigger, ask whether the agent should instead be periodically checking whether the trigger condition is met.
- **Context is scarce.** Every token loaded into an agent's prompt costs money and displaces other information. The memory system's three-layer design exists to enforce this. Don't load information "just in case."
- **Deterministic enforcement over trust.** Anything where a mistake has irreversible consequences (spending money, deleting data, modifying governance) must be enforced in code, not relied upon in prompts. Prompts are suggestions. Code is law.
- **Parallel over sequential.** Research tasks, subagent work, and multi-competition management should run concurrently when possible. Serial execution wastes time and time costs money (competition deadlines are real).

## Tech Stack

- **Language:** Python 3.11+ (async throughout, type hints)
- **AI:** Anthropic API (direct SDK, `anthropic` package)
- **Messaging:** Slack Bolt SDK, Socket Mode
- **Deployment:** Docker + docker-compose on VPS
- **GPU:** Kaggle free GPUs (preferred) + RunPod pay-per-minute (overflow)
- **Data:** Kaggle API (`kaggle` package)
- **State:** JSON files (structured state), markdown files (strategies), JSONL (transcripts, experiments)
- **No database.** File-based state is intentional — simple, inspectable, version-controllable
- **No web framework.** No Flask, no FastAPI. The system has no HTTP endpoints except Slack's socket connection

## Codebase Map

```
src/
  runtime/          # Core agent loop, context loading, prompt assembly
  orchestrator/     # Agent lifecycle, heartbeat scheduling, message routing
  memory/           # State store, strategy reader/writer, transcript manager
  tools/            # kaggle_tools, execution_tools, research_tools,
                    # communication_tools, agent_mgmt_tools, gpu_tools
  comms/            # slack_bot.py, inter_agent.py
  budget/           # Budget controller, spend tracking
  consolidation/    # autoDream — strategy library updates from evidence
constitutions/      # Markdown system prompts: vp.md, worker.md, subagent.md, consolidation.md
strategies/         # Evolving knowledge files: competition-selection.md, tabular-methods.md, etc.
skills/             # Agent skill prompts (e.g., deep-research.md, competition-evaluation.md)
tasks/              # Task definitions (e.g., first-boot.md)
state/              # Runtime state JSON files (gitignored)
workspaces/         # Per-agent working directories (gitignored)
transcripts/        # Agent conversation logs (gitignored)
```

## Guidance Hierarchy (Runtime Agents)

The running agents have a 5-layer guidance system. You modify these files, not the
agent runtime code, to change agent behavior:

1. **Constitution** (`constitutions/*.md`) — Role, objective, hard constraints. Rarely changes. Cached for prompt efficiency.
2. **Operating Manual** — Decision authority matrix, reporting protocol. Embedded in constitution.
3. **Strategy Library** (`strategies/*.md`) — Evolving knowledge per domain. Written by consolidation agent from experimental evidence. Agents read relevant files on demand.
4. **Active Context** — Dynamic per-session: portfolio status, budget, pending decisions. Assembled by `src/memory/state_store.py`.
5. **Conversation Memory** — Transcripts. Searched, never bulk-loaded.

When changing how agents behave, modify constitutions or strategy files first.
Only modify runtime code if the behavioral change requires new tools or new control flow.

## Anti-Patterns — What NOT to Do

These are mistakes that have been made before or mistakes a reasonable developer
would make without knowing the project's philosophy:

- **Don't let agents self-report on their own budget compliance.** Budget is checked in code before the API call, not after. An agent saying "I think I'm within budget" is meaningless.
- **Don't build Slack messages without checking character limits.** Block Kit has specific limits. Decision buttons with long option text will fail silently. Truncate or paginate.
- **Don't let incoming CEO messages cancel running agent tasks.** A new Slack message from the CEO should queue, not interrupt. The VP should finish its current task, then check for new messages.
- **Don't use max_tokens without handling truncation.** If a response hits the token limit, it's cut off mid-thought. Either set max_tokens high enough, or detect truncation and continue.
- **Don't build a heartbeat that tight-loops on pending decisions.** If the VP has a pending CEO decision, the heartbeat should skip or back off, not re-evaluate the same state every 30 seconds.
- **Don't store conversation memory in the prompt.** Transcripts go to JSONL files and are searched when needed. Loading full conversation history into every API call is wasteful and context-destroying.
- **Don't add abstraction layers over the Anthropic API.** The SDK is the abstraction layer. Don't wrap it in a "provider interface" or "model manager" — there will only ever be one provider.
- **Don't impose fixed reporting schedules.** The VP reports when it has something worth reporting, not on a timer. Workers report when experiments complete, not hourly.
- **Don't treat the strategy library as static config.** Strategy files are living documents updated by the consolidation agent based on experimental evidence. They should cite which experiments support each technique.

## Working With Sharma

Sharma is the CEO and the sole human operator. He does NOT write code — all
implementation is done through Claude Code (this tool). Understanding how he works
helps you make better decisions:

- **He describes what he wants architecturally.
- **DO ask about architectural decisions.** "Should subagents share the worker's memory or get fresh context?" is a question worth asking. "Should I use a dict or a dataclass?" is not.
- **Don't be conservative.** Don't pad time estimates. Don't hedge about what agents can accomplish. Don't suggest "starting simple" when the design calls for multi-agent. Build what's designed.
- **He thinks in organizational metaphors.** VP, workers, employees, reporting lines, authority matrices, company culture. Use these metaphors when explaining design tradeoffs — they map directly to how he thinks about the system.

## Current State & Known Issues

V1 is built and running. VP agent is operational — it scouts competitions and
responds to CEO messages in Slack.