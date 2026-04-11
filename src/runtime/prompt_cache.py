"""Prompt splitting for Anthropic prompt caching.

Splits system prompt into:
- Stable section (constitution + tool definitions) — cached across calls
- Dynamic section (current state, task context) — changes each call
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PromptCacheManager:
    """Manages stable vs dynamic system prompt sections for prompt caching."""

    def __init__(self, constitution_path: str) -> None:
        self._constitution = self._load_constitution(constitution_path)
        self._dynamic_state: str = ""

    def _load_constitution(self, path: str) -> str:
        p = Path(path)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return f"You are an AI agent. Constitution file not found at {path}."

    def set_dynamic_state(self, state_summary: str) -> None:
        """Update the dynamic portion of system prompt."""
        self._dynamic_state = state_summary

    def get_system_blocks(self) -> list[dict[str, Any]]:
        """Return system content blocks with cache_control markers.

        The constitution (stable) gets cache_control so it's cached across calls.
        The dynamic state changes each call and is not cached.
        """
        blocks = [
            {
                "type": "text",
                "text": self._constitution,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if self._dynamic_state:
            blocks.append({
                "type": "text",
                "text": self._dynamic_state,
            })
        return blocks

    @property
    def constitution(self) -> str:
        return self._constitution
