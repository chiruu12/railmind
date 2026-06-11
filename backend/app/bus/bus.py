"""In-process async event bus — hardened (WS2).

Public interface is FROZEN: subscribe(topic, handler), publish(event),
EventBus.envelope(event). Topic "*" receives every event (used by the
audit sink and any catch-all subscriber).

WS2 extensions (internals only — additive, no caller breaks):
- every publish wraps the event in a single EventEnvelope, kept in an
  in-memory replay buffer (last 200) so the WS layer can send recent
  history to late subscribers: `replay()`.
- synchronous envelope listeners (`add_envelope_listener` /
  `remove_envelope_listener`) receive that same envelope — used by the
  SQLite audit sink and the /ws fan-out, so wire ts is consistent
  across replay, live stream and audit log.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.contracts.events import Event, EventEnvelope

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]
EnvelopeListener = Callable[[EventEnvelope], None]

WILDCARD = "*"
REPLAY_SIZE = 200


class EventBus:
    def __init__(self, replay_size: int = REPLAY_SIZE) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._replay: deque[EventEnvelope] = deque(maxlen=replay_size)
        self._envelope_listeners: list[EnvelopeListener] = []

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register an async handler for a topic ('*' for all events)."""
        self._handlers[topic].append(handler)

    def add_envelope_listener(self, listener: EnvelopeListener) -> None:
        """Register a synchronous listener called with every published envelope.

        Listeners must be fast and non-blocking (queue put, sqlite insert).
        Exceptions are logged, never propagated.
        """
        self._envelope_listeners.append(listener)

    def remove_envelope_listener(self, listener: EnvelopeListener) -> None:
        try:
            self._envelope_listeners.remove(listener)
        except ValueError:
            pass

    def replay(self) -> list[EventEnvelope]:
        """Snapshot of the most recent envelopes (oldest first)."""
        return list(self._replay)

    async def publish(self, event: Event) -> None:
        """Deliver to all topic + wildcard handlers concurrently.

        Handler exceptions are logged, never propagated — one broken
        subscriber must not break the cascade.
        """
        env = self.envelope(event)
        self._replay.append(env)
        for listener in list(self._envelope_listeners):
            try:
                listener(env)
            except Exception:
                logger.exception("bus envelope listener failed for %s", event.topic)

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
