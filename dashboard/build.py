"""Build dashboard data from Kaggle Company transcripts and state files.

Output structure:
    dashboard/data/run.json — single file containing everything for one run
    dashboard/data/agents/{agent_id}.json — full event list per agent
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = PROJECT_ROOT / "transcripts"
STATE = PROJECT_ROOT / "state"
OUTPUT = Path(__file__).resolve().parent / "data"


def discover_agents() -> list[str]:
    """Find all agent IDs from transcript directories."""
    if not TRANSCRIPTS.exists():
        return []
    return sorted(
        d.name for d in TRANSCRIPTS.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def discover_dates() -> list[str]:
    """Find all dates with transcript data."""
    dates = set()
    for agent_dir in TRANSCRIPTS.iterdir():
        if not agent_dir.is_dir():
            continue
        for jsonl in agent_dir.glob("*.jsonl"):
            dates.add(jsonl.stem)
    return sorted(dates)


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def load_state() -> dict[str, dict]:
    """Load all agent state files."""
    states = {}
    state_dir = STATE / "agents"
    if not state_dir.exists():
        return states
    for f in state_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            states[data["id"]] = data
        except (json.JSONDecodeError, KeyError):
            continue
    return states


def load_budget() -> dict:
    """Load daily budget file."""
    path = STATE / "budget" / "daily.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def infer_parent(agent_id: str) -> str | None:
    """Determine parent from agent ID naming convention.

    - vp-001 → ceo
    - worker-{slug} → vp-001
    - sub-{parent_id}-{8hex} → {parent_id}
    """
    if agent_id == "vp-001":
        return "ceo"
    if agent_id.startswith("worker-"):
        return "vp-001"
    if agent_id.startswith("sub-"):
        parts = agent_id.split("-")
        if len(parts) >= 3 and len(parts[-1]) == 8:
            try:
                int(parts[-1], 16)  # verify hex
                return "-".join(parts[1:-1])
            except ValueError:
                pass
    return None


def extract_agent_type(constitution: str) -> str | None:
    """Extract a human-readable agent type from the constitution's first heading.

    Examples:
        '# VP Agent' → 'VP'
        '# Research Worker' → 'Research Worker'
        '# Research Subagent' → 'Research Subagent'
        '# Consolidation Agent Constitution' → 'Consolidation'
    """
    if not constitution:
        return None
    first_line = next((ln for ln in constitution.splitlines() if ln.strip()), "")
    title = first_line.lstrip("#").strip()
    # Strip common suffixes
    for suffix in (" Agent Constitution", " Constitution", " Agent"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break
    return title or None


def short_name(agent_id: str) -> str:
    """Fallback name from agent ID when no constitution-based name is known."""
    if agent_id == "ceo":
        return "CEO"
    if agent_id == "vp-001":
        return "VP"
    if agent_id.startswith("worker-"):
        slug = agent_id[len("worker-"):]
        return slug[:18] + ("…" if len(slug) > 18 else "")
    if agent_id.startswith("sub-"):
        return "Sub " + agent_id[-6:]
    return agent_id


def role_of(agent_id: str, states: dict) -> str:
    """Get role of an agent (from state file or fallback to ID parsing)."""
    if agent_id == "ceo":
        return "ceo"
    state = states.get(agent_id, {})
    role = state.get("role")
    if role:
        return role
    if agent_id == "vp-001":
        return "vp"
    if agent_id.startswith("worker-"):
        return "worker"
    if agent_id.startswith("sub-"):
        return "subagent"
    return "unknown"


# Claude Opus 4.6 pricing per million tokens
PRICING = {
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_write": 1.00,
        "cache_read": 0.08,
    },
}


def cost_from_usage(usage: dict, model: str = "claude-opus-4-6") -> float:
    """Compute API cost from token usage."""
    p = PRICING.get(model, PRICING["claude-opus-4-6"])
    cost = 0.0
    cost += (usage.get("input", 0) / 1_000_000) * p["input"]
    cost += (usage.get("output", 0) / 1_000_000) * p["output"]
    cost += (usage.get("cache_creation", 0) / 1_000_000) * p["cache_write"]
    cost += (usage.get("cache_read", 0) / 1_000_000) * p["cache_read"]
    return cost


def tool_call_summary(name: str, inp: dict) -> str:
    """Short human-readable summary of a tool call."""
    if not isinstance(inp, dict):
        inp = {}
    if name == "web_fetch":
        return f"Fetch: {str(inp.get('url',''))[:80]}"
    if name == "web_search":
        return f"Search: {str(inp.get('query',''))[:80]}"
    if name == "deep_research":
        prompts = inp.get("prompts", [])
        if isinstance(prompts, list) and prompts:
            first = prompts[0]
            if isinstance(first, str):
                return f"Research: {first[:80]}"
            if isinstance(first, dict):
                return f"Research: {first.get('prompt','')[:80]}"
        return f"Research: {str(inp)[:80]}"
    if name == "send_slack_message":
        return f"Slack #{inp.get('channel','')}: {str(inp.get('text',''))[:60]}"
    if name == "create_worker_agent":
        return f"Create worker: {inp.get('competition_slug','')}"
    if name in ("spawn_research", "spawn_subagents"):
        tasks = inp.get("tasks", [])
        return f"Spawn {len(tasks)} subagents"
    if name == "save_report":
        return f"Save report: {str(inp.get('title',''))[:60]}"
    if name == "report_progress":
        return f"Progress: {str(inp.get('summary', inp.get('status','')))[:60]}"
    return f"{name}({str(inp)[:60]})"


def build_run(date: str) -> dict:
    """Build the data for a single run (one date)."""
    agent_ids = discover_agents()
    states = load_state()
    budget = load_budget()
    budget_by_agent = budget.get("by_agent", {})

    # Load all transcripts for this date
    transcripts = {}
    for aid in agent_ids:
        events = load_jsonl(TRANSCRIPTS / aid / f"{date}.jsonl")
        if events:
            transcripts[aid] = events

    # Extract agent_type from each agent's first system_prompt event
    agent_types = {}
    for aid, events in transcripts.items():
        for ev in events:
            if ev.get("type") == "system_prompt":
                t = extract_agent_type(ev.get("constitution", ""))
                if t:
                    agent_types[aid] = t
                break

    # Build agents dict
    agents = {}
    # Add CEO virtual agent
    agents["ceo"] = {
        "id": "ceo",
        "name": "CEO",
        "agent_type": "CEO",
        "role": "ceo",
        "parent": None,
        "children": [],
    }

    for aid in transcripts:
        atype = agent_types.get(aid) or short_name(aid)
        agents[aid] = {
            "id": aid,
            "name": atype,        # Display name = agent type
            "agent_type": atype,
            "role": role_of(aid, states),
            "parent": infer_parent(aid),
            "children": [],
        }

    # Wire up children
    for aid, agent in agents.items():
        parent = agent.get("parent")
        if parent and parent in agents:
            agents[parent]["children"].append(aid)

    # When multiple sibling agents share the same display name, append a position
    # number so the org chart can visually distinguish them.
    for parent_agent in agents.values():
        children = parent_agent.get("children", [])
        if len(children) <= 1:
            continue
        # Count how many siblings share each base name
        name_counts = {}
        for cid in children:
            base = agents[cid].get("name", cid)
            name_counts[base] = name_counts.get(base, 0) + 1
        # Number siblings that have duplicates
        seen = {}
        for cid in children:
            base = agents[cid].get("name", cid)
            if name_counts[base] > 1:
                seen[base] = seen.get(base, 0) + 1
                agents[cid]["name"] = f"{base} #{seen[base]}"

    # Build merged timeline (every event from every agent + synthetic CEO events)
    timeline = []

    for aid, events in transcripts.items():
        # Build a tool_use_id → {tool_name, tool_input} lookup so we can
        # denormalize the originating call info onto each tool_result.
        tool_call_by_id = {}
        for ev in events:
            if ev.get("type") == "tool_call":
                tuid = ev.get("tool_use_id", "")
                if tuid:
                    inp = ev.get("tool_input", {})
                    if isinstance(inp, dict):
                        inp = {k: v for k, v in inp.items() if k != "_agent_id"}
                    tool_call_by_id[tuid] = {
                        "tool_name": ev.get("tool_name", ""),
                        "tool_input": inp,
                    }

        for ev in events:
            ts = ev.get("timestamp", "")[:23]
            etype = ev.get("type", "")
            entry = {
                "ts": ts,
                "agent_id": aid,
                "type": etype,
                "turn": ev.get("turn"),
            }
            if etype == "task_start":
                entry["summary"] = ev.get("task", "")[:200]
                entry["trigger"] = ev.get("trigger", "")
                # Synthesize CEO event when VP gets a task from CEO
                if aid == "vp-001" and ev.get("trigger") in ("ceo_message", "first_boot"):
                    timeline.append({
                        "ts": ts,
                        "agent_id": "ceo",
                        "type": "ceo_message",
                        "summary": ev.get("task", "")[:200],
                        "trigger": ev.get("trigger"),
                    })
            elif etype == "task_end":
                entry["summary"] = f"Done in {ev.get('turns', '?')} turns"
            elif etype == "api_response":
                entry["summary"] = ev.get("text", "")[:150]
                entry["usage"] = ev.get("usage", {})
                entry["stop_reason"] = ev.get("stop_reason", "")
            elif etype == "tool_call":
                inp = ev.get("tool_input", {})
                if isinstance(inp, dict):
                    inp = {k: v for k, v in inp.items() if k != "_agent_id"}
                entry["tool_name"] = ev.get("tool_name", "")
                entry["summary"] = tool_call_summary(entry["tool_name"], inp)
                entry["tool_use_id"] = ev.get("tool_use_id", "")
            elif etype == "tool_result":
                content = ev.get("content", "")
                tuid = ev.get("tool_use_id", "")
                entry["tool_use_id"] = tuid
                entry["is_error"] = ev.get("is_error", False)
                entry["content_length"] = len(content)
                # Stamp the originating call's name + input onto the result
                call_info = tool_call_by_id.get(tuid, {})
                entry["tool_name"] = call_info.get("tool_name", "")
                # Compact summary: tool name + status + content preview
                status = "ERROR: " if entry["is_error"] else ""
                if entry["tool_name"]:
                    entry["summary"] = f"{entry['tool_name']} → {status}{content[:100]}"
                else:
                    entry["summary"] = f"{status}{content[:100]}"
            elif etype == "reflection":
                entry["summary"] = ev.get("content", "")[:200]
            elif etype == "system_prompt":
                # Skip from main timeline — too noisy
                continue
            elif etype == "api_request_full":
                # Skip from main timeline — duplicates api_response
                continue

            timeline.append(entry)

    timeline.sort(key=lambda e: e.get("ts", ""))

    # Per-agent summaries
    summaries = {}
    total_run_cost = 0.0
    for aid in agents:
        if aid == "ceo":
            continue
        events = transcripts.get(aid, [])
        s = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "api_calls": 0,
            "tools_used": {},
            "task": "",
            "trigger": "",
            "turns": 0,
            "total_cost": 0.0,
        }
        for e in events:
            t = e.get("type")
            if t == "task_start":
                s["task"] = e.get("task", "")
                s["trigger"] = e.get("trigger", "")
            elif t == "task_end":
                s["turns"] = e.get("turns", 0)
            elif t == "api_response":
                s["api_calls"] += 1
                u = e.get("usage", {})
                s["input_tokens"] += u.get("input", 0)
                s["output_tokens"] += u.get("output", 0)
                s["cache_read"] += u.get("cache_read", 0)
                s["cache_write"] += u.get("cache_creation", 0)
                model = e.get("model", "claude-opus-4-6")
                s["total_cost"] += cost_from_usage(u, model)
            elif t == "tool_call":
                name = e.get("tool_name", "?")
                s["tools_used"][name] = s["tools_used"].get(name, 0) + 1
        summaries[aid] = s
        total_run_cost += s["total_cost"]

    # CEO summary: list of messages
    ceo_messages = [e for e in timeline if e.get("agent_id") == "ceo"]
    summaries["ceo"] = {
        "messages": ceo_messages,
        "task": "Sole human operator. Sends directives via Slack.",
        "trigger": "",
        "turns": len(ceo_messages),
        "total_cost": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "api_calls": 0,
        "tools_used": {},
    }

    return {
        "date": date,
        "total_cost": total_run_cost,
        "agents": agents,
        "timeline": timeline,
        "summaries": summaries,
    }


def build_agent_detail(agent_id: str, date: str) -> dict:
    """Build the per-agent detail file (full events with content)."""
    events = load_jsonl(TRANSCRIPTS / agent_id / f"{date}.jsonl")
    detail = {"agent_id": agent_id, "date": date, "events": []}

    # Build tool_use_id → call info lookup so we can stamp it onto results
    tool_call_by_id = {}
    for ev in events:
        if ev.get("type") == "tool_call":
            tuid = ev.get("tool_use_id", "")
            if tuid:
                inp = ev.get("tool_input", {})
                if isinstance(inp, dict):
                    inp = {k: v for k, v in inp.items() if k != "_agent_id"}
                tool_call_by_id[tuid] = {
                    "tool_name": ev.get("tool_name", ""),
                    "tool_input": inp,
                    "turn": ev.get("turn"),
                    "ts": ev.get("timestamp", "")[:23],
                }

    for ev in events:
        ts = ev.get("timestamp", "")[:23]
        etype = ev.get("type", "")
        entry = {"ts": ts, "type": etype, "turn": ev.get("turn")}

        if etype == "task_start":
            entry["task"] = ev.get("task", "")
            entry["trigger"] = ev.get("trigger", "")
        elif etype == "task_end":
            entry["turns"] = ev.get("turns", 0)
            entry["result_preview"] = ev.get("result_preview", "")[:1000]
        elif etype == "system_prompt":
            entry["model"] = ev.get("model", "")
            entry["constitution"] = ev.get("constitution", "")
            entry["dynamic_state"] = ev.get("dynamic_state", "")
        elif etype == "api_request_full":
            entry["model"] = ev.get("model", "")
            entry["max_tokens"] = ev.get("max_tokens", 0)
            entry["system"] = ev.get("system", [])
            entry["messages"] = ev.get("messages", [])
            # Just tool names, not full schemas (those are huge)
            entry["tools"] = [
                {"name": t.get("name", "?"), "description": (t.get("description", "") or "")[:200]}
                for t in ev.get("tools", [])
            ]
            entry["message_count"] = len(ev.get("messages", []))
            entry["tool_count"] = len(ev.get("tools", []))
        elif etype == "api_response":
            entry["model"] = ev.get("model", "")
            entry["stop_reason"] = ev.get("stop_reason", "")
            entry["usage"] = ev.get("usage", {})
            entry["text"] = ev.get("text", "")
            entry["raw_content_blocks"] = ev.get("raw_content_blocks", [])
        elif etype == "tool_call":
            inp = ev.get("tool_input", {})
            if isinstance(inp, dict):
                inp = {k: v for k, v in inp.items() if k != "_agent_id"}
            entry["tool_name"] = ev.get("tool_name", "")
            entry["tool_use_id"] = ev.get("tool_use_id", "")
            entry["tool_input"] = inp
        elif etype == "tool_result":
            content = ev.get("content", "")
            tuid = ev.get("tool_use_id", "")
            entry["tool_use_id"] = tuid
            entry["is_error"] = ev.get("is_error", False)
            entry["content"] = content  # FULL content, not truncated
            entry["content_length"] = len(content)
            # Stamp originating call info so the modal can show what was called
            call_info = tool_call_by_id.get(tuid, {})
            entry["tool_name"] = call_info.get("tool_name", "")
            entry["tool_input"] = call_info.get("tool_input", {})
            entry["call_ts"] = call_info.get("ts", "")
        elif etype == "reflection":
            entry["content"] = ev.get("content", "")

        detail["events"].append(entry)

    return detail


def build_all(verbose: bool = True) -> dict | None:
    """Build all dashboard data files. Returns the index dict, or None if no data."""
    from datetime import datetime, timezone

    dates = discover_dates()
    if not dates:
        if verbose:
            print("No transcript data found.")
        return None

    if verbose:
        print(f"  Dates found: {dates}")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "agents").mkdir(parents=True, exist_ok=True)

    runs = {}
    for date in dates:
        run = build_run(date)
        runs[date] = run
        if verbose:
            print(f"  {date}: {len(run['agents'])} agents, {len(run['timeline'])} events")

    default_date = max(dates, key=lambda d: len(runs[d]["timeline"]))

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dates": dates,
        "default_date": default_date,
        "runs": runs,
    }
    index_path = OUTPUT / "index.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    # Per-agent detail files
    agent_ids = discover_agents()
    for date in dates:
        for aid in agent_ids:
            tpath = TRANSCRIPTS / aid / f"{date}.jsonl"
            if not tpath.exists():
                continue
            detail = build_agent_detail(aid, date)
            fname = f"{aid}__{date}.json"
            fpath = OUTPUT / "agents" / fname
            fpath.write_text(json.dumps(detail), encoding="utf-8")

    if verbose:
        print(f"  Wrote {index_path.relative_to(PROJECT_ROOT) if PROJECT_ROOT in index_path.parents else index_path}")
        print("Done.")

    return index


def watched_paths() -> list[Path]:
    """Return all paths the build depends on (for change detection)."""
    paths = []
    if TRANSCRIPTS.exists():
        for jsonl in TRANSCRIPTS.rglob("*.jsonl"):
            paths.append(jsonl)
    if STATE.exists():
        for jf in STATE.rglob("*.json"):
            paths.append(jf)
    return paths


def max_mtime() -> float:
    """Return the max mtime across all watched files. 0 if no files."""
    paths = watched_paths()
    if not paths:
        return 0.0
    try:
        return max(p.stat().st_mtime for p in paths)
    except FileNotFoundError:
        return 0.0


def main():
    print("Building dashboard data...")
    result = build_all(verbose=True)
    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
