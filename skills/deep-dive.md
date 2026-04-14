# Skill: Competition Deep Dive

This skill documents how Kaggle Company conducts a deep dive on a Kaggle
competition. It is loaded on demand by both the **VP** (when commissioning a
deep dive and when evaluating the resulting report) and the **research worker**
(when executing the deep dive itself).

A deep dive's purpose is **to reduce uncertainty enough to commit real money
and weeks of agent-time to a competition** — or to confidently walk away.
The output is a Competition Intelligence Report that leaves nothing important
unknown about the competition.

This skill has multiple sections. Read the ones relevant to your role:

- **Research workers** executing a deep dive → read *Purpose*, *What "alpha"
  means*, *What makes a good Intelligence Report*, and *Phase 1: Initial
  Scout Deep Dive (Execution)*.
- **VPs** commissioning a deep dive → read *Commissioning a Deep Dive*.
- **VPs** evaluating a returned report → read *Evaluating a Deep-Dive Report*.

---

## Purpose

A deep dive exists to answer one question: **is this competition worth committing
serious resources to, and if so, how do we win it?**

A good Intelligence Report leaves the reader (CEO + VP) able to make a
go/no-go decision with confidence and, if go, with a concrete technical
roadmap. A bad Intelligence Report is a pile of facts that doesn't help anyone
decide.

The bar is not "comprehensive." The bar is **decision-grade**.

---

## What "alpha" means concretely

"Alpha" is the edge that lets us beat the public leaderboard. A research
worker should explicitly look for alpha in four categories — and the final
report must call out specific findings in each:

1. **Frontier techniques not yet in public notebooks.** Methods from recent
   papers (last 12–18 months) in adjacent fields that map onto this problem
   but haven't shown up in public Kaggle solutions yet. Cite the paper,
   describe the technique, explain why it would apply.

2. **Gaps in the public notebook landscape.** What aren't the top public
   notebooks doing? What's everyone copying from each other and missing?
   What's the consensus approach, and what's the consensus approach
   *missing*?

3. **Data quirks the leaders haven't exploited.** Label noise patterns,
   leakage opportunities (legitimate ones), distribution shifts between
   train/test, hidden structure in metadata. Things you can only find by
   actually looking at the data carefully.

4. **External data, tools, or compute strategies others aren't using.**
   Public datasets that augment the training set. Pretrained models nobody
   has fine-tuned for this. Compute tricks (quantization, distillation,
   efficient architectures) that change what's feasible.

If the report doesn't have at least one specific candidate in each category —
or doesn't explain why a category is empty — the deep dive is incomplete.

---

## Fit assessment for our system specifically

A competition can have rich alpha and *still* be a bad fit for Kaggle Company.
The deep dive must answer these questions about the competition's fit with
our specific operating model:

- **Is the experiment loop automatable end-to-end?** Can an agent train,
  evaluate, and submit without human intervention? Or does this competition
  require human judgment in the loop (manual feature engineering, qualitative
  evaluation, hand-tuning)?
- **What is the iteration cycle time?** A competition where one experiment
  takes 6 hours of GPU time supports many fewer iterations than one where
  experiments take 30 minutes. Faster cycles compound.
- **Is free Kaggle GPU sufficient, or do we need RunPod?** This is a direct
  cost lever. Estimate hours per submission and hours per "good run."
- **How parallelizable is the experiment space?** If we can run 8 different
  approaches in parallel, we cover ground 8x faster. If approaches are
  inherently sequential (each builds on the last), parallelism doesn't help.
- **What's the skill ceiling vs. floor?** A competition where everyone gets
  to within 0.1% of each other has low ceiling — we can't differentiate. A
  competition where the leader is 5% ahead has high ceiling but suggests
  there's hidden alpha we'd need to find. Both extremes are warning signs.
- **What's the prize-to-effort ratio?** Compute and API spend will be
  significant. The expected value calculation must be honest.

---

## What makes a good Intelligence Report

A deep-dive Intelligence Report must contain these sections, by name. Don't
improvise the structure — the CEO needs to be able to skim consistently
across reports.

1. **Competition Mechanics** — prize structure, deadline, team size limits,
   submission limits per day, evaluation metric (with the exact formula),
   leaderboard structure (public vs private split, when private is
   revealed). Anything that affects strategy.

2. **Data** — train/test sizes, schema, file formats, target distribution,
   class balance, known leakage risks, label quality, metadata fields. What
   the data actually looks like, not just what the description says.

3. **Public Solution Landscape** — what the top public notebooks are doing,
   what scores they're achieving, what techniques they share, what they all
   miss. Specific notebook URLs and their leaderboard positions where
   possible.

