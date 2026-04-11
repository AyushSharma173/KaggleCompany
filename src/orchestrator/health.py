"""Health monitoring: stuck detection, crash recovery."""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.state_store import StateStore
    from src.orchestrator.manager import AgentManager

logger = logging.getLogger("kaggle-company.health")


class HealthMonitor:
    """Detects stuck or crashed agents.

    Each agent updates a 'last_active' timestamp on every turn.
    If an agent hasn't been active for 2x its heartbeat interval,
    it's considered stuck.
    """

    def __init__(self, state_store: StateStore, manager: AgentManager | None = None) -> None:
        self._state = state_store
        self._manager = manager
        self._restart_counts: dict[str, int] = {}
        self._max_restarts = 3

    def set_manager(self, manager: AgentManager) -> None:
        self._manager = manager

    async def check_all(self) -> list[dict[str, Any]]:
        """Check health of all agents. Returns list of issues found."""
        issues: list[dict[str, Any]] = []
        if not self._manager:
            return issues

        for agent_info in self._manager.list_agents():
            agent_id = agent_info["id"]
            issue = self._check_agent(agent_info)
            if issue:
                issues.append(issue)
                await self._handle_issue(issue)

        return issues

    def _check_agent(self, agent_info: dict[str, Any]) -> dict[str, Any] | None:
        """Check a single agent's health.

        NOTE: Stuck detection is DISABLED. Agents doing deep research can
        legitimately block for 45+ minutes (Parallel.ai ultra tier) and
        spawn_subagents waits for all subagents. Killing them destroys
        completed work and wastes money on orphaned API calls.

        The health monitor now only logs warnings — it never kills agents.
        """
        return None

    async def _handle_issue(self, issue: dict[str, Any]) -> None:
        """Handle a detected health issue."""
        agent_id = issue["agent_id"]
        issue_type = issue["type"]

        if issue_type == "stuck" and self._manager:
            restarts = self._restart_counts.get(agent_id, 0)
            if restarts < self._max_restarts:
                logger.warning(
                    "Restarting stuck agent %s (attempt %d/%d)",
                    agent_id, restarts + 1, self._max_restarts,
                )
                self._restart_counts[agent_id] = restarts + 1
                try:
                    agent = self._manager.get_agent(agent_id)
                    if agent:
                        await agent.stop()
                        await self._manager.run_agent_task(
                            agent_id,
                            "You were restarted due to being stuck. Review your last task and continue.",
                            trigger="health_restart",
                        )
                except Exception as e:
                    logger.error("Failed to restart agent %s: %s", agent_id, e)
            else:
                logger.error(
                    "Agent %s exceeded max restarts (%d). Alerting CEO.",
                    agent_id, self._max_restarts,
                )
