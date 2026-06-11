"""In-process async event bus — minimal Phase 0 implementation.

WS2 hardens this (audit sink to SQLite, replay-last-N, error isolation),
but the public interface below is FROZEN: subscribe(topic, handler),
publish(event). Topic "*" receives every event (used by the WS fan-out
and the audit sink).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from collections import defaultdict
from datetime import datetime, timezone

from app.contracts.events import Event, EventEnvelope

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]

WILDCARD = "*"


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register an async handler for a topic ('*' for all events)."""
        self._handlers[topic].append(handler)

    async def publish(self, event: Event) -> None:
        """Deliver to all topic + wildcard handlers concurrently.

        Handler exceptions are logged, never propagated — one broken
        subscriber must not break the cascade.
        """
        handlers = self._handlers[event.topic] + self._handlers[WILDCARD]
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.exception("bus handler failed for %s", event.topic, exc_info=result)

    @staticmethod
    def envelope(event: Event) -> EventEnvelope:
        return EventEnvelope(
            topic=event.topic,
            ts=datetime.now(timezone.utc),
            payload=event.model_dump(mode="json"),
        )
