"""WS /ws: replay history on connect, then live envelopes; multi-client fan-out."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    # wire_workstreams=False: hermetic — no real sim/agents reacting on the bus.
    app = create_app(db_path=str(tmp_path / "audit.sqlite"), wire_workstreams=False)
    with TestClient(app) as test_client:
        yield test_client


def _inject(client: TestClient, scenario_type: str, params: dict) -> None:
    response = client.post(
        "/api/scenarios", json={"scenario_type": scenario_type, "params": params}
    )
    assert response.status_code == 202


def test_ws_replay_then_live(client: TestClient) -> None:
    # Published BEFORE connecting -> must arrive via the replay buffer.
    _inject(client, "delay", {"train_number": "12302", "delay_min": 25})

    with client.websocket_connect("/ws") as ws:
        replayed = ws.receive_json()
        assert replayed["topic"] == "scenario.injected"
        assert replayed["payload"]["params"]["delay_min"] == 25
        assert "ts" in replayed

        # Published WHILE connected -> must stream live.
        _inject(client, "crew_sick", {"crew_id": "CR-201"})
        live = ws.receive_json()
        assert live["topic"] == "scenario.injected"
        assert live["payload"]["scenario_type"] == "crew_sick"


def test_ws_multiple_concurrent_clients(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        _inject(
            client,
            "platform_block",
            {"station_code": "CNB", "platform": 1, "duration_min": 30},
        )
        for ws in (first, second):
            message = ws.receive_json()
            assert message["topic"] == "scenario.injected"
            assert message["payload"]["params"]["station_code"] == "CNB"


def test_ws_listener_removed_after_disconnect(client: TestClient) -> None:
    bus = client.app.state.bus  # type: ignore[attr-defined]
    audit_listeners = len(bus._envelope_listeners)
    with client.websocket_connect("/ws") as ws:
        _inject(client, "delay", {"train_number": "12302", "delay_min": 5})
        ws.receive_json()
        assert len(bus._envelope_listeners) == audit_listeners + 1
    assert len(bus._envelope_listeners) == audit_listeners
