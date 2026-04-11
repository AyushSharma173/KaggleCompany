"""In-process message passing between agents.

Each agent has an asyncio.Queue. Messages are typed dicts:
{"from": agent_id, "to": agent_id, "type": str, "payload": ...}
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("kaggle-company.comms")


class CommHub:
    """In-process async message passing between agents."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def register_agent(self, agent_id: str) -> None:
        """Create a message queue for this agent."""
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
            logger.debug("Registered comm queue for %s", agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        """Remove agent's queue."""
        self._queues.pop(agent_id, None)
        logger.debug("Unregistered comm queue for %s", agent_id)

    async def send(self, from_id: str, to_id: str, msg_type: str, payload: Any) -> bool:
        """Send a message to a specific agent. Returns True if delivered."""
        queue = self._queues.get(to_id)
        if queue is None:
            logger.warning("Cannot send to %s: not registered", to_id)
            return False

        message = {
            "from": from_id,
            "to": to_id,
            "type": msg_type,
            "payload": payload,
            "timestamp": time.time(),
        }
        await queue.put(message)
        logger.debug("Message from %s to %s: %s", from_id, to_id, msg_type)
        return True

    async def receive(self, agent_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """Receive next message for agent, with timeout. Returns None on timeout."""
        queue = self._queues.get(agent_id)
        if queue is None:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def has_messages(self, agent_id: str) -> bool:
        """Check if agent has pending messages."""
        queue = self._queues.get(agent_id)
        return queue is not None and not queue.empty()

    async def broadcast(self, from_id: str, msg_type: str, payload: Any) -> int:
        """Send to all registered agents. Returns count of recipients."""
        count = 0
        for agent_id in list(self._queues.keys()):
            if agent_id != from_id:
                await self.send(from_id, agent_id, msg_type, payload)
                count += 1
        return count

    def list_agents(self) -> list[str]:
        """List all registered agent IDs."""
        return list(self._queues.keys())
