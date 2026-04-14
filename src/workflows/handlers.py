"""Workflow event handlers.

Each `make_*_handler` function is a factory that captures dependencies
(slack_bot, manager, etc.) via closure and returns an async handler. The
handler signature is always `async def handler(payload: dict[str, Any]) -> None`.

Handlers are wired up in `src/main.py` after the orchestrator and event bus
exist. Switching workflow modes is a matter of changing which handlers are
subscribed in main.py — V2 will move this into `workflows/*.yaml`.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.comms.slack_bot import SlackBot
    from src.orchestrator.manager import AgentManager

logger = logging.getLogger("kaggle-company.workflows")


def make_upload_report_to_slack_handler(slack_bot: SlackBot | None):
    """Pure-dispatcher handler: uploads the report file directly to Slack.

    This is what `save_report` used to do inline before the workflow layer
    existed. Kept here so the routing decision is a workflow-layer concern,
    not a tool implementation detail. Toggle this on (and `notify_vp` off)
    in main.py to restore pre-workflow-layer behavior.
    """

    async def upload_report_to_slack(payload: dict[str, Any]) -> None:
        slack_channel = payload.get("slack_channel")
        if not slack_channel:
            logger.info(
                "[upload_report_to_slack] no slack_channel in payload; skipping"
            )
            return
        if slack_bot is None:
            logger.warning(
                "[upload_report_to_slack] no slack_bot configured; skipping"
            )
            return

        title = payload["title"]
        content = payload["content"]
        filename = payload["filename"]

        # Resolve channel id (same lookup logic the old save_report used)
        channel_id = slack_bot.get_channel_id(slack_channel)
        if not channel_id:
            normalized = slack_channel.replace("-", "_")
            channel_id = slack_bot.get_channel_id(normalized)
        if not channel_id and slack_channel.startswith("C"):
            channel_id = slack_channel
        if not channel_id:
            logger.warning(
                "[upload_report_to_slack] channel '%s' not found", slack_channel
            )
            return

        try:
            await slack_bot._app.client.files_upload_v2(
                channel=channel_id,
                content=content,
                filename=filename,
                title=title,
                initial_comment=f"Competition Intelligence Report: *{title}*",
            )
            logger.info(
                "[upload_report_to_slack] uploaded '%s' to #%s",
                title,
                slack_channel,
            )
        except Exception:
            logger.exception(
                "[upload_report_to_slack] failed to upload '%s' to Slack", title
            )

    return upload_report_to_slack


def make_notify_vp_for_review_handler(manager: AgentManager):
    """Default V1 handler: spawn a task on the VP to read the report and
    present both the report and the VP's take to the CEO.

    The VP does not have a file-read tool, so the full report content is
    embedded directly in the task description. The VP reads it as part of
    its prompt, forms a take, and posts both file and take to the CEO
    channel using its existing tools (`save_report`, `send_slack_message`).

    Known limitation: `manager.run_agent_task` will cancel any task the VP
    is currently running. In V1 this is acceptable because the VP is almost
    always idle by the time a research worker delivers a report (the
    research worker takes minutes-to-hours, long after the VP finished its
    commissioning task). A V2 improvement is to queue the task instead of
    cancelling.
    """

    async def notify_vp_for_review(payload: dict[str, Any]) -> None:
        author = payload.get("agent_id", "unknown")
        title = payload["title"]
        filepath = payload["filepath"]
        content = payload["content"]
        slack_channel = payload.get("slack_channel") or "ceo-briefing"

        task_description = (
            f"A research worker (`{author}`) has just delivered a deep-dive "
            f"Competition Intelligence Report titled **{title}**.\n\n"
            f"The full report is saved at `{filepath}`. The complete content "
            f"is included below for you to read.\n\n"
            f"Your job: read the report end-to-end, form a substantive take on "
            f"it (what looks like real alpha vs noise, what's missing, your "
            f"go/no-go lean and why), and then present both the report file "
            f"and your take to the CEO in `#{slack_channel}`.\n\n"
            f"Use `save_report` (with the same title and content) to deliver "
            f"the underlying file via the workflow layer. Then use "
            f"`send_slack_message` to `#{slack_channel}` to post your take. "
            f"Follow the **Evaluating a Deep-Dive Report** section of the "
            f"deep-dive skill — load it via `load_skill(skill_name='deep-dive')`.\n\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"{content}"
        )

        logger.info(
            "[notify_vp_for_review] triggering vp-001 to review report '%s' "
            "from %s (%d chars)",
            title,
            author,
            len(content),
        )
        await manager.run_agent_task(
            agent_id="vp-001",
            task=task_description,
            trigger="report.saved",
        )

    return notify_vp_for_review
