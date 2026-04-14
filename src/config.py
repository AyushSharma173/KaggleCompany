"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""

    # Kaggle
    kaggle_username: str = ""
    kaggle_key: str = ""

    # GPU (optional)
    runpod_api_key: str = ""

    # Search
    parallel_api_key: str = ""
    brave_search_api_key: str = ""  # fallback if Parallel is down

    # Budget
    global_daily_budget_usd: float = 100.00
    default_agent_budget_usd: float = 100.00

    # Heartbeat intervals — health monitor stuck detection is DISABLED.
    # These values are only used for heartbeat scheduling, not kill thresholds.
    vp_heartbeat_interval_seconds: int = 86400      # 24 hours
    worker_heartbeat_interval_seconds: int = 86400   # 24 hours

    # Startup behavior
    clear_slack_on_start: bool = False

    # Models — Opus 4.6 everywhere for maximum reasoning quality
    default_model: str = "claude-opus-4-6"
    reasoning_model: str = "claude-opus-4-6"
    heartbeat_model: str = "claude-opus-4-6"
    research_model: str = "claude-opus-4-6"

    # Directories
    state_dir: str = "state"
    workspace_dir: str = "workspaces"
    transcript_dir: str = "transcripts"
    constitution_dir: str = "constitutions"
    strategy_dir: str = "strategies"
    skill_dir: str = "skills"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