4. **Research Frontier** — what's published in this domain (recent papers
   with citations), which top researchers work on this problem, what the
   state-of-the-art looks like outside Kaggle. Real URLs and paper titles.

5. **Identified Alpha** — 3 to 5 specific bets, with rationale, expected
   impact, and difficulty. This is the heart of the report. Each bet should
   be concrete enough that a worker could start prototyping it tomorrow.

6. **Fit Verdict** — go / no-go / conditional, with explicit reasoning
   tied to the fit-assessment questions above. If conditional, list the
   conditions that would change the verdict.

7. **Resource Estimate** — expected GPU hours, expected API spend (Anthropic
   + any external services), expected calendar time to a competitive
   submission. Be honest. Pad if uncertain, but say what's padded and why.

A 30-page report full of specifics in these sections is the right size.
Don't compress findings into bullet summaries — preserve the detail. If a
subagent found a comparison table of model architectures with benchmark
scores, **include the table**. If a subagent found the exact evaluation
code, **include the code reference**. Specific numbers, specific URLs,
specific names. Nothing should be lost in synthesis.

---

## Phase 1: Initial Scout Deep Dive (Execution)

This is the procedure a research worker follows when assigned a competition
deep-dive task. It is the body of work the worker performs from when it
starts to when it calls `save_report`.

### Step 1 — Gather primary data from Kaggle

Before any external research, fetch these pages directly. They are the
ground truth that anchors everything else.

1. **Competition overview** —
   `web_fetch(url="https://www.kaggle.com/competitions/{slug}", objective="prize structure, deadline, rules, evaluation metric, data description")`
2. **Leaderboard** —
   `web_fetch(url="https://www.kaggle.com/competitions/{slug}/leaderboard", objective="team count, score distribution, top teams", fresh=True)`
3. **Discussion forum** —
   `web_fetch(url="https://www.kaggle.com/competitions/{slug}/discussion", objective="host announcements, common questions, shared insights, approach discussions", fresh=True)`
4. **Public notebooks** —
   `web_fetch(url="https://www.kaggle.com/competitions/{slug}/code", objective="approaches being used, baseline scores, popular techniques")`

Read everything carefully. Take notes mentally — what jumps out, what's
weird, what doesn't add up.

### Step 2 — Reason about research directions

Before spawning subagents, **stop and think**. What kind of problem is
this? What domains does it touch? Where are the biggest unknowns? What
would an expert in this domain immediately want to investigate?

Each research direction you identify becomes a query for a research
subagent. The quality of these queries determines the quality of the
final report. Don't apply a boilerplate template — every competition is
different. A protein function prediction competition demands different
research than a tabular forecasting challenge.

A good research direction is:
- Specific enough to drive concrete investigation
- Important enough that the answer would change our strategy
- Independent enough to be researched without depending on other directions

Aim for 4–6 research directions, each meaty enough to keep a subagent
busy for the full deep_research budget.

### Step 3 — Delegate to research subagents

Call `spawn_subagents` with `subagent_type="research-subagent"`.

Each subagent has access to `web_search`, `web_fetch`, and `deep_research`
(Parallel.ai's autonomous research engine — can iterate for up to 45
minutes, search dozens of sources, read papers, synthesize with citations).
Subagents are highly capable; your job in writing their queries is
**alignment, not micromanagement**.

Each query you write should:
- Give the subagent the competition context (what it is, what you already
  know from Step 1)
- Specify the research direction clearly — what aspect to investigate, why
  it matters for our strategy
- Convey the depth expected — this is not surface-level research; you want
  technical specifics, named papers, named researchers, benchmark numbers,
  code references

Spawn all subagents in one `spawn_subagents` call so they run in parallel.

### Step 4 — Synthesize into the Intelligence Report

This is the culmination of everything. Read every subagent result
carefully. They contain rich, technical findings — specific benchmark
scores, model architectures, training tricks, code repositories,
competitive analysis, metric formulations.

**Synthesis is not summarization.** Synthesis means:
- Connecting findings across subagents (e.g., the evaluation metric
  insight informs which modeling strategies matter most; previous-edition
  winners' approaches inform what's plausible)
- Identifying contradictions between subagent findings and resolving them
- Calling out gaps honestly — if a subagent didn't find what you needed,
  say so
- Producing a document that is **greater than the sum of its parts**

Write the report following the section structure under *What makes a good
Intelligence Report* above. Section by section. Be thorough. Include
specifics. Preserve detail. A 30-page report is fine; a 5-page summary is
not.

If, after writing, you find critical gaps and budget allows, spawn 1–2
follow-up subagents and incorporate their findings before saving.

