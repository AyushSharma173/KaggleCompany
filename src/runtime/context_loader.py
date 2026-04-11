"""Builds the messages array for Claude API calls.

Manages conversation history with a sliding window to stay within
context limits. Keeps the most recent N messages plus pinned messages.
"""

from __future__ import annotations

import json
from typing import Any


class ContextLoader:
    """Builds and manages the conversation message history for an agent."""

    def __init__(self, max_messages: int = 100) -> None:
        self._messages: list[dict[str, Any]] = []
        self._pinned: list[dict[str, Any]] = []
        self._max_messages = max_messages

    def build_messages(self) -> list[dict[str, Any]]:
        """Return pinned + recent messages, trimmed to fit."""
        recent = self._messages[-self._max_messages:] if self._messages else []
        # Pinned messages go first, then recent history
        return self._pinned + recent

    def append_user(self, content: str) -> None:
        """Add a user message."""
        self._messages.append({"role": "user", "content": content})

    def append_assistant(self, content: list[dict[str, Any]] | str) -> None:
        """Add an assistant message (can be text or content blocks)."""
        if isinstance(content, str):
            self._messages.append({"role": "assistant", "content": content})
        else:
            self._messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        """Add tool result blocks as a user message."""
        self._messages.append({"role": "user", "content": results})

    def append_reflection(self, reflection: str) -> None:
        """Add a reflection as a user message (self-directed context)."""
        self._messages.append({
            "role": "user",
            "content": f"[Self-reflection] {reflection}",
        })

    def pin_message(self, message: dict[str, Any]) -> None:
        """Pin a message so it's always included at the start."""
        self._pinned.append(message)

    def clear_history(self) -> None:
        """Clear conversation history (keeps pinned messages)."""
        self._messages.clear()

    def message_count(self) -> int:
        """Return total message count (pinned + history)."""
        return len(self._pinned) + len(self._messages)

    def compact(self, summary: str) -> None:
        """Replace all history with a summary message, keeping pinned."""
        self._messages = [{"role": "user", "content": f"[Context summary] {summary}"}]
