"""Core agent runtime: the main loop shared by all agent types.

Each agent (VP, Worker, Subagent, Consolidation) is an instance of this
runtime with different config, constitution, and tool access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import anthropic

from src.runtime.context_loader import ContextLoader
from src.runtime.prompt_cache import PromptCacheManager
from src.runtime.reflection import Reflector
from src.runtime.tool_executor import ToolExecutor
from src.tools import AgentRole, ToolRegistry

if TYPE_CHECKING:
    from src.budget.tracker import BudgetTracker
    from src.memory.state_store import StateStore
    from src.memory.strategy import StrategyLibrary
    from src.memory.transcripts import TranscriptLogger

logger = logging.getLogger("kaggle-company.agent")


@dataclass
class AgentConfig:
    agent_id: str
    role: AgentRole
    agent_type: str = ""
    constitution_path: str = ""
    parent_id: str | None = None
    competition_slug: str | None = None
    slack_channel: str | None = None
    heartbeat_interval_s: int = 300
    max_turns: int = 200
    budget_limit: float = 100.0
    ephemeral: bool = False
    skip_reflection: bool = False


class Agent:
    """Core autonomous agent. Runs as an asyncio task."""

    def __init__(
        self,
        config: AgentConfig,
        client: anthropic.AsyncAnthropic,
        model: str,
        tool_registry: ToolRegistry,
        state_store: StateStore,
        transcript_logger: TranscriptLogger,
        strategy_library: StrategyLibrary,
        budget_tracker: BudgetTracker | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._model = model
        self._state = state_store
        self._transcript = transcript_logger
        self._strategy = strategy_library
        self._budget = budget_tracker

        self._prompt_cache = PromptCacheManager(config.constitution_path)
        self._context = ContextLoader(max_messages=100)
        self._tool_executor = ToolExecutor(
            config.role, config.agent_id, tool_registry, budget_tracker
        )
        self._reflector = Reflector(client, model)

        # Tool definitions for this agent's role
        self._tools = tool_registry.get_tools_for_role(config.role)

        self._running = False
        self._turn_count = 0
        self._last_active = time.time()

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_active(self) -> float:
        return self._last_active

    @property
    def turn_count(self) -> int:
        return self._turn_count

    async def run_task(self, task_description: str, trigger: str = "manual") -> str:
        """Execute a task. Called by heartbeat, CEO message, or another agent.

        Returns the final text response from the agent.
        """
        self._running = True
        self._turn_count = 0
        final_text = ""

        logger.info(
            "Agent %s starting task (trigger=%s): %s",
            self._config.agent_id, trigger, task_description,
        )

        # Update state — merge with existing to preserve heartbeat_interval_s, budget_limit, etc.
        existing_state = self._state.read("agents", self._config.agent_id, default={})
        existing_state.update({
            "id": self._config.agent_id,
            "role": self._config.role.value,
            "status": "running",
            "current_task": task_description,
            "trigger": trigger,
            "started_at": time.time(),
        })
        self._state.write("agents", self._config.agent_id, existing_state)

        # Set up dynamic state context
        self._update_dynamic_state(task_description)

        # Pin the task as the first user message
        self._context.clear_history()
        self._context.append_user(task_description)

        # Log task start
        self._transcript.log(self._config.agent_id, {
            "type": "task_start",
            "task": task_description,
            "trigger": trigger,
        })

        try:
            final_text = await self._agent_loop()
        except asyncio.CancelledError:
            logger.info("Agent %s task cancelled", self._config.agent_id)
            final_text = "[Task cancelled]"
        except Exception as e:
            logger.error("Agent %s task failed: %s", self._config.agent_id, e, exc_info=True)
            final_text = f"[Task failed: {e}]"
        finally:
            self._running = False
            existing_state = self._state.read("agents", self._config.agent_id, default={})
            existing_state.update({
                "status": "idle",
                "last_task": task_description,
                "turns_used": self._turn_count,
                "finished_at": time.time(),
            })
            self._state.write("agents", self._config.agent_id, existing_state)
            self._transcript.log(self._config.agent_id, {
                "type": "task_end",
                "turns": self._turn_count,
                "result_preview": final_text,
            })

        return final_text

    async def _agent_loop(self) -> str:
        """The core tool-use loop. Returns the final text output."""
        final_text = ""

        while self._running and self._turn_count < self._config.max_turns:
            self._last_active = time.time()
            self._turn_count += 1

            # Budget check before API call — hard stop, no more spending
            if self._budget and not self._budget.check_budget(self._config.agent_id):
                logger.warning("Agent %s budget exceeded, stopping immediately", self._config.agent_id)
                return "[Budget exceeded — task stopped]"

            # Call Claude
            messages = self._context.build_messages()
            system = self._prompt_cache.get_system_blocks()

            # Log full system prompt on first turn only (avoids bloat on subsequent turns)
            if self._turn_count == 1:
                self._transcript.log(self._config.agent_id, {
                    "type": "system_prompt",
                    "model": self._model,
                    "constitution": system[0]["text"] if system else "",
                    "dynamic_state": system[1]["text"] if len(system) > 1 else "",
                })

            # Log API request — what we're sending to Claude
            # Log the COMPLETE API request — everything we're sending to Anthropic
            self._transcript.log(self._config.agent_id, {
                "type": "api_request_full",
                "turn": self._turn_count,
                "model": self._model,
                "max_tokens": 32000,
                "system": system,
                "messages": messages,
                "tools": self._tools if self._tools else [],
            })

            try:
                async with self._client.messages.stream(
                    model=self._model,
                    max_tokens=32000,
                    system=system,
                    messages=messages,
                    tools=self._tools if self._tools else anthropic.NOT_GIVEN,
                ) as stream:
                    response = await stream.get_final_message()
            except anthropic.BadRequestError as e:
                error_body = getattr(e, "body", {}) or {}
                error_msg = ""
                if isinstance(error_body, dict):
                    error_msg = error_body.get("error", {}).get("message", str(e))
                else:
                    error_msg = str(e)
                if "credit balance" in error_msg or "billing" in error_msg.lower():
                    logger.error(
                        "Agent %s: API billing error — stopping. Add credits at console.anthropic.com. Error: %s",
                        self._config.agent_id, error_msg,
                    )
                    return f"[Stopped: {error_msg}]"
                logger.error("API bad request for agent %s: %s", self._config.agent_id, e)
                return f"[Stopped: bad request — {error_msg}]"
            except anthropic.APIError as e:
                logger.error("API error for agent %s: %s", self._config.agent_id, e)
                await asyncio.sleep(10)
                continue

            # Track API usage
            if self._budget and response.usage:
                self._budget.record_api_usage(
                    self._config.agent_id,
                    {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
                        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                    },
                )

            # Log transcript — capture ALL content blocks (text + tool_use)
            raw_blocks = []
            for block in response.content:
                if hasattr(block, "text"):
                    raw_blocks.append({"type": "text", "text": block.text})
                elif hasattr(block, "name"):
                    raw_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            self._transcript.log(self._config.agent_id, {
                "type": "api_response",
                "turn": self._turn_count,
                "model": self._model,
                "stop_reason": response.stop_reason,
                "usage": {
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                    "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
                    "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                } if response.usage else {},
                "text": "\n".join(
                    block.text for block in response.content if hasattr(block, "text")
                ),
                "raw_content_blocks": raw_blocks,
            })

            # Process response
            # Extract text and tool_use blocks
            content_blocks = []
            text_parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                content_blocks.append(block)

            final_text = "\n".join(text_parts)

            # Store assistant message
            self._context.append_assistant(
                [_block_to_dict(b) for b in response.content]
            )

            if response.stop_reason == "tool_use":
                # Update last_active before tool execution — long-running tools
                # like deep_research (up to 45 min) or spawn_subagents should not
                # trigger stuck detection while blocking
                self._last_active = time.time()
                # Execute tools and continue loop
                tool_results = await self._tool_executor.execute_batch(response.content)
                self._context.append_tool_results(tool_results)

                # Log tool calls
                for block in response.content:
                    if hasattr(block, "name"):
                        self._transcript.log(self._config.agent_id, {
                            "type": "tool_call",
                            "turn": self._turn_count,
                            "tool_use_id": block.id,
                            "tool_name": block.name,
                            "tool_input": block.input,
                        })
                for result in tool_results:
                    self._transcript.log(self._config.agent_id, {
                        "type": "tool_result",
                        "turn": self._turn_count,
                        "tool_use_id": result.get("tool_use_id"),
                        "is_error": result.get("is_error", False),
                        "content": result.get("content", ""),
                    })

            elif response.stop_reason == "end_turn":
                # Agent is done with this task
                logger.info(
                    "Agent %s completed task in %d turns",
                    self._config.agent_id, self._turn_count,
                )

                # Reflection step (skip for subagents and when budget exceeded)
                budget_ok = not self._budget or self._budget.check_budget(self._config.agent_id)
                if not self._config.skip_reflection and final_text and budget_ok:
                    reflection = await self._reflector.reflect(
                        messages, final_text
                    )
                    self._transcript.log(self._config.agent_id, {
                        "type": "reflection",
                        "content": reflection,
                    })

                break
            elif response.stop_reason == "max_tokens":
                # Output was truncated. Check for orphaned tool_use blocks
                # (tool call started but cut off mid-JSON). These cause API
                # errors on the next turn if not handled.
                logger.warning(
                    "Agent %s hit max_tokens on turn %d — handling truncation",
                    self._config.agent_id, self._turn_count,
                )

                # Check if the last content block is an incomplete tool_use
                has_orphaned_tool = any(
                    (hasattr(b, "type") and b.type == "tool_use") or
                    (isinstance(b, dict) and b.get("type") == "tool_use")
                    for b in response.content
                )

                if has_orphaned_tool:
                    # Provide dummy tool results for any tool_use blocks to
                    # prevent the "tool_use without tool_result" API error
                    dummy_results = []
                    for block in response.content:
                        block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                        if block_type == "tool_use":
                            tool_id = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else None)
                            dummy_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": "[Tool call was truncated due to max_tokens. Please retry this tool call.]",
                                "is_error": True,
                            })
                    if dummy_results:
                        self._context.append_tool_results(dummy_results)
                        logger.warning(
                            "Agent %s: provided %d dummy tool results for orphaned tool_use blocks",
                            self._config.agent_id, len(dummy_results),
                        )
                else:
                    # Pure text truncation — just prompt continuation
                    self._context.append_user(
                        "[System: Your previous response was truncated due to length. "
                        "Continue where you left off. If you were about to call a tool, "
                        "call it now.]"
                    )
            else:
                # Truly unexpected stop reason
                logger.warning(
                    "Agent %s unexpected stop_reason: %s",
                    self._config.agent_id, response.stop_reason,
                )
                break

        if self._turn_count >= self._config.max_turns:
            logger.warning("Agent %s hit max turns (%d)", self._config.agent_id, self._config.max_turns)

        return final_text

    def _update_dynamic_state(self, task: str) -> None:
        """Load current state into the dynamic prompt section.

        Disabled for now — dynamic state was causing confusion by duplicating
        and truncating task content. The agent gets everything it needs from
        its constitution (system block 1) and the task (user message).
        """
        # Only include competition slug if the agent has one — this is
        # essential for research workers to construct Kaggle URLs
        if self._config.competition_slug:
            self._prompt_cache.set_dynamic_state(
                f"Competition slug: {self._config.competition_slug}"
            )
        else:
            self._prompt_cache.set_dynamic_state("")

    async def stop(self) -> None:
        """Signal the agent to stop."""
        self._running = False

    async def inject_message(self, message: str, source: str = "system") -> None:
        """Inject a message into the agent's context (e.g., from CEO or another agent)."""
        self._context.append_user(f"[Message from {source}] {message}")

    async def _collect_stream(self, stream):
        """Collect a streamed API response into a final Message object."""
        content_blocks = []
        current_text = ""
        current_tool_use = None
        stop_reason = None
        usage = None

        async for event in stream:
            # Content block start events
            if event.type == "content_block_start":
                if event.content_block.type == "text":
                    current_text = ""
                elif event.content_block.type == "tool_use":
                    current_tool_use = {
                        "type": "tool_use",
                        "id": event.content_block.id,
                        "name": event.content_block.name,
                        "input": "",
                    }
            # Content block delta events (text accumulation)
            elif event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    current_text += event.delta.text
                elif hasattr(event.delta, "input_json_partial"):
                    # Handle input_json_partial for tool_use
                    if current_tool_use:
                        current_tool_use["input"] += event.delta.input_json_partial
            # Content block stop events
            elif event.type == "content_block_stop":
                if current_text:
                    # Create a text block object
                    text_block = type("TextBlock", (), {
                        "type": "text",
                        "text": current_text,
                    })()
                    content_blocks.append(text_block)
                    current_text = ""
                elif current_tool_use:
                    # Parse the accumulated input JSON
                    input_str = current_tool_use["input"].strip()
                    if input_str:
                        try:
                            tool_input = json.loads(input_str)
                        except json.JSONDecodeError:
                            # Fallback if JSON is incomplete
                            tool_input = {}
                    else:
                        tool_input = {}

                    tool_block = type("ToolUseBlock", (), {
                        "type": "tool_use",
                        "id": current_tool_use["id"],
                        "name": current_tool_use["name"],
                        "input": tool_input,
                    })()
                    content_blocks.append(tool_block)
                    current_tool_use = None
            # Message delta events (stop reason)
            elif event.type == "message_delta":
                if hasattr(event, "delta") and hasattr(event.delta, "stop_reason"):
                    stop_reason = event.delta.stop_reason
            # Message start events (usage)
            elif event.type == "message_start":
                if hasattr(event, "message") and hasattr(event.message, "usage"):
                    usage = event.message.usage
            # Message stop event with final usage
            elif event.type == "message_stop":
                # The stream end message may have final usage data
                pass

        # Create a synthetic Message object with the expected interface
        response = type("Message", (), {
            "content": content_blocks,
            "stop_reason": stop_reason,
            "usage": usage,
        })()

        return response


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an Anthropic content block to a dict for message history."""
    if hasattr(block, "text"):
        return {"type": "text", "text": block.text}
    elif hasattr(block, "name"):
        # tool_use block
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    return {"type": "text", "text": str(block)}
