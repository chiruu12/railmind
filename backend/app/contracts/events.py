"""Event bus topics and payloads — the inter-workstream contract.

FROZEN after Phase 0. Changes only via the main session, mirrored in
frontend/src/api/types.ts and docs/CONTRACTS.md.

Every event is a Pydantic model with a class-level `topic`. On the wire
(WebSocket /ws) events are wrapped in EventEnvelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.contracts.entities import (
    AgentDecision,
    AlertSeverity,
    DecisionStatus,
    ScenarioType,
    TrainStatus,
)


class Event(BaseModel):
    """Base class. `topic` identifies the pub/sub channel."""

    topic: ClassVar[str]


class EventEnvelope(BaseModel):
    """Wire format for WS /ws and the audit log."""

    topic: str
    ts: datetime
    payload: dict[str, Any]


# ── Sim → world ──────────────────────────────────────────────────────────────


class SimTick(Event):
    topic: ClassVar[str] = "sim.tick"

    sim_time: datetime
    sim_speed: float
    running: bool


class TrainPosition(Event):
    topic: ClassVar[str] = "train.position"

    train_number: str
    lat: float
    lon: float
    km_offset: float
    speed_kmph: float
    status: TrainStatus
    delay_min: int


class TrainStatusChanged(Event):
    topic: ClassVar[str] = "train.status"

    train_number: str
    status: TrainStatus
    delay_min: int
    next_station: str | None
    eta_next: datetime | None


# ── Agent events ─────────────────────────────────────────────────────────────


class DelayDetected(Event):
    topic: ClassVar[str] = "delay.detected"

    train_number: str
    delay_min: int
    cause: str
    downstream_stops: list[str] = Field(description="Station codes still ahead")


class PlatformConflict(Event):
    topic: ClassVar[str] = "platform.conflict"

    station_code: str
    platform: int
    train_numbers: list[str]
    window_start: datetime
    window_end: datetime


class PlatformReassigned(Event):
    topic: ClassVar[str] = "platform.reassigned"

    station_code: str
    train_number: str
    old_platform: int
    new_platform: int
    rationale: str
    decision_id: str


class CrewDutyBreach(Event):
    topic: ClassVar[str] = "crew.duty_breach"

    crew_id: str
    train_number: str
    projected_hours: float
    limit_hours: float
    breach_station: str = Field(description="Station code where projection exceeds limit")


class CrewSwapped(Event):
    topic: ClassVar[str] = "crew.swapped"

    old_crew_id: str
    new_crew_id: str
    train_number: str
    station_code: str = Field(description="Where the swap happens")
    rationale: str
    decision_id: str


class PassengerAlert(Event):
    topic: ClassVar[str] = "passenger.alert"

    severity: AlertSeverity
    train_number: str
    message: str
    channels: list[str] = Field(default=["app"], examples=[["app", "display", "announcement"]])


class AgentThought(Event):
    """Streaming reasoning shown live in the UI agent feed."""

    topic: ClassVar[str] = "agent.thought"

    agent: str = Field(examples=["crew-agent"])
    text: str
    decision_id: str | None = None


class DecisionProposed(Event):
    topic: ClassVar[str] = "decision.proposed"

    decision: AgentDecision


class DecisionResolved(Event):
    topic: ClassVar[str] = "decision.resolved"

    decision_id: str
    status: DecisionStatus
    resolved_by: str = Field(examples=["orchestrator", "human", "auto"])
    note: str | None = None


# ── Control plane ────────────────────────────────────────────────────────────


class ScenarioInjected(Event):
    topic: ClassVar[str] = "scenario.injected"

    scenario_type: ScenarioType
    params: dict[str, Any] = Field(
        examples=[{"train_number": "12302", "delay_min": 25, "cause": "loco failure near GZB"}]
    )


class KPIUpdated(Event):
    topic: ClassVar[str] = "kpi.updated"

    total_delay_min: int
    knock_on_delays_avoided: int
    pct_instant_platforming: float
    decisions_made: int


ALL_EVENT_TYPES: list[type[Event]] = [
    SimTick,
    TrainPosition,
    TrainStatusChanged,
    DelayDetected,
    PlatformConflict,
    PlatformReassigned,
    CrewDutyBreach,
    CrewSwapped,
    PassengerAlert,
    AgentThought,
    DecisionProposed,
    DecisionResolved,
    ScenarioInjected,
    KPIUpdated,
]

TOPIC_TO_EVENT: dict[str, type[Event]] = {e.topic: e for e in ALL_EVENT_TYPES}
