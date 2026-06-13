"""Shared fixtures for the WS5 passenger endpoint tests (no tests in this file).

Builds a fake SimEngine exposing the read-only seam from docs/CONTRACTS.md
(state, get_platform_board, project_downstream_impact) over a 2-train twin:
12952 delayed 25 min, 12302 on time. Sim day 2026-06-13, sim_time 09:00.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.passenger import build_router
from app.bus.bus import EventBus
from app.contracts.entities import (
    KPISnapshot,
    NetworkState,
    PlatformAssignment,
    Station,
    StationStop,
    Train,
    TrainPriority,
    TrainStatus,
)

SIM_DAY = "2026-06-13"


def t(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{SIM_DAY}T{hhmm}:00")


STATIONS = [
    Station(code="NDLS", name="New Delhi", lat=28.64, lon=77.22, platform_count=6, km_offset=0),
    Station(
        code="CNB", name="Kanpur Central", lat=26.45, lon=80.35, platform_count=4, km_offset=440
    ),
    Station(
        code="MGS", name="Mughalsarai Jn", lat=25.28, lon=83.12, platform_count=4, km_offset=790
    ),
]


def make_trains() -> list[Train]:
    delayed = Train(
        number="12952",
        name="Tejas Rajdhani",
        priority=TrainPriority.PREMIUM,
        status=TrainStatus.DELAYED,
        delay_min=25,
        km_offset=120.0,
        speed_kmph=110.0,
        route=[
            StationStop(
                station_code="NDLS", sched_arrival=None, sched_departure=t("08:00"), platform=1
            ),
            StationStop(
                station_code="CNB", sched_arrival=t("10:30"), sched_departure=t("10:40"), platform=1
            ),
            StationStop(
                station_code="MGS", sched_arrival=t("13:00"), sched_departure=None, platform=2
            ),
        ],
    )
    on_time = Train(
        number="12302",
        name="Howrah Rajdhani",
        priority=TrainPriority.PREMIUM,
        status=TrainStatus.RUNNING,
        delay_min=0,
        km_offset=60.0,
        speed_kmph=120.0,
        route=[
            StationStop(
                station_code="NDLS", sched_arrival=None, sched_departure=t("08:30"), platform=2
            ),
            StationStop(
                station_code="CNB", sched_arrival=t("11:00"), sched_departure=t("11:10"), platform=3
            ),
            StationStop(
                station_code="MGS", sched_arrival=t("13:30"), sched_departure=None, platform=3
            ),
        ],
    )
    return [delayed, on_time]


def make_assignments() -> list[PlatformAssignment]:
    return [
        PlatformAssignment(
            station_code="CNB",
            platform=1,
            train_number="12952",
            arrival=t("10:55"),
            departure=t("11:05"),
        ),
        PlatformAssignment(
            station_code="CNB",
            platform=3,
            train_number="12302",
            arrival=t("11:00"),
            departure=t("11:10"),
        ),
    ]


class FakeSim:
    """Duck-typed SimEngine read-only seam used by the passenger router."""

    def __init__(self) -> None:
        self.trains = make_trains()
        self.assignments = make_assignments()

    def state(self) -> NetworkState:
        return NetworkState(
            sim_time=t("09:00"),
            sim_speed=1.0,
            running=True,
            trains=self.trains,
            stations=STATIONS,
            assignments=self.assignments,
            crews=[],
            kpis=KPISnapshot(),
        )

    def get_platform_board(self, station_code: str) -> list[PlatformAssignment]:
        return [a for a in self.assignments if a.station_code == station_code]

    def project_downstream_impact(self, train_number: str, delay_min: int) -> list[StationStop]:
        for train in self.trains:
            if train.number == train_number:
                return [s for s in train.route if s.sched_arrival is not None]
        return []

    @property
    def delay_causes(self) -> dict[str, str]:  # property, matching SimEngine
        return {"12952": "signal failure at CNB"}


class BrokenSim:
    """Sim whose every read fails — endpoints must still answer 200."""

    def state(self) -> NetworkState:
        raise RuntimeError("twin unavailable")

    def get_platform_board(self, station_code: str) -> list[PlatformAssignment]:
        raise RuntimeError("twin unavailable")

    def project_downstream_impact(self, train_number: str, delay_min: int) -> list[StationStop]:
        raise RuntimeError("twin unavailable")

    @property
    def delay_causes(self) -> dict[str, str]:  # property, matching SimEngine
        raise RuntimeError("twin unavailable")


def make_client(sim: object | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(sim if sim is not None else FakeSim(), EventBus()))
    return TestClient(app)
