"""Tests for inter-agent communication."""

from __future__ import annotations

import asyncio

import pytest

from src.comms.inter_agent import CommHub


class TestCommHub:
    @pytest.mark.asyncio
    async def test_send_and_receive(self) -> None:
        hub = CommHub()
        hub.register_agent("vp")
        hub.register_agent("worker-1")

        await hub.send("vp", "worker-1", "task", {"do": "something"})
        msg = await hub.receive("worker-1", timeout=1.0)
        assert msg is not None
        assert msg["from"] == "vp"
        assert msg["type"] == "task"
        assert msg["payload"] == {"do": "something"}

    @pytest.mark.asyncio
    async def test_receive_timeout(self) -> None:
        hub = CommHub()
        hub.register_agent("agent-1")
        msg = await hub.receive("agent-1", timeout=0.1)
        assert msg is None

    @pytest.mark.asyncio
    async def test_send_to_unregistered(self) -> None:
        hub = CommHub()
        result = await hub.send("vp", "nonexistent", "task", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_broadcast(self) -> None:
        hub = CommHub()
        hub.register_agent("vp")
        hub.register_agent("w1")
        hub.register_agent("w2")

        count = await hub.broadcast("vp", "announcement", {"msg": "hello"})
        assert count == 2

        msg1 = await hub.receive("w1", timeout=1.0)
        msg2 = await hub.receive("w2", timeout=1.0)
        assert msg1 is not None
        assert msg2 is not None
        assert msg1["type"] == "announcement"

    def test_has_messages(self) -> None:
        hub = CommHub()
        hub.register_agent("a")
        assert hub.has_messages("a") is False

    @pytest.mark.asyncio
    async def test_has_messages_after_send(self) -> None:
        hub = CommHub()
        hub.register_agent("a")
        await hub.send("b", "a", "ping", {})
        assert hub.has_messages("a") is True

    def test_list_agents(self) -> None:
        hub = CommHub()
        hub.register_agent("a")
        hub.register_agent("b")
        assert sorted(hub.list_agents()) == ["a", "b"]

    def test_unregister(self) -> None:
        hub = CommHub()
        hub.register_agent("a")
        hub.unregister_agent("a")
        assert "a" not in hub.list_agents()
