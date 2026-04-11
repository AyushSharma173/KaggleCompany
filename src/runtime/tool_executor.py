"""Tool dispatch: validates, checks access, enforces budget, executes tools."""

from __future__ import annotations

import logging
import traceback
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.budget.tracker import BudgetTracker
    from src.tools import AgentRole, ToolRegistry

logger = logging.getLogger("kaggle-company.tools")


class ToolExecutor:
    """Dispatches tool calls from Claude API responses."""

    def __init__(
        self,
        role: AgentRole,
        agent_id: str,
        registry: ToolRegistry,
        budget_tracker: BudgetTracker | None = None,
    ) -> None:
        self._role = role
        self._agent_id = agent_id
        self._registry = registry
        self._budget = budget_tracker

    def check_access(self, tool_name: str) -> bool:
        """Check if this agent role can use this tool."""
        tool_def = self._registry.get(tool_name)
        if tool_def is None:
            return False
        return self._role in tool_def.allowed_roles

    async def execute_batch(self, content_blocks: list[Any]) -> list[dict[str, Any]]:
        """Execute all tool_use blocks from a response.

        Returns list of tool_result blocks ready to append to messages.
        Each tool call is tried individually; failures return error text.
        """
        results = []
        for block in content_blocks:
            # Handle both dict and object forms
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if block_type != "tool_use":
                continue

            tool_id = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
            tool_name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            tool_input = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})

            result = await self._execute_one(tool_id, tool_name, tool_input)
            results.append(result)

        return results

    async def _execute_one(
        self, tool_id: str, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single tool call. Returns a tool_result block."""
        # Access check
        if not self.check_access(tool_name):
            logger.warning(
                "Agent %s (role=%s) denied access to tool %s",
                self._agent_id, self._role.value, tool_name,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": f"Error: You do not have permission to use the '{tool_name}' tool.",
                "is_error": True,
            }

        # Budget check
        if self._budget and not self._budget.check_budget(self._agent_id):
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "Error: Budget exceeded. Request a budget increase before continuing.",
                "is_error": True,
            }

        # Execute
        tool_def = self._registry.get(tool_name)
        if tool_def is None:
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": f"Error: Unknown tool '{tool_name}'.",
                "is_error": True,
            }

        # Check for missing required parameters before calling handler
        schema = tool_def.input_schema
        required = schema.get("required", [])
        missing = [r for r in required if r not in tool_input or not tool_input.get(r)]
        if missing:
            logger.warning(
                "Agent %s called %s with missing required params: %s",
                self._agent_id, tool_name, missing,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": f"Error: Required parameters missing: {', '.join(missing)}. Please call {tool_name} again with all required parameters filled in.",
                "is_error": True,
            }

        try:
            logger.info("Agent %s calling tool %s", self._agent_id, tool_name)
            # Inject agent context for tools that need it (e.g. spawn_subagents)
            tool_input["_agent_id"] = self._agent_id
            result = await tool_def.handler(tool_input)
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": result,
            }
        except Exception as e:
            logger.error(
                "Tool %s failed for agent %s: %s",
                tool_name, self._agent_id, e,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": f"Error executing {tool_name}: {e}\n{traceback.format_exc()[-500:]}",
                "is_error": True,
            }