When the report is complete, call `save_report` with:
- `title`: e.g., `"CAFA 6 Protein Function Prediction — Intelligence Report"`
- `content`: the full markdown report
- `slack_channel`: `"ceo-briefing"`

`save_report` saves the file locally and emits a `report.saved` event. The
workflow layer routes the report from there — you do not need to upload it
to Slack yourself.

---

## Phase 2: Active Strategy Review

*Not yet used — this section will be populated when Kaggle Company has its
first in-progress competition with experimental results to review. For now,
deep dives are always Phase 1 (Initial Scout).*

---

## Phase 3: Post-Mortem

*Not yet used — this section will be populated after Kaggle Company
completes its first competition. For now, deep dives are always Phase 1
(Initial Scout).*

---

## Commissioning a Deep Dive (for VPs)

When the CEO names a competition to deep-dive, your job is to launch a
research worker that will produce a world-class Intelligence Report. The
quality of your commissioning shapes the quality of the result.

### Step 1 — Resolve the competition slug

If the CEO named the competition by friendly name, you may need to
search Kaggle to find the exact slug. The slug is what appears in the
competition URL: `kaggle.com/competitions/{slug}`.

### Step 2 — Spawn the research worker

Call `create_worker_agent` with:
- `competition_slug`: the exact Kaggle slug
- `worker_type`: `"research-worker"`
- `budget_usd`: `50` (research workers spawn multiple subagents that each
  run deep research; this needs room)
- `task`: a natural-language directive that includes:
  - The competition slug and friendly name
  - Any context you already have (what the competition is about, the
    domain, the prize, anything from the discovery phase)
  - The instruction "Conduct a Phase 1 Initial Scout Deep Dive. Load the
    deep-dive skill via `load_skill('deep-dive')` and follow it. Produce
    a complete Competition Intelligence Report and save it via
    `save_report`."

Do **not** rewrite the deep-dive procedure in your task description. The
worker loads the skill itself; your task tells it which competition and
why, not how to do the work.

### Step 3 — Notify the CEO

Post to `#ceo-briefing` confirming the deep dive is underway. Name the
competition. Estimate when results will be ready (typically same day,
depending on subagent runtime).

### Step 4 — Wait

Once the worker is launched, your direct involvement ends until the
report comes back. The workflow layer will trigger you with a new task
when the report is saved (event: `report.saved`). At that point, follow
the *Evaluating a Deep-Dive Report* section below.

---

## Evaluating a Deep-Dive Report (for VPs)

When a research worker delivers a report, the workflow layer triggers you
with a new task whose description includes the report's title and file
path. Your job is to **read the report, form a substantive take, and
present both the report and your take to the CEO.**

You are not a passive forwarder. You have judgment and the CEO wants it.

### What to look for in the report

Read the report end-to-end. As you read, evaluate:

- **Is the Identified Alpha section concrete?** Are the bets specific
  enough that a worker could start prototyping them tomorrow? Vague alpha
  ("try ensembling") is not alpha.
- **Is the Fit Verdict defensible?** Does it tie to the fit-assessment
  questions? Or is it hand-wavy?
- **Are the Resource Estimates honest?** Or do they look like the worker
  padded everything by 10x to be safe (or worse, didn't estimate at all)?
- **Are there specific URLs, paper citations, benchmark numbers?** Or is
  the report all generic ML advice with no Kaggle-specific or
  competition-specific content?
- **What's missing?** Every deep dive has gaps. Calling them out is part
  of your job.

### Form your take

Your take, in 5–10 sentences, should answer:
- **Bottom line: go / no-go / conditional.** Your lean, even if it
  diverges from the worker's verdict.
- **What's the strongest piece of alpha** in the report, and why you
  believe it.
- **What's the biggest concern** — fit, alpha quality, resource cost,
  timeline, something else.
- **What you'd want to know that the report doesn't cover.** If it's
  important, say "I'm requesting a v2 with the following follow-ups" and
  list them.

You can disagree with the worker. You can push back. You can ask for a
v2. The CEO wants a VP-level perspective, not a passthrough.

### Present to the CEO

Use `save_report` to upload the underlying Intelligence Report to
`#ceo-briefing` (this is the same tool the worker used; it will route the
file via the same workflow). Then, in a separate `send_slack_message` to
`#ceo-briefing`, post your take.

Structure your take message like:

```
*VP take on {competition name} Intelligence Report*

Bottom line: {go / no-go / conditional}

Strongest alpha: {one sentence}

Biggest concern: {one sentence}

{2–4 more sentences with the substantive reasoning}

{Optional: requesting v2 with the following follow-ups: ...}
```

That's the deep-dive lifecycle. Worker produces; you evaluate; CEO decides.
