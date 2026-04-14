"""In-process event bus for the workflow layer.

This is the nervous system of Kaggle Company. Tools and orchestrator code
*emit* events ("report.saved", "worker.completed", etc.); workflow handlers
*subscribe* to those events. The decision of "what happens when X occurs"
lives in handler registration (in main.py for V1, in workflows/*.yaml in V2),
not inside the tool that triggered the event.

Design properties for V1:
- Async, sequential, in-process. Handlers run in registration order, awaited
  one at a time. No threads, no fan-out, no priorities. Simplest thing that
  works.
- Every emit and every handler invocation is logged. This is the data the
  Event Log dashboard tab will eventually render. It is also how you debug
  "why did the VP wake up" questions today.
- A failing handler does not abort the chain. Errors are logged and the next
  handler still runs. Workflows should be resilient to one handler being
  buggy.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger("kaggle-company.events")

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    """In-process pub/sub for workflow events."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type. Handlers run in registration order."""
        self._handlers[event_type].append(handler)
        handler_name = getattr(handler, "__name__", repr(handler))
        logger.info("[event-bus] subscribed %s to %s", handler_name, event_type)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an event. All registered handlers run sequentially."""
        handlers = self._handlers.get(event_type, [])
        logger.info(
            "[event-bus] emit %s -> %d handler(s) (payload keys: %s)",
            event_type,
            len(handlers),
            list(payload.keys()),
        )
        for handler in handlers:
            handler_name = getattr(handler, "__name__", repr(handler))
            try:
                await handler(payload)
                logger.info(
                    "[event-bus] handler %s completed for %s",
                    handler_name,
                    event_type,
                )
            except Exception:
                logger.exception(
                    "[event-bus] handler %s failed for %s — continuing chain",
                    handler_name,
                    event_type,
                )

    def list_subscriptions(self) -> dict[str, list[str]]:
        """Return a snapshot of {event_type: [handler_names]} for inspection."""
        return {
            event: [getattr(h, "__name__", repr(h)) for h in handlers]
            for event, handlers in self._handlers.items()
        }
