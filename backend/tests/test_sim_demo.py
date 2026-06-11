"""The engineered demo cascade at the deterministic-helper level + baseline mode (WS1).

Demo: +25 min on 12302 at sim 08:05 ->
- CNB platform 1 conflicts for 12560 (P2 held by terminating 12420, P4 by 64581;
  platform 3 is the feasible escape)
- crew CR-101 projects a duty breach at DDU; CR-201 is the spare at PRYJ
- baseline run: 12560 waits at CNB behind 12302 and its delay grows
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.bus.bus import EventBus
from app.contracts.entities import ScenarioType, Train, TrainStatus
from app.contracts.events import PlatformReassigned, ScenarioInjected
from app.sim.engine import SimEngine

DATA_DIR = "../data"

DEMO_DELAY = ScenarioInjected(
    scenario_type=ScenarioType.DELAY,
    params={"train_number": "12302", "delay_min": 25, "cause": "loco failure near GZB"},
)


def t(hour: int, minute: int) -> datetime:
    return datetime(2026, 6, 13, hour, minute)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def train(engine: SimEngine, number: str) -> Train:
    return next(tr for tr in engine.state().trains if tr.number == number)


async def ticks(engine: SimEngine, start: datetime, end: datetime, step_min: int = 1) -> None:
    now = start
    while now <= end:
        await engine._advance_to(now)
        now += timedelta(minutes=step_min)


async def test_demo_platform_conflict_and_escape(bus: EventBus) -> None:
    engine = SimEngine(bus, data_dir=DATA_DIR)
    await engine._advance_to(t(8, 0))
    await engine._advance_to(t(8, 5))

    assert engine.find_feasible_platforms("CNB", "12560") == [1, 3]  # P2: 12420, P4: 64581
    await bus.publish(DEMO_DELAY)
    assert engine.find_feasible_platforms("CNB", "12560") == [3]  # P1 now conflicts with 12302


async def test_demo_duty_breach_and_spare(bus: EventBus) -> None:
    engine = SimEngine(bus, data_dir=DATA_DIR)
    await engine._advance_to(t(8, 0))

    hours_before, limit, breach_before = engine.check_duty("CR-101", "12302")
    assert breach_before == ""
    assert hours_before == pytest.approx(9.0, abs=0.01)  # 02:00 -> 11:00 exactly at limit

    await bus.publish(DEMO_DELAY)
    hours, limit, breach = engine.check_duty("CR-101", "12302")
    assert limit == 9.0
    assert hours == pytest.approx(9.42, abs=0.01)  # 02:00 -> 11:25
    assert breach == "DDU"

    assert [c.id for c in engine.find_spare_crews("PRYJ")] == ["CR-201"]
    assert {c.id for c in engine.find_spare_crews()} == {"CR-201", "CR-202"}
    assert engine.find_spare_crews("NDLS") == []


async def test_demo_downstream_projection(bus: EventBus) -> None:
    engine = SimEngine(bus, data_dir=DATA_DIR)
    await engine._advance_to(t(8, 0))  # NDLS departure (07:55) already crossed
    stops = engine.project_downstream_impact("12302", 25)
    assert [s.station_code for s in stops] == ["CNB", "PRYJ", "DDU"]
    assert stops[0].sched_arrival == t(9, 35)
    assert stops[0].sched_departure == t(9, 40)
    assert stops[-1].sched_arrival == t(11, 25)


async def test_agent_mode_reassignment_keeps_12560_on_time(bus: EventBus) -> None:
    engine = SimEngine(bus, data_dir=DATA_DIR)
    await ticks(engine, t(8, 0), t(8, 5), step_min=5)
    await bus.publish(DEMO_DELAY)
    await bus.publish(
        PlatformReassigned(
            station_code="CNB",
            train_number="12560",
            old_platform=1,
            new_platform=3,
            rationale="12302 occupies P1 in the conflict window",
            decision_id="dec-0001",
        )
    )
    await ticks(engine, t(8, 10), t(9, 38))  # both dwell at CNB 09:35-09:40
    tr_12560 = train(engine, "12560")
    assert tr_12560.delay_min == 0
    assert tr_12560.status is TrainStatus.AT_PLATFORM
    assert train(engine, "12302").status is TrainStatus.AT_PLATFORM
    kpis = engine.state().kpis
    assert kpis.knock_on_delays_avoided == 1
    assert kpis.pct_instant_platforming == 100.0


async def test_baseline_mode_delay_grows_while_waiting(bus: EventBus) -> None:
    engine = SimEngine(bus, data_dir=DATA_DIR, baseline=True)
    await ticks(engine, t(8, 0), t(8, 5), step_min=5)
    await bus.publish(DEMO_DELAY)

    # 12302 (eff arr 09:35) takes CNB P1 first; 12560 must wait.
    await ticks(engine, t(8, 10), t(9, 38))
    assert train(engine, "12302").status is TrainStatus.AT_PLATFORM
    held = train(engine, "12560")
    assert held.status in (TrainStatus.RUNNING, TrainStatus.DELAYED)
    delay_at_938 = held.delay_min
    assert delay_at_938 >= 1

    # still waiting through 12302's departure + 5-min headway (free at 09:45)
    await ticks(engine, t(9, 39), t(9, 43))
    assert train(engine, "12560").delay_min > delay_at_938
    assert train(engine, "12560").status is not TrainStatus.AT_PLATFORM

    await ticks(engine, t(9, 44), t(9, 46))
    waited = train(engine, "12560")
    assert waited.status is TrainStatus.AT_PLATFORM
    assert waited.delay_min >= 10  # sched 09:35, actually platformed ~09:45

    kpis = engine.state().kpis
    assert kpis.knock_on_delays_avoided == 0
    assert kpis.pct_instant_platforming < 100.0
    assert kpis.total_delay_min >= 35  # 25 (12302) + knock-on on 12560


async def test_non_baseline_records_conflicted_arrival_without_holding(bus: EventBus) -> None:
    # Without agents acting, the twin lets the conflicted arrival happen but
    # pct_instant_platforming records it.
    engine = SimEngine(bus, data_dir=DATA_DIR)
    await ticks(engine, t(8, 0), t(8, 5), step_min=5)
    await bus.publish(DEMO_DELAY)
    await ticks(engine, t(8, 10), t(9, 36))
    tr_12560 = train(engine, "12560")
    assert tr_12560.status is TrainStatus.AT_PLATFORM
    assert tr_12560.delay_min == 0
    assert engine.state().kpis.pct_instant_platforming < 100.0
