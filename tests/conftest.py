"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.config import Settings


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with temporary directories for testing."""
    return Settings(
        anthropic_api_key="test-key",
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        kaggle_username="test-user",
        kaggle_key="test-key",
        global_daily_budget_usd=10.0,
        state_dir=str(tmp_path / "state"),
        workspace_dir=str(tmp_path / "workspaces"),
        transcript_dir=str(tmp_path / "transcripts"),
        constitution_dir=str(tmp_path / "constitutions"),
        strategy_dir=str(tmp_path / "strategies"),
    )
