"""Tests for budget tracking and enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.budget.limits import BudgetExceeded, BudgetLimits
from src.budget.security import RateLimiter, SecurityController
from src.budget.tracker import BudgetTracker
from src.memory.state_store import StateStore


class TestBudgetTracker:
    def _make_tracker(self, tmp_path: Path, daily_limit: float = 10.0, per_agent: float = 5.0) -> BudgetTracker:
        store = StateStore(str(tmp_path / "state"))
        return BudgetTracker(store, daily_limit_usd=daily_limit, per_agent_limit_usd=per_agent)

    def test_initial_state(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path)
        summary = tracker.get_daily_summary()
        assert summary["total_usd"] == 0.0

    def test_record_api_usage(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path)
        cost = tracker.record_api_usage("vp-001", {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        })
        assert cost > 0
        summary = tracker.get_daily_summary()
        assert summary["total_usd"] > 0

    def test_budget_check_passes(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path, daily_limit=10.0)
        assert tracker.check_budget("vp-001", 1.0) is True

    def test_budget_check_fails_daily(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path, daily_limit=0.01)
        tracker.record_api_usage("vp-001", {
            "input_tokens": 100000,
            "output_tokens": 50000,
        })
        assert tracker.check_budget("vp-001", 0.01) is False

    def test_budget_check_fails_per_agent(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path, daily_limit=100.0, per_agent=0.01)
        tracker.record_api_usage("vp-001", {
            "input_tokens": 100000,
            "output_tokens": 50000,
        })
        assert tracker.check_budget("vp-001") is False

    def test_get_agent_spend(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path)
        tracker.record_api_usage("vp-001", {"input_tokens": 1000, "output_tokens": 500})
        tracker.record_api_usage("worker-1", {"input_tokens": 2000, "output_tokens": 1000})
        vp_spend = tracker.get_agent_spend("vp-001")
        worker_spend = tracker.get_agent_spend("worker-1")
        assert vp_spend["total"] > 0
        assert worker_spend["total"] > vp_spend["total"]

    def test_get_remaining_today(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path, daily_limit=10.0)
        remaining = tracker.get_remaining_today()
        assert remaining == 10.0

    def test_record_gpu_spend(self, tmp_path: Path) -> None:
        tracker = self._make_tracker(tmp_path)
        tracker.record_gpu_spend("worker-1", 2.50, {"instance_id": "gpu-123"})
        summary = tracker.get_daily_summary()
        assert summary["gpu_usd"] == 2.50
        assert summary["total_usd"] == 2.50


class TestBudgetLimits:
    def test_defaults(self) -> None:
        limits = BudgetLimits()
        assert limits.daily_total_usd == 50.0
        assert limits.warning_threshold == 0.8

    def test_budget_exceeded(self) -> None:
        exc = BudgetExceeded("daily_total", 55.0, 50.0)
        assert "55.00" in str(exc)
        assert "50.00" in str(exc)


class TestRateLimiter:
    def test_allows_within_limit(self) -> None:
        limiter = RateLimiter(max_calls=5, window_seconds=60.0)
        for _ in range(5):
            assert limiter.try_acquire() is True

    def test_blocks_over_limit(self) -> None:
        limiter = RateLimiter(max_calls=2, window_seconds=60.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False


class TestSecurityController:
    def test_write_within_workspace(self, tmp_path: Path) -> None:
        sec = SecurityController(str(tmp_path / "workspaces"))
        workspace = sec.create_workspace("agent-1")
        assert sec.validate_file_access("agent-1", str(workspace / "file.py"), write=True)

    def test_write_outside_workspace_blocked(self, tmp_path: Path) -> None:
        sec = SecurityController(str(tmp_path / "workspaces"))
        sec.create_workspace("agent-1")
        assert sec.validate_file_access("agent-1", "/tmp/evil.py", write=True) is False

    def test_create_workspace(self, tmp_path: Path) -> None:
        sec = SecurityController(str(tmp_path / "workspaces"))
        ws = sec.create_workspace("agent-1")
        assert ws.exists()
        assert ws.name == "agent-1"
