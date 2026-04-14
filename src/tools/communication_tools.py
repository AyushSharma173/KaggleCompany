"""Communication tools: Slack messaging, CEO interaction, inter-agent messaging."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.tools import AgentRole, ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from src.comms.inter_agent import CommHub
    from src.comms.slack_bot import SlackBot
    from src.memory.state_store import StateStore
    from src.orchestrator.events import EventBus

logger = logging.getLogger("kaggle-company.tools.comms")


REPORT_DIR = Path("reports")


def make_communication_tools(
    slack_bot: SlackBot | None,
    comm_hub: CommHub | None,
    state_store: StateStore | None,
    event_bus: EventBus | None = None,
) -> list[ToolDefinition]:
    """Create communication tools with bound references."""

    async def save_report(params: dict[str, Any]) -> str:
        """Save a full report to file and emit a `report.saved` event.

        The tool no longer uploads to Slack directly. It writes the file to
        disk and emits `report.saved` on the event bus. The workflow layer
        decides where the report goes from there (notify VP, upload to a
        channel, etc.) — see `src/workflows/handlers.py` and the
        registrations in `main.py`.
        """
        title = params.get("title", "report")
        content = params.get("content", "")
        slack_channel = params.get("slack_channel", "")
        agent_id = params.get("_agent_id", "unknown")

        if not content:
            return "Error: content is required"

        # Sanitize title for filename
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title)
        safe_title = safe_title.strip().replace(" ", "-").lower()[:80]
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{safe_title}-{timestamp}.md"

        # Save full report locally
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        filepath = REPORT_DIR / filename
        filepath.write_text(content, encoding="utf-8")

        result = f"Report saved to {filepath} ({len(content)} chars)"

        # Emit event so the workflow layer can route it.
        if event_bus is not None:
            await event_bus.emit(
                "report.saved",
                {
                    "agent_id": agent_id,
                    "title": title,
                    "filepath": str(filepath),
                    "filename": filename,
                    "content": content,
                    "slack_channel": slack_channel,
                },
            )
            result += " | report.saved event emitted"
        else:
            result += " | (no event bus configured — file saved locally only)"

        return result

    async def send_slack_message(params: dict[str, Any]) -> str:
        """Post a message to a Slack channel."""
        channel = params.get("channel", "")
        text = params.get("text", "")
        thread_ts = params.get("thread_ts")

        if not channel or not text:
            return "Error: channel and text are required"
        if not slack_bot:
            return "Slack not connected. Message logged: " + text

        # Slack message limit is ~4000 chars. If longer, truncate and warn.
        if len(text) > 3900:
            truncated = text[:3900] + "\n\n[Message truncated — use save_report for long content]"
            text = truncated

        ts = await slack_bot.post_to(channel, text, thread_ts=thread_ts)
        if ts:
            return f"Message posted to #{channel} (ts={ts})"
        # Try direct channel ID
        ts = await slack_bot.post_message(channel, text, thread_ts=thread_ts)
        return f"Message posted (ts={ts})" if ts else "Failed to post message"

    async def report_progress(params: dict[str, Any]) -> str:
        """Report progress on current work to the VP/system-log."""
        summary = params.get("summary", "")
        metrics_raw = params.get("metrics", "")
        agent_id = params.get("_agent_id", "unknown")

        if not summary:
            return "Error: summary is required"

        message = f"*Progress report from `{agent_id}`*\n{summary}"
        if metrics_raw:
            try:
                parsed = json.loads(metrics_raw)
                if isinstance(parsed, dict):
                    metrics_str = " | ".join(f"{k}: {v}" for k, v in parsed.items())
                else:
                    metrics_str = str(parsed)
            except json.JSONDecodeError:
                metrics_str = metrics_raw
            message += f"\n_{metrics_str}_"

        if slack_bot:
            await slack_bot.post_to("system_log", message)
        return f"Progress reported: {summary}"

    async def request_ceo_decision(params: dict[str, Any]) -> str:
        """Submit a decision request for CEO approval."""
        question = params.get("question", "")
        options = params.get("options", ["approve", "reject"])
        urgency = params.get("urgency", "normal")
        recommendation = params.get("recommendation", "")
        agent_id = params.get("_agent_id", "unknown")

        if not question:
            return "Error: question is required"

        decision_id = f"dec-{int(time.time())}"
        decision = {
            "id": decision_id,
            "from_agent": agent_id,
            "type": "general",
            "summary": question,
            "options": options,
            "recommendation": recommendation,
            "urgency": urgency,
            "created_at": time.time(),
            "status": "pending",
        }

        # Store in state
        if state_store:
            state_store.update("decisions", "pending", lambda d: {
                **d,
                "items": d.get("items", []) + [decision],
            }, default={"items": []})

        # Post to Slack
        if slack_bot:
            await slack_bot.post_decision_request(decision)

        return json.dumps({
            "decision_id": decision_id,
            "status": "pending",
            "message": "Decision request submitted to CEO",
        })

    async def request_budget_increase(params: dict[str, Any]) -> str:
        """Request additional budget from CEO."""
        amount = params.get("amount_usd", 0)
        reason = params.get("reason", "")
        agent_id = params.get("_agent_id", "unknown")

        if not amount or not reason:
            return "Error: amount_usd and reason are required"

        return await request_ceo_decision({
            "question": f"Budget increase request from {agent_id}: ${amount:.2f}\nReason: {reason}",
            "options": ["approve", "reject", "approve_partial"],
            "urgency": "normal",
            "recommendation": f"Approve ${amount:.2f} for {agent_id}",
            "_agent_id": agent_id,
        })

    async def send_to_agent(params: dict[str, Any]) -> str:
        """Send a message to another agent."""
        to_agent = params.get("to_agent", "")
        message = params.get("message", "")
        msg_type = params.get("type", "message")
        from_agent = params.get("_agent_id", "unknown")

        if not to_agent or not message:
            return "Error: to_agent and message are required"
        if not comm_hub:
            return "Inter-agent comms not available"

        success = await comm_hub.send(from_agent, to_agent, msg_type, {"message": message})
        return f"Message sent to {to_agent}" if success else f"Failed: agent {to_agent} not found"

    return [
        ToolDefinition(
            name="save_report",
            description=(
                "Save a full report to a markdown file in reports/ and emit a `report.saved` event "
                "on the workflow event bus. Use this for Competition Intelligence Reports and any "
                "long-form output. The workflow layer routes the report from there (notify the VP, "
                "upload to a Slack channel, etc.) — you do not need to upload it yourself. "
                "Nothing gets truncated."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Report title (e.g., 'Hull Tactical Intelligence Report')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full report content in markdown format — include everything, no truncation",
                    },
                    "slack_channel": {
                        "type": "string",
                        "description": "Slack channel to upload file to (e.g., 'ceo_briefing'). Omit to save locally only.",
                    },
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
            handler=save_report,
            allowed_roles={AgentRole.VP, AgentRole.WORKER},
        ),
        ToolDefinition(
            name="send_slack_message",
            description="Post a message to a Slack channel. Use channel keys like 'ceo_briefing', 'alerts', 'research', or a channel ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel key or ID"},
                    "text": {"type": "string", "description": "Message text (supports markdown)"},
                    "thread_ts": {"type": "string", "description": "Thread timestamp for replies"},
                },
                "required": ["channel", "text"],
                "additionalProperties": False,
            },
            handler=send_slack_message,
            allowed_roles={AgentRole.VP, AgentRole.WORKER},
        ),
        ToolDefinition(
            name="report_progress",
            description="Report progress on current work. Posts to system log.",
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Progress summary"},
                    "metrics": {"type": "string", "description": "Key metrics as JSON string (optional)"},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
            handler=report_progress,
            allowed_roles={AgentRole.VP, AgentRole.WORKER},
        ),
        # request_ceo_decision — disabled for now
        # request_budget_increase — disabled for now
        # send_to_agent — disabled for now
    ]


def register_communication_tools(
    registry: ToolRegistry,
    slack_bot: SlackBot | None = None,
    comm_hub: CommHub | None = None,
    state_store: StateStore | None = None,
    event_bus: EventBus | None = None,
) -> None:
    """Register all communication tools."""
    for tool in make_communication_tools(slack_bot, comm_hub, state_store, event_bus):
        registry.register(tool)
