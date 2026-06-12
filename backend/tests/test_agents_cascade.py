"""WS3 agent-layer tests — LLM-free (AGENT_LLM=off), real EventBus, fake sim.

Fake sim mirrors the demo fixture: 12302 (Howrah Rajdhani, premium) +25 min
hits CNB at 09:35, conflicting with 12560 on platform 1; platforms 3/4 are
feasible; crew CR-101 breaches its 9h duty by DDU; CR-201 is the spare at
PRYJ. Feeding one train.status event must drive the full cascade with zero
LLM access.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.agents.adapter import AgentRuntime
from app.agents.registry import register_agents
from app.bus.bus import EventBus
from app.contracts.entities import (
    Crew,
    CrewStatus,
    DecisionStatus,
    KPISnapshot,
    NetworkState,
    PlatformAssignment,
    ScenarioType,
    Station,
    StationStop,
    Train,
    TrainPriority,
    TrainStatus,
)
from app.contracts.events import (
    AgentThought,
    CrewDutyBreach,
    CrewSwapped,
    DecisionProposed,
    DecisionResolved,
    DelayDetected,
    PassengerAlert,
    PlatformConflict,
    PlatformReassigned,
    ScenarioInjected,
    TrainStatusChanged,
)
from app.settings import settings

SIM_TIME = datetime(2026, 6, 13, 9, 0)
T = lambda h, m: datetime(2026, 6, 13, h, m)  # noqa: E731


@pytest.fixture(autouse=True)
def llm_off(monkeypatch):
    """Tests are always LLM-free, regardless of the ambient environment."""
    monkeypatch.setattr(settings, "agent_llm", "off")


class FakeSim:
    """Implements the CONTRACTS.md SimEngine read-only surface with demo data."""

    def __init__(self) -> None:
        self.sim_time = SIM_TIME

    def state(self) -> NetworkState:
        return NetworkState(
            sim_time=self.sim_time,
            sim_speed=1.0,
            running=True,
            trains=[
                Train(
                    number="12302",
                    name="Howrah Rajdhani",
                    priority=TrainPriority.PREMIUM,
                    crew_id="CR-101",
                    status=TrainStatus.DELAYED,
                    delay_min=25,
                    route=[
                        StationStop(station_code="NDLS", sched_departure=T(7, 55), platform=1),
                        StationStop(
                            station_code="CNB",
                            sched_arrival=T(9, 10),
                            sched_departure=T(9, 15),
                            platform=1,
                        ),
                        StationStop(
                            station_code="PRYJ",
                            sched_arrival=T(10, 10),
                            sched_departure=T(10, 15),
                            platform=1,
                        ),
                        StationStop(station_code="DDU", sched_arrival=T(11, 0), platform=1),
                    ],
                ),
                Train(
                    number="12560",
                    name="Shiv Ganga Express",
                    priority=TrainPriority.EXPRESS,
                    crew_id="CR-102",
                    status=TrainStatus.RUNNING,
                    route=[
                        StationStop(station_code="NDLS", sched_departure=T(8, 10), platform=2),
                        StationStop(
                            station_code="CNB",
                            sched_arrival=T(9, 35),
                            sched_departure=T(9, 40),
                            platform=1,
                        ),
                    ],
                ),
            ],
            stations=[
                Station(
                    code="NDLS",
                    name="New Delhi",
                    lat=28.64,
                    lon=77.22,
                    platform_count=6,
                    km_offset=0,
                ),
                Station(
                    code="CNB",
                    name="Kanpur Central",
                    lat=26.45,
                    lon=80.33,
                    platform_count=4,
                    km_offset=440,
                ),
                Station(
                    code="PRYJ",
                    name="Prayagraj Junction",
                    lat=25.44,
                    lon=81.85,
                    platform_count=4,
                    km_offset=632,
                ),
                Station(
                    code="DDU",
                    name="Pt. DDU Junction",
                    lat=25.28,
                    lon=83.12,
                    platform_count=3,
                    km_offset=786,
                ),
            ],
            assignments=[],
            crews=[
                Crew(
                    id="CR-101",
                    name="Rakesh Sharma",
                    home_station="NDLS",
                    assigned_train="12302",
                    duty_start=T(2, 0),
                    status=CrewStatus.ON_DUTY,
                ),
                Crew(id="CR-201", name="Anil Kumar", home_station="PRYJ", status=CrewStatus.SPARE),
            ],
            kpis=KPISnapshot(),
        )

    def get_platform_board(self, station_code: str) -> list[PlatformAssignment]:
        if station_code != "CNB":
            return []
        return [
            PlatformAssignment(
                station_code="CNB",
                platform=1,
                train_number="12560",
                arrival=T(9, 35),
                departure=T(9, 40),
            ),
            PlatformAssignment(
                station_code="CNB",
                platform=2,
                train_number="12420",
                arrival=T(9, 30),
                departure=T(10, 0),
            ),
            PlatformAssignment(
                station_code="CNB",
                platform=1,
                train_number="12302",
                arrival=T(9, 35),
                departure=T(9, 40),
            ),
        ]

    def find_feasible_platforms(self, station_code: str, train_number: str) -> list[int]:
        if station_code == "CNB":
            return [3, 4]  # P1 contested, P2 blocked by terminating 12420
        return [2, 3]

    def project_downstream_impact(self, train_number: str, delay_min: int) -> list[StationStop]:
        assert train_number == "12302"
        return [
            StationStop(
                station_code="CNB", sched_arrival=T(9, 35), sched_departure=T(9, 40), platform=1
            ),
            StationStop(
                station_code="PRYJ", sched_arrival=T(10, 35), sched_departure=T(10, 40), platform=1
            ),
            StationStop(station_code="DDU", sched_arrival=T(11, 25), platform=1),
        ]

    def check_duty(self, crew_id: str, train_number: str) -> tuple[float, float, str]:
        if crew_id == "CR-101":
            return (9.42, 9.0, "DDU")  # 02:00 start, DDU arrival projected 11:25
        return (5.0, 9.0, "")

    def find_spare_crews(self, station_code: str | None = None) -> list[Crew]:
        spare = Crew(id="CR-201", name="Anil Kumar", home_station="PRYJ", status=CrewStatus.SPARE)
        if station_code in (None, "PRYJ"):
            return [spare]
        return []


def make_world() -> tuple[EventBus, FakeSim, list]:
    bus = EventBus()
    sim = FakeSim()
    events: list = []

    async def collect(event) -> None:
        events.append(event)

    bus.subscribe("*", collect)
    register_agents(bus, sim)
    return bus, sim, events


def of_type(events: list, cls: type) -> list:
    return [e for e in events if isinstance(e, cls)]


async def run_demo_cascade(bus: EventBus) -> None:
    await bus.publish(
        ScenarioInjected(
            scenario_type=ScenarioType.DELAY,
            params={"train_number": "12302", "delay_min": 25, "cause": "loco failure near GZB"},
        )
    )
    await bus.publish(
        TrainStatusChanged(
            train_number="12302",
            status=TrainStatus.DELAYED,
            delay_min=25,
            next_station="CNB",
            eta_next=T(9, 35),
        )
    )


async def test_full_cascade_llm_free():
    bus, _sim, events = make_world()
    await run_demo_cascade(bus)
    topics = [e.topic for e in events]

    # delay.detected with scenario cause + downstream stops
    delays = of_type(events, DelayDetected)
    assert len(delays) == 1
    assert delays[0].train_number == "12302"
    assert delays[0].cause == "loco failure near GZB"
    assert delays[0].downstream_stops == ["CNB", "PRYJ", "DDU"]

    # platform.conflict at CNB on platform 1 between 12302 and 12560
    conflicts = of_type(events, PlatformConflict)
    assert len(conflicts) == 1
    assert conflicts[0].station_code == "CNB"
    assert conflicts[0].platform == 1
    assert set(conflicts[0].train_numbers) == {"12302", "12560"}

    # station proposal: every feasible candidate considered, deterministic choice
    station_decisions = [
        e.decision
        for e in of_type(events, DecisionProposed)
        if e.decision.agent == "station-agent:CNB"
    ]
    assert len(station_decisions) == 1
    dec = station_decisions[0]
    # shared dec-NNNN counter: sequential, no gaps, no duplicates
    all_ids = [e.decision.id for e in of_type(events, DecisionProposed)]
    assert sorted(all_ids) == [f"dec-{i:04d}" for i in range(1, len(all_ids) + 1)]
    assert dec.id in all_ids
    assert set(dec.options_considered) == {
        "move 12302 to platform 3",
        "move 12302 to platform 4",
        "move 12560 to platform 3",
        "move 12560 to platform 4",
    }
    # premium 12302 keeps platform 1; 12560 moves to lowest feasible platform
    assert dec.chosen == "move 12560 to platform 3"
    assert "12560" in dec.rationale and "platform 3" in dec.rationale

    # orchestrator approval → platform.reassigned to a feasible platform
    approvals = [
        e
        for e in of_type(events, DecisionResolved)
        if e.status == DecisionStatus.APPROVED and e.resolved_by == "orchestrator"
    ]
    assert {a.decision_id for a in approvals} >= {dec.id}
    reassignments = of_type(events, PlatformReassigned)
    assert len(reassignments) == 1
    assert reassignments[0].station_code == "CNB"
    assert reassignments[0].train_number == "12560"
    assert reassignments[0].old_platform == 1
    assert reassignments[0].new_platform == 3
    assert reassignments[0].decision_id == dec.id

    # crew breach → swap proposal → crew.swapped to CR-201 at PRYJ
    breaches = of_type(events, CrewDutyBreach)
    assert len(breaches) == 1
    assert breaches[0].crew_id == "CR-101"
    assert breaches[0].breach_station == "DDU"
    swaps = of_type(events, CrewSwapped)
    assert len(swaps) == 1
    assert swaps[0].old_crew_id == "CR-101"
    assert swaps[0].new_crew_id == "CR-201"
    assert swaps[0].station_code == "PRYJ"
    assert swaps[0].train_number == "12302"

    # passenger alerts for delay + platform change + crew swap, AUTO (no approval)
    alerts = of_type(events, PassengerAlert)
    assert len(alerts) == 3
    assert all(a.train_number in ("12302", "12560") for a in alerts)
    auto_ids = {
        e.decision.id
        for e in of_type(events, DecisionProposed)
        if e.decision.status == DecisionStatus.AUTO
    }
    assert len(auto_ids) == 3
    resolved_ids = {e.decision_id for e in of_type(events, DecisionResolved)}
    assert not (auto_ids & resolved_ids)  # AUTO decisions never go to approval

    # streaming feel + ordering
    assert len(of_type(events, AgentThought)) > 5
    assert (
        topics.index("delay.detected")
        < topics.index("platform.conflict")
        < topics.index("decision.proposed")
        < topics.index("platform.reassigned")
    )
    assert topics.index("crew.duty_breach") < topics.index("crew.swapped")


async def test_cooldown_blocks_duplicate_trigger():
    bus, _sim, events = make_world()
    await run_demo_cascade(bus)
    # identical trigger again, same frozen sim-time (well within 120 sim-seconds)
    await bus.publish(
        TrainStatusChanged(
            train_number="12302",
            status=TrainStatus.DELAYED,
            delay_min=25,
            next_station="CNB",
            eta_next=T(9, 35),
        )
    )
    assert len(of_type(events, DelayDetected)) == 1
    assert len(of_type(events, PlatformConflict)) == 1
    assert len(of_type(events, CrewSwapped)) == 1


async def test_human_rejection_retries_with_next_best_option():
    bus, _sim, events = make_world()
    await run_demo_cascade(bus)
    original = next(
        e.decision
        for e in of_type(events, DecisionProposed)
        if e.decision.agent == "station-agent:CNB"
    )
    assert original.chosen == "move 12560 to platform 3"

    # operator overrides the platform decision
    await bus.publish(
        DecisionResolved(
            decision_id=original.id,
            status=DecisionStatus.REJECTED,
            resolved_by="human",
            note="keep platform 3 free for a freight movement",
        )
    )

    proposals = [
        e.decision
        for e in of_type(events, DecisionProposed)
        if e.decision.agent == "station-agent:CNB"
    ]
    assert len(proposals) == 2
    retry = proposals[1]
    assert retry.id != original.id
    assert retry.chosen != original.chosen  # next-best, rejected option excluded
    assert retry.chosen == "move 12560 to platform 4"
    assert original.chosen not in retry.options_considered

    # the recomputed proposal is auto-approved and executed
    reassignments = of_type(events, PlatformReassigned)
    assert reassignments[-1].new_platform == 4
    assert reassignments[-1].decision_id == retry.id


async def test_adapter_template_mode_returns_deterministic_fallback():
    runtime = AgentRuntime(name="t", instructions="x", tools=[], model_tier="fast")
    out = await runtime.run("anything", context={"fallback": "rule-ranked answer"})
    assert out == "rule-ranked answer"
    # without an explicit fallback it still answers deterministically
    out2 = await runtime.run("anything", context={})
    assert isinstance(out2, str) and "t" in out2
