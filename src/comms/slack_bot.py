"""Slack bot using Socket Mode for CEO communication.

Channels:
- #ceo-briefing: VP posts daily summaries, CEO responds with direction
- #decisions: Pending decisions with approve/reject buttons
- #alerts: Urgent notifications
- #vp-agent: Direct conversation with VP
- #research: Research findings
- #system-log: Orchestrator events
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger("kaggle-company.slack")

# Standard channels
CHANNELS = {
    "ceo_briefing": "ceo-briefing",
    "decisions": "decisions",
    "alerts": "alerts",
    "vp_agent": "vp-agent",
    "research": "research",
    "system_log": "system-log",
}


class SlackBot:
    """Slack integration using Socket Mode."""

    def __init__(self, settings: Settings) -> None:
        self._app = AsyncApp(token=settings.slack_bot_token)
        self._handler = AsyncSocketModeHandler(self._app, settings.slack_app_token)
        self._channel_ids: dict[str, str] = {}
        self._message_callback: Callable[[str, str, str, str | None], Coroutine] | None = None
        self._register_handlers()

    def set_message_callback(
        self,
        callback: Callable[[str, str, str, str | None], Coroutine],
    ) -> None:
        """Set callback for incoming messages: (channel, user, text, thread_ts)."""
        self._message_callback = callback

    async def start(self, clear_history: bool = False) -> None:
        """Start Socket Mode connection and ensure channels exist.

        Args:
            clear_history: If True, delete all messages from managed channels on startup.
        """
        await self._ensure_channels()
        if clear_history:
            await self._clear_channels()
        await self._handler.connect_async()
        logger.info("Slack bot connected")

    async def stop(self) -> None:
        """Disconnect."""
        await self._handler.close_async()
        logger.info("Slack bot disconnected")

    async def _ensure_channels(self) -> None:
        """Create channels if they don't exist, join them, cache their IDs."""
        client = self._app.client
        try:
            # List all channels including ones the bot isn't in
            existing: dict[str, str] = {}
            cursor = None
            while True:
                kwargs = {"types": "public_channel", "limit": 200, "exclude_archived": True}
                if cursor:
                    kwargs["cursor"] = cursor
                result = await client.conversations_list(**kwargs)
                for c in result.get("channels", []):
                    existing[c["name"]] = c["id"]
                cursor = result.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            logger.info("Found %d existing channels in workspace", len(existing))

            for key, name in CHANNELS.items():
                if name in existing:
                    channel_id = existing[name]
                    self._channel_ids[key] = channel_id
                    # Join the channel if not already a member
                    try:
                        await client.conversations_join(channel=channel_id)
                    except Exception:
                        pass  # Already a member
                    logger.info("Using existing channel #%s (%s)", name, channel_id)
                else:
                    try:
                        resp = await client.conversations_create(name=name)
                        channel_id = resp["channel"]["id"]
                        self._channel_ids[key] = channel_id
                        logger.info("Created channel #%s (%s)", name, channel_id)
                    except Exception as e:
                        logger.error("Could not create channel #%s: %s", name, e)

            logger.info("Channel IDs cached: %s", {k: v[:8] + "..." for k, v in self._channel_ids.items()})
        except Exception as e:
            logger.error("Failed to setup channels: %s", e, exc_info=True)

    async def _clear_channels(self) -> None:
        """Delete all messages from managed channels for a clean start."""
        client = self._app.client
        for key, channel_id in self._channel_ids.items():
            try:
                deleted = 0
                cursor = None
                while True:
                    kwargs: dict[str, Any] = {"channel": channel_id, "limit": 200}
                    if cursor:
                        kwargs["cursor"] = cursor
                    result = await client.conversations_history(**kwargs)
                    messages = result.get("messages", [])
                    if not messages:
                        break
                    for msg in messages:
                        ts = msg.get("ts")
                        if not ts:
                            continue
                        try:
                            await client.chat_delete(channel=channel_id, ts=ts)
                            deleted += 1
                        except Exception:
                            pass  # Some messages (system, etc.) can't be deleted
                    cursor = result.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
                if deleted:
                    logger.info("Cleared %d messages from #%s", deleted, CHANNELS.get(key, key))
            except Exception as e:
                logger.warning("Could not clear #%s: %s", CHANNELS.get(key, key), e)

    def get_channel_id(self, key: str) -> str | None:
        """Get channel ID by logical key (e.g., 'ceo_briefing')."""
        return self._channel_ids.get(key)

    async def post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
    ) -> str | None:
        """Post a message. Returns message ts for threading."""
        try:
            kwargs: dict[str, Any] = {"channel": channel, "text": text}
            if blocks:
                kwargs["blocks"] = blocks
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            result = await self._app.client.chat_postMessage(**kwargs)
            return result.get("ts")
        except Exception as e:
            logger.error("Failed to post to %s: %s", channel, e)
            return None

    async def post_to(self, channel_key: str, text: str, **kwargs: Any) -> str | None:
        """Post to a named channel. Accepts key ('ceo_briefing'), name ('ceo-briefing'), or channel ID."""
        # Try direct key match
        channel_id = self._channel_ids.get(channel_key)
        if not channel_id:
            # Try with underscores replaced by hyphens (name -> key lookup)
            normalized = channel_key.replace("-", "_")
            channel_id = self._channel_ids.get(normalized)
        if not channel_id:
            # Try reverse lookup: maybe they passed the channel name
            for key, name in CHANNELS.items():
                if name == channel_key:
                    channel_id = self._channel_ids.get(key)
                    break
        if not channel_id:
            # Maybe it's already a channel ID (starts with C)
            if channel_key.startswith("C"):
                channel_id = channel_key
        if not channel_id:
            logger.warning("Channel key '%s' not found in %s", channel_key, list(self._channel_ids.keys()))
            return None
        return await self.post_message(channel_id, text, **kwargs)

    async def post_briefing(self, briefing: dict[str, Any]) -> str | None:
        """Post a formatted briefing to #ceo-briefing."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": briefing.get("title", "Daily Briefing")},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": briefing.get("summary", "No summary.")},
            },
        ]

        # Competition status fields
        competitions = briefing.get("competitions", [])
        if competitions:
            fields = []
            for comp in competitions[:10]:
                fields.append({
                    "type": "mrkdwn",
                    "text": f"*{comp['name']}*\nRank: {comp.get('rank', 'N/A')} | Score: {comp.get('score', 'N/A')}",
                })
            blocks.append({"type": "section", "fields": fields})

        # Budget
        if "budget" in briefing:
            b = briefing["budget"]
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Budget*: ${b.get('spent', 0):.2f} / ${b.get('limit', 0):.2f} today",
                },
            })

        text = briefing.get("summary", "Daily briefing")
        return await self.post_to("ceo_briefing", text, blocks=blocks)

    async def post_decision_request(self, decision: dict[str, Any]) -> str | None:
        """Post a decision request to #decisions with action buttons."""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Decision Required* ({decision.get('urgency', 'normal')} priority)\n\n"
                            f"From: `{decision.get('from_agent', 'unknown')}`\n"
                            f"Type: {decision.get('type', 'general')}\n\n"
                            f"{decision.get('summary', 'No details.')}",
                },
            },
        ]

        if "recommendation" in decision:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recommendation*: {decision['recommendation']}",
                },
            })

        # Action buttons
        options = decision.get("options", ["approve", "reject"])
        elements = []
        for opt in options:
            style = "primary" if opt == "approve" else "danger" if opt == "reject" else None
            btn = {
                "type": "button",
                "text": {"type": "plain_text", "text": opt.capitalize()[:75]},
                "action_id": f"decision_{decision.get('id', 'unknown')}_{opt}",
                "value": json.dumps({"decision_id": decision.get("id"), "choice": opt}),
            }
            if style:
                btn["style"] = style
            elements.append(btn)

        blocks.append({"type": "actions", "elements": elements})

        text = f"Decision: {decision.get('summary', 'Pending')}"
        return await self.post_to("decisions", text, blocks=blocks)

    async def post_alert(self, message: str, urgency: str = "normal") -> str | None:
        """Post an alert to #alerts."""
        prefix = {"low": "", "normal": ":warning:", "high": ":rotating_light:", "critical": ":fire:"}
        text = f"{prefix.get(urgency, '')} {message}"
        return await self.post_to("alerts", text)

    async def post_system_log(self, message: str) -> str | None:
        """Post to #system-log."""
        return await self.post_to("system_log", message)

    async def create_competition_channel(self, competition_slug: str) -> str | None:
        """Create a channel for a competition worker."""
        name = f"comp-{competition_slug[:60]}"
        try:
            resp = await self._app.client.conversations_create(name=name)
            channel_id = resp["channel"]["id"]
            self._channel_ids[f"comp_{competition_slug}"] = channel_id
            logger.info("Created competition channel #%s", name)
            return channel_id
        except Exception as e:
            logger.warning("Could not create channel #%s: %s", name, e)
            return None

    def _register_handlers(self) -> None:
        """Register Slack event handlers."""

        @self._app.event("message")
        async def handle_message(event: dict, say: Callable) -> None:
            # Ignore bot messages
            if event.get("bot_id") or event.get("subtype"):
                return

            channel = event.get("channel", "")
            user = event.get("user", "")
            text = event.get("text", "")
            thread_ts = event.get("thread_ts")

            logger.info("Message from %s in %s: %s", user, channel, text[:100])

            if self._message_callback:
                await self._message_callback(channel, user, text, thread_ts)

        @self._app.action({"action_id": "decision_.*"})
        async def handle_decision_action(ack: Callable, action: dict, say: Callable) -> None:
            await ack()
            try:
                value = json.loads(action.get("value", "{}"))
                decision_id = value.get("decision_id")
                choice = value.get("choice")
                logger.info("Decision %s: %s", decision_id, choice)
                await say(f"Decision `{decision_id}` resolved: *{choice}*")
                if self._message_callback:
                    await self._message_callback(
                        "", "", f"decision_response:{decision_id}:{choice}", None
                    )
            except Exception as e:
                logger.error("Error handling decision action: %s", e)
