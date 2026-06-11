"""REST routes against a fake sim implementing the CONTRACTS.md SimEngine seam."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bus.bus import EventBus
from app.contracts.entities import (
    Crew,
    CrewStatus,
    KPISnapshot,
    NetworkState,
    PlatformAssignment,
    Station,
    StationStop,
    Train,
    TrainPriority,
    TrainStatus,
)
from app.contracts.events import DecisionProposed
from app.main import create_app
from tests.test_api_audit import make_decision

SIM_T0 = datetime(2026, 6, 13, 8, 0)


def make_state() -> NetworkState:
    return NetworkState(
        sim_time=SIM_T0,
        sim_speed=1.0,
        running=False,
        trains=[
            Train(
                number="12302",
                name="Howrah Rajdhani",
                priority=TrainPriority.PREMIUM,
                route=[
                    StationStop(
                        station_code="CNB",
                        sched_arrival=datetime(2026, 6, 13, 9, 10),
                        sched_departure=datetime(2026, 6, 13, 9, 15),
                        platform=1,
                    )
                ],
                status=TrainStatus.RUNNING,
                delay_min=0,
                km_offset=42.0,
                speed_kmph=110.0,
                crew_id="CR-101",
            )
        ],
        stations=[
            Station(
                code="CNB", name="Kanpur Central", lat=26.45, lon=80.35,
                platform_count=6, km_offset=440.0,
            )
        ],
        assignments=[
            PlatformAssignment(
                station_code="CNB",
                platform=1,
                train_number="12302",
                arrival=datetime(2026, 6, 13, 9, 10),
                departure=datetime(2026, 6, 13, 9, 15),
            )
        ],
        crews=[
            Crew(id="CR-201", name="S. Verma", home_station="CNB", status=CrewStatus.SPARE)
        ],
        kpis=KPISnapshot(),
    )


class FakeSim:
    """Implements the WS1 SimEngine interface from docs/CONTRACTS.md."""

    def __init__(self, bus: EventBus | None = None, data_dir: str = "../data",
                 speed: float = 1.0) -> None:
        self.bus = bus
        self.running = False
        self.speed = speed
        self._state = make_state()

    async def start(self) -> None:
        self.running = True

    async def pause(self) -> None:
        self.running = False

    async def reset(self) -> None:
        self.running = False
        self.speed = 1.0
        self._state = make_state()

    def set_speed(self, speed: float) -> None:
        self.speed = speed

    def state(self) -> NetworkState:
        return self._state.model_copy(update={"running": self.running, "sim_speed": self.speed})

    def get_platform_board(self, station_code: str) -> list[PlatformAssignment]:
        return [a for a in self._state.assignments if a.station_code == station_code]

    def find_feasible_platforms(self, station_code: str, train_number: str) -> list[int]:
        return [2, 4]

    def project_downstream_impact(self, train_number: str, delay_min: int) -> list[StationStop]:
        return [s for t in self._state.trains if t.number == train_number for s in t.route]

    def check_duty(self, crew_id: str, train_number: str) -> tuple[float, float, str]:
        return (8.5, 9.0, "")

    def find_spare_crews(self, station_code: str | None = None) -> list[Crew]:
        return [
            c for c in self._state.crews
            if c.status == CrewStatus.SPARE
            and (station_code is None or c.home_station == station_code)
        ]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    # wire_workstreams=False: hermetic — no real sim/agents reacting on the bus.
    app = create_app(db_path=str(tmp_path / "audit.sqlite"), wire_workstreams=False)
    with TestClient(app) as test_client:
        app.state.sim = FakeSim()
        yield test_client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_state_snapshot(client: TestClient) -> None:
    response = client.get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert body["trains"][0]["number"] == "12302"
    assert body["stations"][0]["code"] == "CNB"
    assert body["running"] is False  # boots paused


def test_state_503_without_sim(client: TestClient) -> None:
    client.app.state.sim = None  # type: ignore[attr-defined]
    assert client.get("/api/state").status_code == 503
    assert client.post("/api/sim/start").status_code == 503


def test_train_detail(client: TestClient) -> None:
    response = client.get("/api/trains/12302")
    assert response.status_code == 200
    assert response.json()["name"] == "Howrah Rajdhani"


def test_train_unknown_404(client: TestClient) -> None:
    assert client.get("/api/trains/99999").status_code == 404


def test_sim_controls(client: TestClient) -> None:
    sim: FakeSim = client.app.state.sim  # type: ignore[attr-defined]
    assert client.post("/api/sim/start").json() == {"status": "started"}
    assert sim.running is True
    assert client.post("/api/sim/pause").json() == {"status": "paused"}
    assert sim.running is False
    assert client.post("/api/sim/speed", json={"speed": 4.0}).status_code == 200
    assert sim.speed == 4.0
    assert client.post("/api/sim/speed", json={"speed": 0}).status_code == 422
    assert client.post("/api/sim/reset").json() == {"status": "reset"}
    assert sim.speed == 1.0


def test_scenario_injection_publishes_event(client: TestClient) -> None:
    response = client.post(
        "/api/scenarios",
        json={
            "scenario_type": "delay",
            "params": {"train_number": "12302", "delay_min": 25, "cause": "loco failure"},
        },
    )
    assert response.status_code == 202
    assert response.json()["scenario_type"] == "delay"
    envelopes = client.app.state.bus.replay()  # type: ignore[attr-defined]
    assert envelopes[-1].topic == "scenario.injected"
    assert envelopes[-1].payload["params"]["delay_min"] == 25
    # ... and lands in the audit event log via the sink listener.
    audit_events = client.app.state.audit.recent_events(limit=5)  # type: ignore[attr-defined]
    assert audit_events[0].topic == "scenario.injected"


def test_scenario_bad_type_422(client: TestClient) -> None:
    response = client.post(
        "/api/scenarios", json={"scenario_type": "alien_invasion", "params": {}}
    )
    assert response.status_code == 422


def test_decision_resolve_flow(client: TestClient) -> None:
    audit = client.app.state.audit  # type: ignore[attr-defined]
    audit.handle(EventBus.envelope(DecisionProposed(decision=make_decision("dec-0007"))))

    listed = client.get("/api/decisions").json()
    assert listed[0]["id"] == "dec-0007"
    assert listed[0]["status"] == "proposed"

    response = client.post(
        "/api/decisions/dec-0007/resolve", json={"status": "approved", "note": "go ahead"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert client.get("/api/decisions").json()[0]["status"] == "approved"

    # The resolution itself went over the bus (audit + WS consumers see it).
    resolved_envelope = client.app.state.bus.replay()[-1]  # type: ignore[attr-defined]
    assert resolved_envelope.topic == "decision.resolved"
    assert resolved_envelope.payload["resolved_by"] == "human"


def test_resolve_unknown_decision_404(client: TestClient) -> None:
    response = client.post("/api/decisions/dec-9999/resolve", json={"status": "approved"})
    assert response.status_code == 404


def test_resolve_bad_status_422(client: TestClient) -> None:
    audit = client.app.state.audit  # type: ignore[attr-defined]
    audit.handle(EventBus.envelope(DecisionProposed(decision=make_decision("dec-0008"))))
    response = client.post("/api/decisions/dec-0008/resolve", json={"status": "maybe"})
    assert response.status_code == 422
