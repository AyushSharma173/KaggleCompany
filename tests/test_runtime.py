"""Tests for the agent runtime: context_loader, prompt_cache, tool_executor, agent loop."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest

from src.runtime.context_loader import ContextLoader
from src.runtime.prompt_cache import PromptCacheManager
from src.runtime.tool_executor import ToolExecutor
from src.tools import AgentRole, ToolDefinition, ToolRegistry


# --- ContextLoader tests ---


class TestContextLoader:
    def test_build_empty(self) -> None:
        cl = ContextLoader()
        assert cl.build_messages() == []

    def test_append_and_build(self) -> None:
        cl = ContextLoader()
        cl.append_user("Hello")
        cl.append_assistant("Hi there")
        msgs = cl.build_messages()
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "Hello"}
        assert msgs[1] == {"role": "assistant", "content": "Hi there"}

    def test_sliding_window(self) -> None:
        cl = ContextLoader(max_messages=3)
        for i in range(5):
            cl.append_user(f"msg-{i}")
        msgs = cl.build_messages()
        assert len(msgs) == 3
        assert msgs[0]["content"] == "msg-2"

    def test_pinned_messages_always_included(self) -> None:
        cl = ContextLoader(max_messages=2)
        cl.pin_message({"role": "user", "content": "pinned"})
        for i in range(5):
            cl.append_user(f"msg-{i}")
        msgs = cl.build_messages()
        assert msgs[0]["content"] == "pinned"
        assert len(msgs) == 3  # 1 pinned + 2 recent

    def test_clear_history(self) -> None:
        cl = ContextLoader()
        cl.pin_message({"role": "user", "content": "pinned"})
        cl.append_user("msg1")
        cl.clear_history()
        msgs = cl.build_messages()
        assert len(msgs) == 1  # only pinned
        assert msgs[0]["content"] == "pinned"

    def test_tool_results(self) -> None:
        cl = ContextLoader()
        cl.append_tool_results([
            {"type": "tool_result", "tool_use_id": "123", "content": "ok"}
        ])
        msgs = cl.build_messages()
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"][0]["type"] == "tool_result"

    def test_compact(self) -> None:
        cl = ContextLoader()
        for i in range(10):
            cl.append_user(f"msg-{i}")
        cl.compact("Summary of previous work")
        msgs = cl.build_messages()
        assert len(msgs) == 1
        assert "Summary" in msgs[0]["content"]

    def test_message_count(self) -> None:
        cl = ContextLoader()
        cl.pin_message({"role": "user", "content": "p"})
        cl.append_user("a")
        cl.append_user("b")
        assert cl.message_count() == 3


# --- PromptCacheManager tests ---


class TestPromptCacheManager:
    def test_load_constitution(self, tmp_path: Path) -> None:
        const_file = tmp_path / "constitution.md"
        const_file.write_text("# VP Agent\nYou are the VP.")
        pcm = PromptCacheManager(str(const_file))
        assert "VP Agent" in pcm.constitution

    def test_fallback_if_missing(self, tmp_path: Path) -> None:
        pcm = PromptCacheManager(str(tmp_path / "nonexistent.md"))
        assert "not found" in pcm.constitution

    def test_system_blocks_structure(self, tmp_path: Path) -> None:
        const_file = tmp_path / "constitution.md"
        const_file.write_text("Constitution text")
        pcm = PromptCacheManager(str(const_file))
        pcm.set_dynamic_state("Dynamic state here")
        blocks = pcm.get_system_blocks()
        assert len(blocks) == 2
        # First block is cached
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks[0]["text"] == "Constitution text"
        # Second block is dynamic
        assert "cache_control" not in blocks[1]
        assert blocks[1]["text"] == "Dynamic state here"

    def test_system_blocks_no_dynamic(self, tmp_path: Path) -> None:
        const_file = tmp_path / "constitution.md"
        const_file.write_text("Constitution text")
        pcm = PromptCacheManager(str(const_file))
        blocks = pcm.get_system_blocks()
        assert len(blocks) == 1


# --- ToolExecutor tests ---


def _make_registry() -> ToolRegistry:
    """Create a registry with a test tool."""
    registry = ToolRegistry()

    async def echo_handler(params: dict) -> str:
        return f"echo: {params.get('text', '')}"

    async def fail_handler(params: dict) -> str:
        raise ValueError("intentional failure")

    registry.register(ToolDefinition(
        name="echo",
        description="Echo text back",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo_handler,
        allowed_roles={AgentRole.VP, AgentRole.WORKER},
    ))
    registry.register(ToolDefinition(
        name="fail_tool",
        description="Always fails",
        input_schema={"type": "object", "properties": {}},
        handler=fail_handler,
        allowed_roles={AgentRole.VP},
    ))
    return registry


class TestToolExecutor:
    def test_access_check_allowed(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.VP, "vp-001", registry)
        assert executor.check_access("echo") is True

    def test_access_check_denied(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.SUBAGENT, "sub-001", registry)
        assert executor.check_access("echo") is False

    def test_access_check_unknown_tool(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.VP, "vp-001", registry)
        assert executor.check_access("nonexistent") is False

    @pytest.mark.asyncio
    async def test_execute_batch_success(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.VP, "vp-001", registry)
        blocks = [
            {"type": "tool_use", "id": "call-1", "name": "echo", "input": {"text": "hello"}},
        ]
        results = await executor.execute_batch(blocks)
        assert len(results) == 1
        assert results[0]["content"] == "echo: hello"
        assert results[0].get("is_error") is None

    @pytest.mark.asyncio
    async def test_execute_batch_denied(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.SUBAGENT, "sub-001", registry)
        blocks = [
            {"type": "tool_use", "id": "call-1", "name": "echo", "input": {"text": "hello"}},
        ]
        results = await executor.execute_batch(blocks)
        assert results[0]["is_error"] is True
        assert "permission" in results[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_execute_batch_tool_error(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.VP, "vp-001", registry)
        blocks = [
            {"type": "tool_use", "id": "call-1", "name": "fail_tool", "input": {}},
        ]
        results = await executor.execute_batch(blocks)
        assert results[0]["is_error"] is True
        assert "intentional failure" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_execute_batch_skips_non_tool_blocks(self) -> None:
        registry = _make_registry()
        executor = ToolExecutor(AgentRole.VP, "vp-001", registry)
        blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "id": "call-1", "name": "echo", "input": {"text": "test"}},
        ]
        results = await executor.execute_batch(blocks)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_budget_exceeded(self) -> None:
        registry = _make_registry()
        budget = MagicMock()
        budget.check_budget = MagicMock(return_value=False)
        executor = ToolExecutor(AgentRole.VP, "vp-001", registry, budget_tracker=budget)
        blocks = [
            {"type": "tool_use", "id": "call-1", "name": "echo", "input": {"text": "test"}},
        ]
        results = await executor.execute_batch(blocks)
        assert results[0]["is_error"] is True
        assert "budget" in results[0]["content"].lower()


# --- ToolRegistry tests ---


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = _make_registry()
        assert registry.get("echo") is not None
        assert registry.get("nonexistent") is None

    def test_list_all(self) -> None:
        registry = _make_registry()
        assert sorted(registry.list_all()) == ["echo", "fail_tool"]

    def test_get_tools_for_role(self) -> None:
        registry = _make_registry()
        vp_tools = registry.get_tools_for_role(AgentRole.VP)
        assert len(vp_tools) == 2  # echo + fail_tool
        worker_tools = registry.get_tools_for_role(AgentRole.WORKER)
        assert len(worker_tools) == 1  # only echo
        sub_tools = registry.get_tools_for_role(AgentRole.SUBAGENT)
        assert len(sub_tools) == 0

    def test_tool_format(self) -> None:
        registry = _make_registry()
        tools = registry.get_tools_for_role(AgentRole.VP)
        tool = next(t for t in tools if t["name"] == "echo")
        assert "description" in tool
        assert "input_schema" in tool
