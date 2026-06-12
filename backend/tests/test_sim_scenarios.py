"""Scenario injection + twin mutations via bus events (WS1)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.bus.bus import EventBus
from app.contracts.entities import CrewStatus, ScenarioType, TrainStatus
from app.contracts.events import (
    CrewSwapped,
    Event,
    PlatformReassigned,
    ScenarioInjected,
)
from app.sim.engine import SimEngine

DATA_DIR = "../data"


def t(hour: int, minute: int) -> datetime:
    return datetime(2026, 6, 13, hour, minute)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def engine(bus: EventBus) -> SimEngine:
    return SimEngine(bus, data_dir=DATA_DIR)


@pytest.fixture
def published(bus: EventBus) -> list[Event]:
    events: list[Event] = []

    async def collect(event: Event) -> None:
        events.append(event)

    bus.subscribe("*", collect)
    return events


def delay_scenario(train_number: str = "12302", delay_min: int = 25) -> ScenarioInjected:
    return ScenarioInjected(
        scenario_type=ScenarioType.DELAY,
        params={"train_number": train_number, "delay_min": delay_min, "cause": "loco failure"},
    )


async def test_delay_scenario_applies(
    engine: SimEngine, bus: EventBus, published: list[Event]
) -> None:
    await bus.publish(delay_scenario())
    tr = next(x for x in engine.state().trains if x.number == "12302")
    assert tr.delay_min == 25
    assert engine.delay_causes["12302"] == "loco failure"
    topics = [e.topic for e in published]
    assert "train.status" in topics
    assert "kpi.updated" in topics
    assert engine.state().kpis.total_delay_min == 25


async def test_delays_accumulate(engine: SimEngine, bus: EventBus) -> None:
    await bus.publish(delay_scenario(delay_min=10))
    await bus.publish(delay_scenario(delay_min=5))
    tr = next(x for x in engine.state().trains if x.number == "12302")
    assert tr.delay_min == 15


async def test_platform_block_excludes_platform(engine: SimEngine, bus: EventBus) -> None:
    assert 3 in engine.find_feasible_platforms("CNB", "12560")
    await bus.publish(
        ScenarioInjected(
            scenario_type=ScenarioType.PLATFORM_BLOCK,
            params={"station_code": "CNB", "platform": 3, "duration_min": 180},
        )
    )
    assert 3 not in engine.find_feasible_platforms("CNB", "12560")


async def test_crew_sick_goes_off_duty(engine: SimEngine, bus: EventBus) -> None:
    await bus.publish(
        ScenarioInjected(scenario_type=ScenarioType.CREW_SICK, params={"crew_id": "CR-102"})
    )
    crew = next(c for c in engine.state().crews if c.id == "CR-102")
    assert crew.status is CrewStatus.OFF_DUTY


async def test_platform_reassignment_applies_and_counts(
    engine: SimEngine, bus: EventBus, published: list[Event]
) -> None:
    await bus.publish(
        PlatformReassigned(
            station_code="CNB",
            train_number="12560",
            old_platform=1,
            new_platform=3,
            rationale="conflict with delayed 12302",
            decision_id="dec-0001",
        )
    )
    tr = next(x for x in engine.state().trains if x.number == "12560")
    stop = next(s for s in tr.route if s.station_code == "CNB")
    assert stop.platform == 3
    kpis = engine.state().kpis
    assert kpis.knock_on_delays_avoided == 1
    assert kpis.decisions_made == 1
    assert "kpi.updated" in [e.topic for e in published]
    # board reflects the reassignment
    board = engine.get_platform_board("CNB")
    assert any(a.train_number == "12560" and a.platform == 3 for a in board)


async def test_crew_swap_applies(engine: SimEngine, bus: EventBus) -> None:
    await engine._advance_to(t(10, 35))
    await bus.publish(
        CrewSwapped(
            old_crew_id="CR-101",
            new_crew_id="CR-201",
            train_number="12302",
            station_code="PRYJ",
            rationale="duty breach projected at DDU",
            decision_id="dec-0002",
        )
    )
    state = engine.state()
    old = next(c for c in state.crews if c.id == "CR-101")
    new = next(c for c in state.crews if c.id == "CR-201")
    tr = next(x for x in state.trains if x.number == "12302")
    assert old.status is CrewStatus.OFF_DUTY and old.assigned_train is None
    assert new.status is CrewStatus.ON_DUTY and new.assigned_train == "12302"
    assert new.duty_start == t(10, 35)
    assert tr.crew_id == "CR-201"
    assert state.kpis.decisions_made == 1
    assert state.kpis.knock_on_delays_avoided == 0


async def test_baseline_ignores_agent_decisions(bus: EventBus) -> None:
    engine = SimEngine(bus, data_dir=DATA_DIR, baseline=True)
    await bus.publish(
        PlatformReassigned(
            station_code="CNB",
            train_number="12560",
            old_platform=1,
            new_platform=3,
            rationale="should be ignored",
            decision_id="dec-0003",
        )
    )
    await bus.publish(
        CrewSwapped(
            old_crew_id="CR-101",
            new_crew_id="CR-201",
            train_number="12302",
            station_code="PRYJ",
            rationale="should be ignored",
            decision_id="dec-0004",
        )
    )
    state = engine.state()
    tr = next(x for x in state.trains if x.number == "12560")
    assert next(s for s in tr.route if s.station_code == "CNB").platform == 1
    assert next(c for c in state.crews if c.id == "CR-201").status is CrewStatus.SPARE
    assert state.kpis.decisions_made == 0
    assert state.kpis.knock_on_delays_avoided == 0


async def test_unknown_train_delay_is_ignored(engine: SimEngine, bus: EventBus) -> None:
    await bus.publish(delay_scenario(train_number="99999"))
    assert engine.state().kpis.total_delay_min == 0


async def test_status_still_at_platform_when_dwelling_delayed(
    engine: SimEngine, bus: EventBus
) -> None:
    # delay injected while a train dwells -> stays at_platform, dwell extends
    await engine._advance_to(t(8, 0))
    await engine._advance_to(t(8, 22))  # 12398 dwelling at CNB 08:20-08:25
    await bus.publish(delay_scenario(train_number="12398", delay_min=10))
    tr = next(x for x in engine.state().trains if x.number == "12398")
    assert tr.status is TrainStatus.AT_PLATFORM
    await engine._advance_to(t(8, 30))
    assert next(x for x in engine.state().trains if x.number == "12398").status is (
        TrainStatus.AT_PLATFORM
    )  # departs 08:35 now
    await engine._advance_to(t(8, 36))
    assert next(x for x in engine.state().trains if x.number == "12398").status is (
        TrainStatus.DELAYED
    )
