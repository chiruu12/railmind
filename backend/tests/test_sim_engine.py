"""Tick / position math, status transitions, lifecycle (WS1)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.bus.bus import EventBus
from app.contracts.entities import KPISnapshot, ScenarioType, Train, TrainStatus
from app.contracts.events import Event, ScenarioInjected
from app.sim.engine import SIM_START, SimEngine

DATA_DIR = "../data"


def t(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 6, 13, hour, minute, second)


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


def train(engine: SimEngine, number: str) -> Train:
    return next(tr for tr in engine.state().trains if tr.number == number)


async def test_scheduled_before_origin_departure(engine: SimEngine) -> None:
    await engine._advance_to(t(7, 59))
    assert train(engine, "22436").status is TrainStatus.SCHEDULED  # departs 08:00
    assert train(engine, "12302").status is TrainStatus.RUNNING  # departed 07:55


async def test_position_interpolates_linearly(engine: SimEngine) -> None:
    # 12302: NDLS (km 0) dep 07:55 -> CNB (km 440) arr 09:10; midpoint at 08:32:30.
    await engine._advance_to(t(8, 0))
    await engine._advance_to(t(8, 32, 30))
    tr = train(engine, "12302")
    assert abs(tr.km_offset - 220.0) < 0.5
    assert tr.speed_kmph == pytest.approx(440 / (75 / 60), rel=0.01)
    # lat/lon interpolated halfway between NDLS and CNB
    ndls, cnb = engine._stations_by_code["NDLS"], engine._stations_by_code["CNB"]
    loc = engine._locate(tr, t(8, 32, 30))
    assert loc.lat == pytest.approx((ndls.lat + cnb.lat) / 2, abs=1e-6)
    assert loc.lon == pytest.approx((ndls.lon + cnb.lon) / 2, abs=1e-6)


async def test_reverse_direction_train_km_decreases(engine: SimEngine) -> None:
    # 12506: DDU (km 786) dep 07:30 -> PRYJ (km 632) arr 08:25.
    await engine._advance_to(t(8, 0))
    tr = train(engine, "12506")
    assert tr.status is TrainStatus.RUNNING
    assert 632 < tr.km_offset < 786


async def test_dwell_and_termination(engine: SimEngine) -> None:
    now = SIM_START
    await engine._advance_to(now)
    await engine._advance_to(t(8, 22))
    assert train(engine, "12398").status is TrainStatus.AT_PLATFORM  # CNB 08:20-08:25
    await engine._advance_to(t(8, 27))
    assert train(engine, "12398").status is TrainStatus.RUNNING
    await engine._advance_to(t(10, 30))
    assert train(engine, "12398").status is TrainStatus.TERMINATED  # DDU arr 10:30


async def test_delayed_status_at_threshold(engine: SimEngine, bus: EventBus) -> None:
    await engine._advance_to(t(8, 0))
    await bus.publish(
        ScenarioInjected(
            scenario_type=ScenarioType.DELAY,
            params={"train_number": "12302", "delay_min": 25, "cause": "test"},
        )
    )
    assert train(engine, "12302").status is TrainStatus.DELAYED
    await engine._advance_to(t(8, 5))
    assert train(engine, "12302").status is TrainStatus.DELAYED


async def test_tick_emits_expected_events(engine: SimEngine, published: list[Event]) -> None:
    await engine._advance_to(t(8, 0))
    topics = [e.topic for e in published]
    assert topics[0] == "sim.tick"
    assert "train.position" in topics
    assert "train.status" in topics
    # second identical tick: no duplicate status events
    published.clear()
    await engine._advance_to(t(8, 0, 30))
    assert "train.status" not in [e.topic for e in published]
    assert "train.position" in [e.topic for e in published]


async def test_delay_shifts_eta_downstream(engine: SimEngine, bus: EventBus) -> None:
    await engine._advance_to(t(8, 0))
    await bus.publish(
        ScenarioInjected(
            scenario_type=ScenarioType.DELAY,
            params={"train_number": "12302", "delay_min": 25, "cause": "test"},
        )
    )
    # Scheduled CNB arrival 09:10; with +25 the train is still short of CNB at 09:15.
    await engine._advance_to(t(9, 15))
    tr = train(engine, "12302")
    assert tr.status is TrainStatus.DELAYED
    assert tr.km_offset < 440
    await engine._advance_to(t(9, 36))
    assert train(engine, "12302").status is TrainStatus.AT_PLATFORM


async def test_start_pause_realtime_loop(engine: SimEngine) -> None:
    engine.set_speed(60.0)  # 1 real second = 1 sim hour
    await engine.start()
    assert engine.state().running
    await asyncio.sleep(2.2)
    await engine.pause()
    state = engine.state()
    assert not state.running
    assert state.sim_time >= t(9, 0)  # advanced at least one sim hour
    frozen = state.sim_time
    await asyncio.sleep(0.3)
    assert engine.state().sim_time == frozen


async def test_reset_restores_seed_state(engine: SimEngine, bus: EventBus) -> None:
    await engine._advance_to(t(9, 0))
    await bus.publish(
        ScenarioInjected(
            scenario_type=ScenarioType.DELAY,
            params={"train_number": "12302", "delay_min": 25, "cause": "test"},
        )
    )
    await engine.reset()
    state = engine.state()
    assert state.sim_time == SIM_START
    assert not state.running
    assert state.kpis == KPISnapshot()
    assert all(tr.delay_min == 0 for tr in state.trains)
    assert all(tr.status is TrainStatus.SCHEDULED for tr in state.trains)
    assert engine.delay_causes == {}
    # twin works again after reset
    await engine._advance_to(t(8, 0))
    assert train(engine, "12302").status is TrainStatus.RUNNING
