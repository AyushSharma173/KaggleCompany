"""Agent lifecycle manager: create, start, stop, track agents.

This is pure Python — no AI. The orchestrator manages agent processes
as asyncio tasks within the same Python process.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, TYPE_CHECKING

import anthropic

from src.runtime.agent import Agent, AgentConfig
from src.runtime.heartbeat import HeartbeatRunner
from src.tools import AgentRole

if TYPE_CHECKING:
    from src.budget.tracker import BudgetTracker
    from src.comms.inter_agent import CommHub
    from src.comms.slack_bot import SlackBot
    from src.config import Settings
    from src.memory.state_store import StateStore
    from src.memory.strategy import StrategyLibrary
    from src.memory.transcripts import TranscriptLogger
    from src.tools import ToolRegistry

logger = logging.getLogger("kaggle-company.orchestrator")


class AgentManager:
    """Manages agent lifecycles. Does NOT make strategic decisions — agents do that."""

    def __init__(
        self,
        settings: Settings,
        state_store: StateStore,
        transcript_logger: TranscriptLogger,
        strategy_library: StrategyLibrary,
        tool_registry: ToolRegistry,
        budget_tracker: BudgetTracker,
        comm_hub: CommHub,
        slack_bot: SlackBot | None = None,
    ) -> None:
        self._settings = settings
        self._state = state_store
        self._transcript = transcript_logger
        self._strategy = strategy_library
        self._tools = tool_registry
        self._budget = budget_tracker
        self._comm_hub = comm_hub
        self._slack_bot = slack_bot

        self._agents: dict[str, Agent] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._heartbeat_runners: dict[str, HeartbeatRunner] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def set_slack_bot(self, slack_bot: SlackBot) -> None:
        self._slack_bot = slack_bot

    async def create_vp(self) -> str:
        """Create the VP agent (called on first boot)."""
        config = AgentConfig(
            agent_id="vp-001",
            role=AgentRole.VP,
            agent_type="vp",
            constitution_path=f"{self._settings.constitution_dir}/vp.md",
            slack_channel="vp-agent",
            heartbeat_interval_s=self._settings.vp_heartbeat_interval_seconds,
            budget_limit=self._settings.default_agent_budget_usd,
        )
        return await self._create_agent(config)

    async def create_worker(
        self,
        competition_slug: str,
        task: str = "",
        budget_usd: float = 50.0,
        worker_type: str = "worker",
    ) -> str:
        """Create a worker agent for a competition."""
        agent_id = f"worker-{competition_slug[:40]}"
        config = AgentConfig(
            agent_id=agent_id,
            role=AgentRole.WORKER,
            agent_type=worker_type,
            constitution_path=f"{self._settings.constitution_dir}/{worker_type}.md",
            parent_id="vp-001",
            competition_slug=competition_slug,
            slack_channel=f"comp-{competition_slug[:60]}",
            heartbeat_interval_s=self._settings.worker_heartbeat_interval_seconds,
            budget_limit=budget_usd,
        )

        agent_id = await self._create_agent(config)

        # Create competition Slack channel
        if self._slack_bot:
            await self._slack_bot.create_competition_channel(competition_slug)

        # Start the worker with its initial task
        if task:
            await self.run_agent_task(agent_id, task, trigger="vp_assignment")

        return agent_id

    async def create_subagent(
        self,
        parent_id: str,
        task: str,
        budget_usd: float = 10.0,
        subagent_type: str = "subagent",
    ) -> str:
        """Create an ephemeral subagent for a one-shot research task."""
        subagent_id = f"sub-{parent_id}-{uuid.uuid4().hex[:8]}"
        config = AgentConfig(
            agent_id=subagent_id,
            role=AgentRole.SUBAGENT,
            agent_type=subagent_type,
            constitution_path=f"{self._settings.constitution_dir}/{subagent_type}.md",
            parent_id=parent_id,
            heartbeat_interval_s=999999,  # Effectively disabled
            budget_limit=budget_usd,
            ephemeral=True,
            skip_reflection=True,
        )
        return await self._create_agent(config, model_override=self._settings.research_model)

    async def run_subagent_task(self, agent_id: str, task: str) -> str:
        """Run a task on a subagent and wait for completion. Returns the result string.

        Unlike run_agent_task (fire-and-forget), this blocks until the subagent
        finishes and returns its final text output. The subagent is cleaned up
        after completion.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            result = await agent.run_task(task, trigger="subagent_spawn")
        except Exception as e:
            logger.error("Subagent %s failed: %s", agent_id, e)
            result = f"[Subagent error: {e}]"
        finally:
            # Clean up — subagents are ephemeral
            await self.stop_agent(agent_id, "task_complete")

        return result

    async def _create_agent(self, config: AgentConfig, model_override: str | None = None) -> str:
        """Internal: create an agent instance and register it."""
        agent = Agent(
            config=config,
            client=self._client,
            model=model_override or self._settings.default_model,
            tool_registry=self._tools,
            state_store=self._state,
            transcript_logger=self._transcript,
            strategy_library=self._strategy,
            budget_tracker=self._budget,
        )

        self._agents[config.agent_id] = agent
        self._comm_hub.register_agent(config.agent_id)

        # Persist agent state
        self._state.write("agents", config.agent_id, {
            "id": config.agent_id,
            "role": config.role.value,
            "agent_type": config.agent_type,
            "status": "idle",
            "constitution": config.constitution_path,
            "slack_channel": config.slack_channel,
            "parent_id": config.parent_id,
            "competition_slug": config.competition_slug,
            "heartbeat_interval_s": config.heartbeat_interval_s,
            "budget_limit": config.budget_limit,
            "created_at": time.time(),
        })

        # Start heartbeat (skip for ephemeral agents)
        if not config.ephemeral:
            await self._start_heartbeat(config.agent_id, config)

        logger.info(
            "Created agent %s (role=%s, heartbeat=%ds)",
            config.agent_id, config.role.value, config.heartbeat_interval_s,
        )

        if self._slack_bot:
            await self._slack_bot.post_system_log(
                f"Agent `{config.agent_id}` created (role: {config.role.value})"
            )

        return config.agent_id

    async def run_agent_task(
        self,
        agent_id: str,
        task: str,
        trigger: str = "manual",
        reply_channel: str | None = None,
        reply_thread_ts: str | None = None,
    ) -> None:
        """Run a task on an agent. Starts as an asyncio task.

        If reply_channel is set, the agent's final text response is posted there.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        # Cancel any existing task
        if agent_id in self._tasks and not self._tasks[agent_id].done():
            self._tasks[agent_id].cancel()
            try:
                await self._tasks[agent_id]
            except asyncio.CancelledError:
                pass

        async def _run() -> None:
            try:
                result = await agent.run_task(task, trigger)
                logger.info("Agent %s task completed: %s", agent_id, result)

                # Auto-post response back to Slack channel
                if reply_channel and self._slack_bot and result and not result.startswith("["):
                    slack_text = result
                    ts = await self._slack_bot.post_message(
                        reply_channel, slack_text, thread_ts=reply_thread_ts,
                    )
                    if ts:
                        logger.info("Posted agent response to channel %s", reply_channel)
                    else:
                        logger.error("Failed to post agent response to channel %s", reply_channel)
            except asyncio.CancelledError:
                logger.info("Agent %s task cancelled", agent_id)
            except Exception as e:
                logger.error("Agent %s task failed: %s", agent_id, e, exc_info=True)

        self._tasks[agent_id] = asyncio.create_task(_run(), name=f"task-{agent_id}")

    async def stop_agent(self, agent_id: str, reason: str = "") -> None:
        """Gracefully stop an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return

        # Stop heartbeat
        if agent_id in self._heartbeat_tasks:
            self._heartbeat_tasks[agent_id].cancel()
            try:
                await self._heartbeat_tasks[agent_id]
            except asyncio.CancelledError:
                pass
            del self._heartbeat_tasks[agent_id]

        # Stop running task
        await agent.stop()
        if agent_id in self._tasks and not self._tasks[agent_id].done():
            self._tasks[agent_id].cancel()
            try:
                await asyncio.wait_for(self._tasks[agent_id], timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Cleanup
        self._comm_hub.unregister_agent(agent_id)
        self._state.write("agents", agent_id, {
            **self._state.read("agents", agent_id, default={}),
            "status": "terminated",
            "terminated_at": time.time(),
            "termination_reason": reason,
        })

        del self._agents[agent_id]
        logger.info("Agent %s stopped: %s", agent_id, reason)

        if self._slack_bot:
            await self._slack_bot.post_system_log(
                f"Agent `{agent_id}` terminated: {reason}"
            )

    async def _start_heartbeat(self, agent_id: str, config: AgentConfig) -> None:
        """Start the heartbeat loop for an agent."""
        runner = HeartbeatRunner(
            self._client, self._settings.heartbeat_model, self._state,
            constitution_dir=self._settings.constitution_dir,
        )
        self._heartbeat_runners[agent_id] = runner

        async def _heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(config.heartbeat_interval_s)
                try:
                    # Skip heartbeat if agent is already running a task
                    if agent_id in self._tasks and not self._tasks[agent_id].done():
                        continue

                    # Skip heartbeat entirely when budget is exceeded — no API calls
                    if not self._budget.check_budget(agent_id):
                        logger.debug("Heartbeat skipped for %s: budget exceeded", agent_id)
                        continue

                    result = await runner.check(agent_id, config.role.value)
                    logger.debug(
                        "Heartbeat %s: %s (%s)",
                        agent_id, result.action, result.reason,
                    )

                    if result.action == "wake" and result.task_description:
                        await self.run_agent_task(
                            agent_id, result.task_description, trigger="heartbeat"
                        )
                    elif result.action == "report":
                        await self.run_agent_task(
                            agent_id,
                            "Generate and post a progress report.",
                            trigger="heartbeat_report",
                        )

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Heartbeat error for %s: %s", agent_id, e)

        self._heartbeat_tasks[agent_id] = asyncio.create_task(
            _heartbeat_loop(), name=f"heartbeat-{agent_id}"
        )

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents and their status."""
        result = []
        for agent_id, agent in self._agents.items():
            state = self._state.read("agents", agent_id, default={})
            result.append({
                "id": agent_id,
                "role": agent.config.role.value,
                "status": "running" if agent.is_running else "idle",
                "turn_count": agent.turn_count,
                "last_active": agent.last_active,
                **{k: v for k, v in state.items() if k not in ("id", "role", "status")},
            })
        return result

    def get_agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        """Get detailed status for a specific agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": f"Agent {agent_id} not found"}
        state = self._state.read("agents", agent_id, default={})
        budget = self._budget.get_agent_spend(agent_id)
        return {
            "id": agent_id,
            "role": agent.config.role.value,
            "is_running": agent.is_running,
            "turn_count": agent.turn_count,
            "budget_spent": budget.get("total", 0),
            **state,
        }

    async def handle_ceo_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
        """Route a CEO message to the appropriate agent."""
        # Check for decision responses
        if text.startswith("decision_response:"):
            parts = text.split(":")
            if len(parts) >= 3:
                decision_id = parts[1]
                choice = parts[2]
                await self._resolve_decision(decision_id, choice)
                return

        # Route to VP by default, reply in the same channel
        vp = self._agents.get("vp-001")
        if vp:
            await self.run_agent_task(
                "vp-001",
                f"CEO says: {text}",
                trigger="ceo_message",
                reply_channel=channel,
                reply_thread_ts=thread_ts,
            )

    async def _resolve_decision(self, decision_id: str, choice: str) -> None:
        """Process a CEO decision response."""
        decisions = self._state.read("decisions", "pending", default={"items": []})
        pending = decisions.get("items", [])
        resolved = None

        for i, d in enumerate(pending):
            if d.get("id") == decision_id:
                resolved = d
                pending.pop(i)
                break

        if resolved:
            resolved["status"] = "resolved"
            resolved["choice"] = choice
            resolved["resolved_at"] = time.time()

            # Store resolved decision
            self._state.update("decisions", "resolved", lambda d: {
                **d,
                "items": d.get("items", []) + [resolved],
            }, default={"items": []})

            # Update pending
            self._state.write("decisions", "pending", {"items": pending})

            # Notify the requesting agent
            from_agent = resolved.get("from_agent", "vp-001")
            if from_agent in self._agents:
                await self.run_agent_task(
                    from_agent,
                    f"CEO decision on '{resolved.get('summary', '')}': {choice}",
                    trigger="ceo_decision",
                )

            logger.info("Decision %s resolved: %s", decision_id, choice)

    async def shutdown_all(self, timeout: float = 30.0) -> None:
        """Stop all agents gracefully."""
        logger.info("Shutting down all agents...")

        # Cancel all heartbeats
        for task in self._heartbeat_tasks.values():
            task.cancel()

        # Stop all agents
        for agent_id in list(self._agents.keys()):
            await self.stop_agent(agent_id, "system_shutdown")

        # Wait for all tasks
        all_tasks = list(self._tasks.values()) + list(self._heartbeat_tasks.values())
        if all_tasks:
            await asyncio.wait(all_tasks, timeout=timeout)

        logger.info("All agents shut down")
