"""Core domain entities — the inter-workstream contract.

FROZEN after Phase 0. Changes only via the main session, mirrored in
frontend/src/api/types.ts and docs/CONTRACTS.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, IntEnum

from pydantic import BaseModel, Field


class TrainStatus(str, Enum):
    SCHEDULED = "scheduled"  # not yet departed origin
    RUNNING = "running"  # between stations, on time (delay < 5 min)
    DELAYED = "delayed"  # running with delay >= 5 min
    AT_PLATFORM = "at_platform"
    TERMINATED = "terminated"


class TrainPriority(IntEnum):
    PREMIUM = 1  # Vande Bharat / Rajdhani — never held if avoidable
    EXPRESS = 2
    LOCAL = 3


class CrewStatus(str, Enum):
    ON_DUTY = "on_duty"
    SPARE = "spare"
    OFF_DUTY = "off_duty"


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"  # awaiting orchestrator/human
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO = "auto"  # executed without approval (low-stakes, e.g. alerts)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ScenarioType(str, Enum):
    DELAY = "delay"  # params: train_number, delay_min, cause
    PLATFORM_BLOCK = "platform_block"  # params: station_code, platform, duration_min
    CREW_SICK = "crew_sick"  # params: crew_id


class Station(BaseModel):
    code: str = Field(examples=["CNB"])
    name: str = Field(examples=["Kanpur Central"])
    lat: float
    lon: float
    platform_count: int
    km_offset: float = Field(description="Position along the corridor from origin, km")


class StationStop(BaseModel):
    station_code: str
    sched_arrival: datetime | None = Field(default=None, description="None at origin")
    sched_departure: datetime | None = Field(default=None, description="None at terminus")
    platform: int = Field(description="Currently assigned platform (seeded, may be reassigned)")


class Train(BaseModel):
    number: str = Field(examples=["12302"])
    name: str = Field(examples=["Howrah Rajdhani"])
    priority: TrainPriority
    route: list[StationStop]
    status: TrainStatus = TrainStatus.SCHEDULED
    delay_min: int = 0
    km_offset: float = Field(default=0.0, description="Live position along corridor")
    speed_kmph: float = 0.0
    crew_id: str | None = None


class Crew(BaseModel):
    id: str = Field(examples=["CR-101"])
    name: str
    home_station: str = Field(description="Station code where crew is based/available")
    assigned_train: str | None = None
    duty_start: datetime | None = Field(default=None, description="None while spare")
    max_duty_hours: float = 9.0
    status: CrewStatus = CrewStatus.SPARE


class PlatformAssignment(BaseModel):
    station_code: str
    platform: int
    train_number: str
    arrival: datetime
    departure: datetime


class AgentDecision(BaseModel):
    """Audit-log record for every consequential agent decision."""

    id: str = Field(description="Short unique id, e.g. 'dec-0007'")
    ts: datetime
    agent: str = Field(examples=["station-agent:CNB"])
    trigger: str = Field(description="Human-readable description of the triggering event")
    options_considered: list[str] = Field(
        description="Rule-validated feasible options the LLM chose between"
    )
    chosen: str
    rationale: str = Field(description="LLM (or template) explanation, shown in UI")
    status: DecisionStatus = DecisionStatus.PROPOSED


class KPISnapshot(BaseModel):
    total_delay_min: int = 0
    knock_on_delays_avoided: int = 0
    pct_instant_platforming: float = 100.0
    decisions_made: int = 0


class NetworkState(BaseModel):
    """Full twin snapshot — response of GET /api/state."""

    sim_time: datetime
    sim_speed: float = Field(description="Sim-minutes per real second")
    running: bool
    trains: list[Train]
    stations: list[Station]
    assignments: list[PlatformAssignment]
    crews: list[Crew]
    kpis: KPISnapshot
