"""Budget limit definitions and policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetLimits:
    """Configurable budget limits."""

    daily_total_usd: float = 50.0
    daily_api_usd: float = 40.0
    daily_gpu_usd: float = 20.0
    per_agent_daily_usd: float = 100.0
    warning_threshold: float = 0.8  # warn at 80%

    # Rate limits (frequency, not cost)
    max_api_calls_per_minute: int = 30
    max_submissions_per_day: int = 5


class BudgetExceeded(Exception):
    """Raised when a budget limit would be exceeded."""

    def __init__(self, limit_name: str, current: float, limit: float) -> None:
        self.limit_name = limit_name
        self.current = current
        self.limit = limit
        super().__init__(
            f"Budget exceeded: {limit_name} (${current:.2f} / ${limit:.2f})"
        )
