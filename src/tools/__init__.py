"""Tool registry for agent tool dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


class AgentRole(Enum):
    VP = "vp"
    WORKER = "worker"
    SUBAGENT = "subagent"
    CONSOLIDATION = "consolidation"


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, str]]
    allowed_roles: set[AgentRole] = field(default_factory=lambda: set(AgentRole))
    input_examples: list[dict[str, Any]] | None = None


class ToolRegistry:
    """Central registry of all tools available to agents."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def get_tools_for_role(self, role: AgentRole) -> list[dict[str, Any]]:
        """Return Claude API tool-use format for tools accessible by this role.

        Includes strict mode and input_examples on all tools.
        Adds cache_control to the last tool for prompt caching.
        """
        tools = []
        for tool in self._tools.values():
            if role in tool.allowed_roles:
                tool_def: dict[str, Any] = {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                if tool.input_examples:
                    tool_def["input_examples"] = tool.input_examples
                tools.append(tool_def)

        # Add cache_control to the last tool for prompt caching
        if tools:
            tools[-1]["cache_control"] = {"type": "ephemeral"}

        return tools

    def get_definition(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_all(self) -> list[str]:
        return list(self._tools.keys())
