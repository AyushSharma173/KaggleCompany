"""Real-time budget tracking and enforcement.

Tracks API token costs, GPU spend, and per-agent budgets.
Enforcement is deterministic Python — not prompt-based.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.state_store import StateStore

logger = logging.getLogger("kaggle-company.budget")

# Claude API pricing per 1M tokens (USD)
PRICING = {
    "claude-sonnet-4-20250514": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-opus-4-20250115": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-haiku-4-20250414": {
        "input": 0.80,
        "output": 4.0,
        "cache_read": 0.08,
        "cache_write": 1.0,
    },
}

# Default pricing if model not in table
DEFAULT_PRICING = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}


class BudgetTracker:
    """Deterministic budget enforcement. Pure Python, no AI."""

    def __init__(
        self,
        state_store: StateStore,
        daily_limit_usd: float = 50.0,
        per_agent_limit_usd: float = 100.0,
        warning_threshold: float = 0.8,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._state = state_store
        self._daily_limit = daily_limit_usd
        self._per_agent_limit = per_agent_limit_usd
        self._warning_threshold = warning_threshold
        self._model = model
        self._pricing = PRICING.get(model, DEFAULT_PRICING)
        self._warned = False

        # Load today's state or initialize
        self._ensure_daily_state()

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_daily_state(self) -> None:
        today = self._today_key()
        current = self._state.read("budget", "daily", default=None)
        if current is None or current.get("date") != today:
            # New day — archive old and reset
            if current and current.get("date"):
                self._state.write("budget", f"archive_{current['date']}", current)
            self._state.write("budget", "daily", {
                "date": today,
                "total_usd": 0.0,
                "api_usd": 0.0,
                "gpu_usd": 0.0,
                "by_agent": {},
                "entries": [],
            })
            self._warned = False

    def record_api_usage(self, agent_id: str, usage: dict[str, int], model: str | None = None) -> float:
        """Record Claude API usage. Returns cost in USD.

        usage = {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
        """
        self._ensure_daily_state()
        pricing = PRICING.get(model or self._model, DEFAULT_PRICING)

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
        cache_read_tokens = usage.get("cache_read_input_tokens", 0)

        cost = (
            (input_tokens / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
            + (cache_write_tokens / 1_000_000) * pricing["cache_write"]
            + (cache_read_tokens / 1_000_000) * pricing["cache_read"]
        )

        self._record_spend(agent_id, cost, "api", {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_read_tokens": cache_read_tokens,
            "model": model or self._model,
        })

        return cost

    def record_gpu_spend(self, agent_id: str, cost_usd: float, details: dict[str, Any] | None = None) -> None:
        """Record GPU compute cost."""
        self._ensure_daily_state()
        self._record_spend(agent_id, cost_usd, "gpu", details or {})

    def _record_spend(self, agent_id: str, cost: float, category: str, details: dict[str, Any]) -> None:
        """Internal: record a spend entry."""
        def updater(state: dict) -> dict:
            state["total_usd"] = state.get("total_usd", 0) + cost
            state[f"{category}_usd"] = state.get(f"{category}_usd", 0) + cost

            by_agent = state.get("by_agent", {})
            agent_spend = by_agent.get(agent_id, {"total": 0.0, "api": 0.0, "gpu": 0.0})
            agent_spend["total"] = agent_spend.get("total", 0) + cost
            agent_spend[category] = agent_spend.get(category, 0) + cost
            by_agent[agent_id] = agent_spend
            state["by_agent"] = by_agent

            # Keep last 100 entries for audit
            entries = state.get("entries", [])
            entries.append({
                "timestamp": time.time(),
                "agent_id": agent_id,
                "category": category,
                "cost_usd": cost,
                "details": details,
            })
            state["entries"] = entries[-100:]

            return state

        self._state.update("budget", "daily", updater, default={
            "date": self._today_key(),
            "total_usd": 0.0,
            "api_usd": 0.0,
            "gpu_usd": 0.0,
            "by_agent": {},
            "entries": [],
        })

        # Check warning threshold
        daily = self._state.read("budget", "daily")
        if daily and daily.get("total_usd", 0) >= self._daily_limit * self._warning_threshold:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "Budget warning: $%.2f / $%.2f (%.0f%%)",
                    daily["total_usd"], self._daily_limit,
                    (daily["total_usd"] / self._daily_limit) * 100,
                )

    def check_budget(self, agent_id: str, estimated_cost: float = 0.0) -> bool:
        """Check if spend + estimated_cost is within limits. Called BEFORE API calls."""
        self._ensure_daily_state()
        daily = self._state.read("budget", "daily", default={})

        total_spent = daily.get("total_usd", 0)
        if total_spent + estimated_cost > self._daily_limit:
            logger.warning(
                "Budget check FAILED: $%.2f + $%.4f > $%.2f daily limit",
                total_spent, estimated_cost, self._daily_limit,
            )
            return False

        # Per-agent check
        agent_spent = daily.get("by_agent", {}).get(agent_id, {}).get("total", 0)
        if agent_spent + estimated_cost > self._per_agent_limit:
            logger.warning(
                "Budget check FAILED for %s: $%.2f + $%.4f > $%.2f agent limit",
                agent_id, agent_spent, estimated_cost, self._per_agent_limit,
            )
            return False

        return True

    def get_daily_summary(self) -> dict[str, Any]:
        """Full daily spend summary."""
        self._ensure_daily_state()
        return self._state.read("budget", "daily", default={})

    def get_agent_spend(self, agent_id: str) -> dict[str, float]:
        """Spend breakdown for a specific agent."""
        daily = self.get_daily_summary()
        return daily.get("by_agent", {}).get(agent_id, {"total": 0.0, "api": 0.0, "gpu": 0.0})

    def get_remaining_today(self) -> float:
        """USD remaining in today's budget."""
        daily = self.get_daily_summary()
        return max(0, self._daily_limit - daily.get("total_usd", 0))

    def set_agent_limit(self, agent_id: str, limit_usd: float) -> None:
        """Override per-agent limit for a specific agent."""
        # Store per-agent overrides
        self._state.write("budget", f"limit_{agent_id}", {"limit_usd": limit_usd})

    @property
    def daily_limit(self) -> float:
        return self._daily_limit

    @property
    def per_agent_limit(self) -> float:
        return self._per_agent_limit
