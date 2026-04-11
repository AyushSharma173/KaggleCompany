"""Scheduling: daily rollover, consolidation triggers, health checks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.budget.tracker import BudgetTracker
    from src.orchestrator.health import HealthMonitor
    from src.orchestrator.manager import AgentManager

logger = logging.getLogger("kaggle-company.scheduler")


class Scheduler:
    """Periodic tasks and scheduling.

    - Health checks every 60 seconds
    - Daily budget rollover at midnight UTC
    - Daily consolidation trigger
    """

    def __init__(
        self,
        manager: AgentManager,
        budget: BudgetTracker,
        health: HealthMonitor,
    ) -> None:
        self._manager = manager
        self._budget = budget
        self._health = health
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        """Start all periodic tasks."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._health_check_loop(), name="health-check"),
            asyncio.create_task(self._daily_rollover_loop(), name="daily-rollover"),
        ]
        logger.info("Scheduler started with %d periodic tasks", len(self._tasks))

    async def stop(self) -> None:
        """Cancel all periodic tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
        self._tasks.clear()
        logger.info("Scheduler stopped")

    async def _health_check_loop(self) -> None:
        """Run health checks every 60 seconds."""
        while self._running:
            try:
                await asyncio.sleep(60)
                issues = await self._health.check_all()
                if issues:
                    for issue in issues:
                        logger.warning("Health issue: %s", issue)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Health check error: %s", e)

    async def _daily_rollover_loop(self) -> None:
        """Check for day boundary and trigger rollover."""
        last_date = self._budget._today_key()
        while self._running:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                current_date = self._budget._today_key()
                if current_date != last_date:
                    logger.info("Day boundary detected: %s -> %s", last_date, current_date)
                    self._budget._ensure_daily_state()
                    last_date = current_date

                    # Trigger daily briefing if VP is available
                    vp = self._manager.get_agent("vp-001")
                    if vp:
                        await self._manager.run_agent_task(
                            "vp-001",
                            "New day started. Generate daily briefing for CEO and review portfolio status.",
                            trigger="daily_rollover",
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Daily rollover error: %s", e)
