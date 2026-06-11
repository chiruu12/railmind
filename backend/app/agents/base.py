"""Shared agent machinery: thoughts, decisions, cooldowns, retry ledger.

No framework imports here — only contracts, the bus and the AgentRuntime
adapter. All domain time comes from sim.state().sim_time (never wall clock).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.bus.bus import EventBus
from app.contracts.entities import (
    AgentDecision,
    Crew,
    DecisionStatus,
    NetworkState,
    PlatformAssignment,
    StationStop,
)
from app.contracts.events import AgentThought, DecisionProposed

logger = logging.getLogger(__name__)

COOLDOWN_SEC = 120.0  # identical trigger suppressed within this many sim-seconds

RetryFn = Callable[[set[str]], Awaitable[None]]


class SimReader(Protocol):
    """The read-only slice of SimEngine the agents use (see docs/CONTRACTS.md)."""

    def state(self) -> NetworkState: ...
    def get_platform_board(self, station_code: str) -> list[PlatformAssignment]: ...
    def find_feasible_platforms(self, station_code: str, train_number: str) -> list[int]: ...
    def project_downstream_impact(self, train_number: str, delay_min: int) -> list[StationStop]: ...
    def check_duty(self, crew_id: str, train_number: str) -> tuple[float, float, str]: ...
    def find_spare_crews(self, station_code: str | None = None) -> list[Crew]: ...


@dataclass
class ProposalRecord:
    """Ledger entry for one decision.proposed — lets the orchestrator detect
    resource conflicts and re-trigger the originating flow on human rejection."""

    decision: AgentDecision
    resource: str  # e.g. "platform:CNB:3" or "crew:CR-201"
    retry: RetryFn | None = None
    excluded: set[str] = field(default_factory=set)


class DecisionLedger:
    """Shared dec-NNNN counter + per-decision records (one per register_agents)."""

    def __init__(self) -> None:
        self._counter = 0
        self._records: dict[str, ProposalRecord] = {}

    def next_id(self) -> str:
        self._counter += 1
        return f"dec-{self._counter:04d}"

    def record(self, rec: ProposalRecord) -> None:
        self._records[rec.decision.id] = rec

    def get(self, decision_id: str) -> ProposalRecord | None:
        return self._records.get(decision_id)


class CooldownRegistry:
    """Skips identical triggers within COOLDOWN_SEC of sim-time.

    Keyed by (agent, train, station); a trigger is "identical" when its
    fingerprint matches the last handled one. Sim-time only — never wall clock.
    """

    def __init__(self, sim: SimReader, window_sec: float = COOLDOWN_SEC) -> None:
        self._sim = sim
        self._window = window_sec
        self._seen: dict[tuple[str, str, str], tuple[str, datetime]] = {}

    def should_handle(self, agent: str, train: str, station: str, fingerprint: str) -> bool:
        now = self._sim.state().sim_time
        key = (agent, train, station)
        prev = self._seen.get(key)
        if prev is not None:
            prev_fp, prev_time = prev
            if prev_fp == fingerprint and (now - prev_time).total_seconds() < self._window:
                return False
        self._seen[key] = (fingerprint, now)
        return True


class BaseAgent:
    """Common plumbing: bus access, streamed thoughts, decision proposals."""

    name: str = "agent"

    def __init__(
        self,
        bus: EventBus,
        sim: SimReader,
        ledger: DecisionLedger,
        cooldowns: CooldownRegistry,
    ) -> None:
        self.bus = bus
        self.sim = sim
        self.ledger = ledger
        self.cooldowns = cooldowns

    async def think(self, text: str, decision_id: str | None = None) -> None:
        """Publish one streamed reasoning step to the live agent feed."""
        await self.bus.publish(AgentThought(agent=self.name, text=text, decision_id=decision_id))

    async def propose(
        self,
        trigger: str,
        options_considered: list[str],
        chosen: str,
        rationale: str,
        resource: str,
        retry: RetryFn | None = None,
        excluded: set[str] | None = None,
        status: DecisionStatus = DecisionStatus.PROPOSED,
        on_created: Callable[[AgentDecision], None] | None = None,
    ) -> AgentDecision:
        """Create an AgentDecision (shared dec-NNNN counter) and publish it.

        `on_created` runs after the decision exists but BEFORE it is published:
        publishing cascades synchronously (the orchestrator may resolve the
        decision within this await), so any state keyed by the decision id —
        e.g. an agent's pending-execution map — must be set here.
        """
        decision = AgentDecision(
            id=self.ledger.next_id(),
            ts=self.sim.state().sim_time,
            agent=self.name,
            trigger=trigger,
            options_considered=options_considered,
            chosen=chosen,
            rationale=rationale,
            status=status,
        )
        self.ledger.record(
            ProposalRecord(
                decision=decision, resource=resource, retry=retry, excluded=set(excluded or set())
            )
        )
        if on_created is not None:
            on_created(decision)
        await self.bus.publish(DecisionProposed(decision=decision))
        return decision

    def subscribe(self, topic: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        self.bus.subscribe(topic, handler)
