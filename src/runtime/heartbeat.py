"""Lightweight heartbeat check using cheap model.

During idle periods, asks: "Given current state, is there anything worth doing?"
Uses minimal context to keep costs low.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from src.memory.state_store import StateStore

logger = logging.getLogger("kaggle-company.heartbeat")

# Minimum interval between wake tasks with similar descriptions
WAKE_DEDUP_INTERVAL_S = 900  # 15 minutes


@dataclass
class HeartbeatResult:
    action: str  # "idle", "wake", "check_gpu", "report"
    reason: str = ""
    task_description: str = ""


_HEARTBEAT_SYSTEM_FALLBACK = """You are a quick-check assistant for an autonomous Kaggle competition agent.
Respond with ACTION: IDLE/WAKE/CHECK_GPU/REPORT, REASON:, and TASK: (if WAKE)."""


class HeartbeatRunner:
    """Lightweight periodic check using cheap/fast model."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str,
        state_store: StateStore,
        constitution_dir: str = "constitutions",
    ) -> None:
        self._client = client
        self._model = model
        self._state = state_store
        self._last_wake_task: str = ""
        self._last_wake_time: float = 0

        # Load heartbeat system prompt from file
        from pathlib import Path
        hb_path = Path(constitution_dir) / "heartbeat.md"
        if hb_path.exists():
            self._system_prompt = hb_path.read_text(encoding="utf-8")
        else:
            self._system_prompt = _HEARTBEAT_SYSTEM_FALLBACK

    async def check(self, agent_id: str, agent_role: str) -> HeartbeatResult:
        """Run heartbeat check. Returns action to take."""
        context = self._build_context(agent_id, agent_role)

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=200,
                system=self._system_prompt,
                messages=[{"role": "user", "content": context}],
            )

            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            result = self._parse_response(text)

            # Deduplicate: if same/similar wake task was triggered recently, downgrade to idle
            if result.action == "wake" and result.task_description:
                if self._is_duplicate_wake(result.task_description):
                    logger.info(
                        "Heartbeat %s: suppressed duplicate wake task: %s",
                        agent_id, result.task_description[:80],
                    )
                    return HeartbeatResult(
                        action="idle",
                        reason="Duplicate task suppressed (triggered recently)",
                    )
                # Record this wake
                self._last_wake_task = result.task_description
                self._last_wake_time = time.time()

            return result

        except Exception as e:
            logger.warning("Heartbeat check failed for %s: %s", agent_id, e)
            return HeartbeatResult(action="idle", reason=f"Heartbeat error: {e}")

    def _is_duplicate_wake(self, task: str) -> bool:
        """Check if a wake task is a duplicate of a recent one."""
        if not self._last_wake_task:
            return False
        if time.time() - self._last_wake_time > WAKE_DEDUP_INTERVAL_S:
            return False
        # Simple similarity: check if key words overlap significantly
        last_words = set(self._last_wake_task.lower().split())
        new_words = set(task.lower().split())
        if not last_words or not new_words:
            return False
        overlap = len(last_words & new_words) / max(len(last_words), len(new_words))
        return overlap > 0.5

    def _build_context(self, agent_id: str, agent_role: str) -> str:
        """Build minimal context for heartbeat check."""
        agent_state = self._state.read("agents", agent_id, default={})
        portfolio = self._state.read("portfolio", "active", default={})
        active_comps = portfolio.get("active_competitions", {})
        budget = self._state.read("budget", "daily", default={})
        decisions = self._state.read("decisions", "pending", default={})
        pending = decisions.get("items", [])

        budget_spent = budget.get("total_usd", 0)
        budget_status = "OK"
        if budget_spent > 40:
            budget_status = "WARNING — approaching limit"
        if budget_spent > 50:
            budget_status = "EXHAUSTED — do NOT start any work"

        # Check if VP recently completed first-boot (awaiting CEO competition selection)
        last_task = agent_state.get('last_task', 'none')
        awaiting_ceo = ""
        if "first_boot" in agent_state.get("trigger", "") or "competition" in last_task.lower():
            import time as _time
            finished = agent_state.get("finished_at", 0)
            if finished and (_time.time() - finished) < 3600:  # within last hour
                awaiting_ceo = "\n⚠️ AWAITING CEO RESPONSE: The VP just posted a competition list to #ceo-briefing and is waiting for the CEO to pick which competitions to deep-dive. Do NOT start new competition scanning or evaluation — wait for CEO response."

        context = f"""Agent: {agent_id} (role: {agent_role})
Status: {agent_state.get('status', 'unknown')}
Last task: {last_task}

Active competitions: {len(active_comps)}
Budget spent today: ${budget_spent:.2f} — {budget_status}
Pending CEO decisions: {len(pending)} (already posted to Slack, waiting for CEO response — do NOT review again)
{awaiting_ceo}
"""

        if agent_role == "worker":
            comp_slug = agent_state.get("competition_slug", "")
            if comp_slug and comp_slug in active_comps:
                comp = active_comps[comp_slug]
                context += f"""
Competition: {comp.get('name', comp_slug)}
Deadline: {comp.get('deadline', 'unknown')}
Best rank: {comp.get('our_best_rank', 'N/A')}
Best score: {comp.get('our_best_score', 'N/A')}
"""

        return context

    def _parse_response(self, text: str) -> HeartbeatResult:
        """Parse the model's response into a HeartbeatResult."""
        text_upper = text.strip().upper()
        action = "idle"
        reason = ""
        task = ""

        for line in text_upper.split("\n"):
            line = line.strip()
            if line.startswith("ACTION:"):
                action_str = line.split(":", 1)[1].strip().lower()
                if action_str in ("idle", "wake", "check_gpu", "report"):
                    action = action_str
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
            elif line.startswith("TASK:"):
                task = line.split(":", 1)[1].strip()

        return HeartbeatResult(action=action, reason=reason, task_description=task)
