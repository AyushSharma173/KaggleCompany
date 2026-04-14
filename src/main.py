"""Entry point for the Kaggle Company autonomous agent system."""

from __future__ import annotations

import asyncio
import logging
import signal

from src.config import Settings

logger = logging.getLogger("kaggle-company")


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logger.info(
        "Kaggle Company starting | daily budget=$%.2f | model=%s",
        settings.global_daily_budget_usd,
        settings.default_model,
    )

    # --- Layer 1: Memory ---
    from src.memory.state_store import StateStore
    from src.memory.strategy import StrategyLibrary
    from src.memory.transcripts import TranscriptLogger

    state_store = StateStore(settings.state_dir)
    transcript_logger = TranscriptLogger(settings.transcript_dir)

    # Clear previous run data on restart
    if settings.clear_slack_on_start:
        state_store.clear_all()
        transcript_logger.clear_all()
        # Clear reports from previous run
        from pathlib import Path
        import shutil
        reports_dir = Path("reports")
        if reports_dir.exists():
            shutil.rmtree(reports_dir)
        reports_dir.mkdir(exist_ok=True)
        logger.info("Cleared state, transcripts, and reports from previous run")
    strategy_library = StrategyLibrary(settings.strategy_dir)
    strategy_library.load_all()
    logger.info(
        "Memory layer initialized (strategies: %s)",
        strategy_library.list_available(),
    )

    # --- Layer 2: Budget ---
    from src.budget.tracker import BudgetTracker

    budget_tracker = BudgetTracker(
        state_store,
        daily_limit_usd=settings.global_daily_budget_usd,
        per_agent_limit_usd=settings.default_agent_budget_usd,
        model=settings.default_model,
    )
    logger.info("Budget controller initialized ($%.2f/day)", settings.global_daily_budget_usd)

    # --- Layer 3: Tools ---
    from src.budget.gpu_provisioner import GPUProvisioner
    from src.tools import ToolRegistry
    from src.tools.agent_mgmt_tools import register_agent_mgmt_tools
    from src.tools.communication_tools import register_communication_tools
    # Disabled for now — adding tools incrementally as needed
    # from src.tools.execution_tools import register_execution_tools
    # from src.tools.gpu_tools import register_gpu_tools
    # from src.tools.kaggle_tools import register_kaggle_tools
    from src.tools.research_tools import register_research_tools
    from src.tools.skill_tools import register_skill_tools

    tool_registry = ToolRegistry()
    gpu_provisioner = GPUProvisioner(
        runpod_api_key=settings.runpod_api_key,
        budget_tracker=budget_tracker,
    )

    # register_kaggle_tools(tool_registry)
    # register_execution_tools(tool_registry, settings.workspace_dir)
    logger.info(
        "Search providers: parallel=%s, brave=%s",
        f"configured (key len={len(settings.parallel_api_key)})" if settings.parallel_api_key else "not configured",
        "configured" if settings.brave_search_api_key else "not configured",
    )
    register_research_tools(
        tool_registry,
        strategy_library,
        brave_search_api_key=settings.brave_search_api_key,
        parallel_api_key=settings.parallel_api_key,
    )
    register_skill_tools(tool_registry, settings)
    # register_gpu_tools(tool_registry, gpu_provisioner)
    # Communication and agent mgmt tools registered after orchestrator is created
    logger.info("Tool registry initialized (%d tools)", len(tool_registry.list_all()))

    # --- Layer 4: Communication ---
    from src.comms.inter_agent import CommHub
    from src.comms.slack_bot import SlackBot
    from src.orchestrator.events import EventBus

    comm_hub = CommHub()
    event_bus = EventBus()
    slack_bot: SlackBot | None = None

    if settings.slack_bot_token and settings.slack_app_token:
        slack_bot = SlackBot(settings)
        logger.info("Slack bot configured")
    else:
        logger.warning("Slack tokens not set — running without Slack")

    # --- Layer 5: Orchestrator ---
    from src.orchestrator.health import HealthMonitor
    from src.orchestrator.manager import AgentManager
    from src.orchestrator.scheduler import Scheduler

    manager = AgentManager(
        settings=settings,
        state_store=state_store,
        transcript_logger=transcript_logger,
        strategy_library=strategy_library,
        tool_registry=tool_registry,
        budget_tracker=budget_tracker,
        comm_hub=comm_hub,
        slack_bot=slack_bot,
        event_bus=event_bus,
    )

    # Now register tools that need orchestrator reference
    register_communication_tools(
        tool_registry, slack_bot, comm_hub, state_store, event_bus=event_bus
    )
    register_agent_mgmt_tools(tool_registry, manager)
    logger.info("All tools registered (%d total)", len(tool_registry.list_all()))

    # --- Workflow layer: subscribe handlers from config ---
    # The source of truth for which handlers are active is
    # state/workflow_config.json, editable from the control dashboard.
    # See alignment.md Part 8.
    import json as _json
    from src.workflows.handlers import (
        make_notify_vp_for_review_handler,
        make_upload_report_to_slack_handler,
    )

    _handler_factories = {
        "notify_vp_for_review": lambda: make_notify_vp_for_review_handler(manager),
        "upload_report_to_slack": lambda: make_upload_report_to_slack_handler(slack_bot),
    }

    _workflow_config_path = Path("state/workflow_config.json")
    if _workflow_config_path.exists():
        _wf_config = _json.loads(_workflow_config_path.read_text())
        for _event_type, _handler_list in _wf_config.items():
            for _entry in _handler_list:
                if _entry.get("enabled") and _entry["handler"] in _handler_factories:
                    event_bus.subscribe(
                        _event_type,
                        _handler_factories[_entry["handler"]](),
                    )
        logger.info("Workflow config loaded from %s", _workflow_config_path)
    else:
        # Fallback: default handler if no config file exists
        event_bus.subscribe(
            "report.saved",
            make_notify_vp_for_review_handler(manager),
        )
        logger.info("No workflow config found — using default handler")

    logger.info(
        "Workflow handlers registered: %s",
        event_bus.list_subscriptions(),
    )

    # Write tool manifest for the control dashboard
    _tool_manifest = []
    for _name in tool_registry.list_all():
        _td = tool_registry.get_definition(_name)
        if _td:
            _tool_manifest.append({
                "name": _td.name,
                "description": _td.description,
                "allowed_roles": sorted(r.value for r in _td.allowed_roles),
            })
    _manifest_path = Path("state/tool_manifest.json")
    _manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _manifest_path.write_text(_json.dumps(_tool_manifest, indent=2))
    logger.info("Tool manifest written to %s (%d tools)", _manifest_path, len(_tool_manifest))

    health = HealthMonitor(state_store, manager)
    scheduler = Scheduler(manager, budget_tracker, health)

    # --- Layer 6: Wire Slack callback ---
    if slack_bot:
        slack_bot.set_message_callback(
            lambda channel, user, text, thread_ts: manager.handle_ceo_message(channel, text, thread_ts)
        )

    # --- Start services ---
    if slack_bot:
        try:
            await slack_bot.start(clear_history=settings.clear_slack_on_start)
        except Exception as e:
            logger.error("Failed to start Slack bot: %s", e)
            slack_bot = None

    await scheduler.start()

    # --- First boot: create VP agent ---
    if not manager.list_agents():
        logger.info("First boot detected — creating VP agent")
        await manager.create_vp()

        # First boot: tell VP to scout competitions
        from datetime import date
        first_boot_directive = (
            f"Today is {date.today().isoformat()}. "
            "Fetch https://www.kaggle.com/competitions — this single page lists all active competitions with prize amounts, deadlines, and participant counts. "
            "Do not add URL filters or parameters. Parse the page to identify competitions with cash prizes and deadlines still open. "
            "Post a summary to #ceo-briefing with competition names, prize amounts, deadlines, and team counts. "
            "Then ask the CEO which competitions to deep-dive."
        )
        await manager.run_agent_task(
            "vp-001",
            first_boot_directive,
            trigger="first_boot",
        )
    else:
        logger.info("Existing agents found: %d", len(manager.list_agents()))

    logger.info("Kaggle Company is running. Ctrl+C to shut down.")

    # --- Run until shutdown ---
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await shutdown_event.wait()

    # --- Graceful shutdown ---
    logger.info("Shutting down...")
    await manager.shutdown_all(timeout=15.0)
    await scheduler.stop()
    if slack_bot:
        await slack_bot.stop()
    logger.info("Kaggle Company shut down cleanly.")


def cli() -> None:
    """CLI entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
