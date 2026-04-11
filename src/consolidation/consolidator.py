"""Consolidation system: reviews experiments and updates strategy library.

The "autoDream" equivalent. Runs on a schedule (daily, or after a competition ends).
Reviews all recent activity and updates the strategy library with lessons learned.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from src.comms.slack_bot import SlackBot
    from src.config import Settings
    from src.memory.state_store import StateStore
    from src.memory.strategy import StrategyLibrary
    from src.memory.transcripts import TranscriptLogger

logger = logging.getLogger("kaggle-company.consolidation")


class ConsolidationAgent:
    """Reviews transcripts and experiments, updates strategy library."""

    def __init__(
        self,
        settings: Settings,
        state_store: StateStore,
        transcript_logger: TranscriptLogger,
        strategy_library: StrategyLibrary,
        slack_bot: SlackBot | None = None,
    ) -> None:
        self._settings = settings
        self._state = state_store
        self._transcript = transcript_logger
        self._strategy = strategy_library
        self._slack_bot = slack_bot
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def run_consolidation(self) -> dict[str, Any]:
        """Run one consolidation cycle.

        Returns summary of what was updated.
        """
        logger.info("Starting consolidation cycle")
        start_time = time.time()

        # 1. Gather data
        experiment_data = self._gather_experiments()
        transcript_summaries = self._gather_transcripts()
        current_strategies = self._gather_strategies()
        ceo_feedback = self._gather_ceo_feedback()

        if not experiment_data and not transcript_summaries:
            logger.info("No new data to consolidate")
            return {"status": "skipped", "reason": "no new data"}

        # 2. Call Claude for analysis
        analysis = await self._analyze(
            experiment_data, transcript_summaries, current_strategies, ceo_feedback
        )

        # 3. Apply strategy updates
        updates = self._parse_updates(analysis)
        applied = []
        for update in updates:
            strategy_name = update.get("strategy")
            new_content = update.get("content")
            if strategy_name and new_content:
                self._strategy.write(strategy_name, new_content)
                applied.append(strategy_name)
                logger.info("Updated strategy: %s", strategy_name)

        # 4. Record consolidation
        summary = {
            "status": "completed",
            "duration_s": time.time() - start_time,
            "experiments_reviewed": len(experiment_data),
            "strategies_updated": applied,
            "analysis_preview": analysis[:500] if analysis else "",
        }

        self._state.write("consolidation", "last_run", {
            "timestamp": time.time(),
            "summary": summary,
        })

        # 5. Post to Slack
        if self._slack_bot and applied:
            await self._slack_bot.post_to(
                "research",
                f"*Strategy Library Updated*\n"
                f"Updated: {', '.join(applied)}\n"
                f"Based on {len(experiment_data)} experiments reviewed.",
            )

        logger.info(
            "Consolidation complete: %d strategies updated in %.1fs",
            len(applied), time.time() - start_time,
        )
        return summary

    def _gather_experiments(self) -> list[dict[str, Any]]:
        """Gather recent experiment data from all agents."""
        experiments = []
        agent_keys = self._state.list_keys("agents")
        for agent_key in agent_keys:
            recent = self._transcript.read_recent(agent_key, n=50)
            for entry in recent:
                if entry.get("type") in ("tool_result", "task_end"):
                    experiments.append({
                        "agent": agent_key,
                        **{k: v for k, v in entry.items() if k != "agent"},
                    })
        return experiments[-100:]  # Last 100 entries

    def _gather_transcripts(self) -> list[dict[str, Any]]:
        """Gather summary data from transcripts."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summaries = []
        agent_keys = self._state.list_keys("agents")
        for agent_key in agent_keys:
            summary = self._transcript.summarize_day(agent_key, today)
            if summary.get("total_entries", 0) > 0:
                summaries.append(summary)
        return summaries

    def _gather_strategies(self) -> dict[str, str]:
        """Get current strategy contents."""
        result = {}
        for name in self._strategy.list_available():
            content = self._strategy.get(name)
            if content:
                result[name] = content
        return result

    def _gather_ceo_feedback(self) -> list[dict[str, Any]]:
        """Get recent CEO directives and decision responses."""
        resolved = self._state.read("decisions", "resolved", default={"items": []})
        return resolved.get("items", [])[-20:]  # Last 20 decisions

    async def _analyze(
        self,
        experiments: list[dict],
        transcripts: list[dict],
        strategies: dict[str, str],
        ceo_feedback: list[dict],
    ) -> str:
        """Use Claude to analyze data and propose strategy updates."""
        constitution = ""
        try:
            from pathlib import Path
            const_path = Path(self._settings.constitution_dir) / "consolidation.md"
            if const_path.exists():
                constitution = const_path.read_text(encoding="utf-8")
        except Exception:
            pass

        prompt = f"""Review the following data and propose updates to our strategy library.

## Recent Experiments ({len(experiments)} entries)
{json.dumps(experiments[:30], indent=2, default=str)[:3000]}

## Agent Activity Today
{json.dumps(transcripts, indent=2, default=str)[:1000]}

## CEO Decisions & Feedback
{json.dumps(ceo_feedback[:10], indent=2, default=str)[:1000]}

## Current Strategies
{json.dumps(list(strategies.keys()))}

For each strategy that needs updating, output a JSON block:
```json
{{"strategy": "strategy-name", "content": "full updated markdown content"}}
```

Only update strategies where you have concrete evidence for changes.
If nothing needs updating, say "NO_UPDATES_NEEDED".
"""

        try:
            response = await self._client.messages.create(
                model=self._settings.reasoning_model,
                max_tokens=32000,
                system=constitution or "You are a knowledge curator reviewing experiment data.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            return text
        except Exception as e:
            logger.error("Consolidation analysis failed: %s", e)
            return ""

    def _parse_updates(self, analysis: str) -> list[dict[str, str]]:
        """Extract strategy updates from analysis text."""
        if not analysis or "NO_UPDATES_NEEDED" in analysis:
            return []

        updates = []
        # Look for JSON blocks in the analysis
        import re
        json_pattern = re.compile(r'```json\s*(\{[^`]+\})\s*```', re.DOTALL)
        for match in json_pattern.finditer(analysis):
            try:
                update = json.loads(match.group(1))
                if "strategy" in update and "content" in update:
                    updates.append(update)
            except json.JSONDecodeError:
                continue

        return updates
