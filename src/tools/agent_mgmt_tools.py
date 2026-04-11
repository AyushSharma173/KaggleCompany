"""Agent management tools: create/terminate workers, spawn subagents."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, TYPE_CHECKING

from src.tools import AgentRole, ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    pass  # Orchestrator reference passed via closure

logger = logging.getLogger("kaggle-company.tools.agent_mgmt")


def make_agent_mgmt_tools(
    orchestrator_ref: Any,  # Will be AgentManager, passed as reference to avoid circular imports
) -> list[ToolDefinition]:
    """Create agent management tools with bound orchestrator reference."""

    async def create_worker_agent(params: dict[str, Any]) -> str:
        """Create a new worker agent for a competition."""
        competition_slug = params.get("competition_slug", "")
        task = params.get("task", "")
        budget = params.get("budget_usd", 50.0)
        worker_type = params.get("worker_type", "worker")

        if not competition_slug:
            return "Error: competition_slug is required"
        if orchestrator_ref is None:
            return "Error: orchestrator not available"

        try:
            agent_id = await orchestrator_ref.create_worker(
                competition_slug=competition_slug,
                task=task or f"Work on competition: {competition_slug}",
                budget_usd=budget,
                worker_type=worker_type,
            )
            return json.dumps({
                "agent_id": agent_id,
                "status": "created",
                "competition": competition_slug,
                "budget_usd": budget,
            })
        except Exception as e:
            return f"Error creating worker: {e}"

    async def terminate_agent(params: dict[str, Any]) -> str:
        """Terminate a running agent."""
        agent_id = params.get("agent_id", "")
        reason = params.get("reason", "VP directive")

        if not agent_id:
            return "Error: agent_id is required"
        if orchestrator_ref is None:
            return "Error: orchestrator not available"

        try:
            await orchestrator_ref.stop_agent(agent_id, reason)
            return f"Agent {agent_id} terminated: {reason}"
        except Exception as e:
            return f"Error terminating agent: {e}"

    async def list_agents(params: dict[str, Any]) -> str:
        """List all running agents and their status."""
        if orchestrator_ref is None:
            return "Error: orchestrator not available"

        try:
            agents = orchestrator_ref.list_agents()
            return json.dumps({"agents": agents}, indent=2)
        except Exception as e:
            return f"Error listing agents: {e}"

    async def get_agent_output(params: dict[str, Any]) -> str:
        """Get the latest output from a specific agent."""
        agent_id = params.get("agent_id", "")
        if not agent_id:
            return "Error: agent_id is required"
        if orchestrator_ref is None:
            return "Error: orchestrator not available"

        try:
            output = orchestrator_ref.get_agent_status(agent_id)
            return json.dumps(output, indent=2)
        except Exception as e:
            return f"Error getting agent output: {e}"

    async def spawn_subagents(params: dict[str, Any]) -> str:
        """Spawn one or more ephemeral subagents and wait for results.

        Subagents run in parallel, each executing a focused task.
        Results are returned when ALL subagents complete. Subagents are
        automatically cleaned up after completion.
        """
        tasks = params.get("tasks", [])
        budget_per_task = params.get("budget_per_task_usd", 5.0)
        subagent_type = params.get("subagent_type", "subagent")
        parent_id = params.get("_agent_id", "unknown")

        if not tasks:
            return "Error: 'tasks' is required (list of task description strings)"
        if len(tasks) > 8:
            return "Error: maximum 8 parallel research tasks"
        if orchestrator_ref is None:
            return "Error: orchestrator not available"

        async def _run_one(task_desc: str) -> dict[str, Any]:
            try:
                subagent_id = await orchestrator_ref.create_subagent(
                    parent_id=parent_id,
                    task=task_desc,
                    budget_usd=budget_per_task,
                    subagent_type=subagent_type,
                )
                result = await orchestrator_ref.run_subagent_task(subagent_id, task_desc)
                return {"task": task_desc, "result": result, "status": "complete"}
            except Exception as e:
                return {"task": task_desc, "result": str(e), "status": "error"}

        results = await asyncio.gather(*[_run_one(t) for t in tasks])
        return json.dumps({"research_results": list(results)}, indent=2)

    async def reassign_budget(params: dict[str, Any]) -> str:
        """Reassign budget between agents (VP only)."""
        from_agent = params.get("from_agent", "")
        to_agent = params.get("to_agent", "")
        amount = params.get("amount_usd", 0)

        if not from_agent or not to_agent or not amount:
            return "Error: from_agent, to_agent, and amount_usd are required"

        return json.dumps({
            "status": "reassigned",
            "from": from_agent,
            "to": to_agent,
            "amount_usd": amount,
            "note": "Budget reassignment recorded",
        })

    return [
        ToolDefinition(
            name="create_worker_agent",
            description=(
                "Create a new worker agent assigned to a Kaggle competition. The worker runs autonomously "
                "with its own constitution, tools, and budget. Use worker_type='research-worker' for deep-dive "
                "research that spawns subagents. The task string becomes the worker's initial instruction. "
                "Returns the created agent's ID and status."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "competition_slug": {"type": "string", "description": "Kaggle competition slug from the competition URL, e.g. 'cafa-6-protein-function-prediction'"},
                    "task": {"type": "string", "description": "Initial task description — this becomes the worker's first instruction"},
                    "budget_usd": {"type": "number", "description": "Budget allocation in USD (default 50)", "default": 50.0},
                    "worker_type": {"type": "string", "description": "Worker type: 'research-worker' for deep-dive research, 'worker' for general tasks", "default": "worker"},
                },
                "required": ["competition_slug"],
                "additionalProperties": False,
            },
            handler=create_worker_agent,
            allowed_roles={AgentRole.VP},
            input_examples=[
                {"competition_slug": "cafa-6-protein-function-prediction", "worker_type": "research-worker", "task": "Deep dive on CAFA 6 protein function prediction.", "budget_usd": 50},
            ],
        ),
        # terminate_agent — disabled for now
        ToolDefinition(
            name="list_agents",
            description="List all agents in the system with their current status.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=list_agents,
            allowed_roles={AgentRole.VP, AgentRole.WORKER},
        ),
        ToolDefinition(
            name="get_agent_output",
            description="Get the latest status and output from a specific agent.",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID"},
                },
                "required": ["agent_id"],
                "additionalProperties": False,
            },
            handler=get_agent_output,
            allowed_roles={AgentRole.VP},
        ),
        ToolDefinition(
            name="spawn_subagents",
            description=(
                "Spawn parallel subagents that execute focused tasks and return results. "
                "Each task runs as an independent subagent with its own constitution. All tasks "
                "run in parallel and results are returned when all complete. Use subagent_type "
                "to select the right kind of subagent (e.g., 'research-subagent' for research)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of research task descriptions. Each becomes a separate subagent. "
                            "Be specific: include what to search for, what to extract, and what "
                            "format to return results in."
                        ),
                    },
                    "budget_per_task_usd": {
                        "type": "number",
                        "description": "Budget per research task in USD",
                        "default": 5.0,
                    },
                    "subagent_type": {
                        "type": "string",
                        "description": "Subagent type — determines which constitution file to load (e.g., 'research-subagent', 'subagent')",
                        "default": "subagent",
                    },
                },
                "required": ["tasks"],
                "additionalProperties": False,
            },
            handler=spawn_subagents,
            allowed_roles={AgentRole.VP, AgentRole.WORKER},
        ),
        # reassign_budget — disabled for now
    ]


def register_agent_mgmt_tools(registry: ToolRegistry, orchestrator_ref: Any = None) -> None:
    """Register all agent management tools."""
    for tool in make_agent_mgmt_tools(orchestrator_ref):
        registry.register(tool)
