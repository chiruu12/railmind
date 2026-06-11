"""WS5 — POST /api/chat template-mode tests (AGENT_LLM=off, no API keys)."""

from __future__ import annotations

import pytest

from app.api import passenger
from app.settings import settings
from tests.test_passenger_fixtures import BrokenSim, make_client


@pytest.fixture(autouse=True)
def llm_off(monkeypatch: pytest.MonkeyPatch):
    """Force template mode and zero keys regardless of the host environment."""
    monkeypatch.setattr(settings, "agent_llm", "off")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    passenger._sessions.clear()
    yield
    passenger._sessions.clear()


def _chat(client, message: str, session_id: str = "test") -> str:
    resp = client.post("/api/chat", json={"message": message, "session_id": session_id})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"reply"}
    return body["reply"]


def test_delayed_train_question_answers_from_twin_state():
    reply = _chat(make_client(), "Where is train 12952? I have a connection at Kanpur.")
    assert "12952" in reply
    assert "25 min late" in reply
    assert "CNB" in reply  # next stop
    assert "10:55" in reply  # 10:30 sched + 25 min delay
    assert "platform 1" in reply


def test_delayed_train_suggests_alternative():
    reply = _chat(make_client(), "Is 12952 running late?")
    # delay >= 15 → the other CNB-serving train is offered as an alternative
    assert "12302" in reply


def test_on_time_train():
    reply = _chat(make_client(), "What about train 12302?")
    assert "12302" in reply
    assert "on time" in reply


def test_train_name_lookup():
    reply = _chat(make_client(), "Where is the Tejas Rajdhani right now?")
    assert "12952" in reply
    assert "late" in reply


def test_unknown_train_handled_politely():
    reply = _chat(make_client(), "Where is train 99999?")
    assert "99999" in reply
    assert "12952" in reply  # offers the trains it can actually track
    assert "don't have" in reply


def test_station_board_question():
    reply = _chat(make_client(), "Which trains are at CNB?")
    assert "Kanpur" in reply
    assert "12952" in reply
    assert "12302" in reply


def test_unrelated_question_gets_capability_message():
    reply = _chat(make_client(), "What's the weather like today?")
    assert "train" in reply.lower()


def test_session_memory_capped_at_ten_turns():
    client = make_client()
    for i in range(15):
        _chat(client, f"Where is train 12952? (ask {i})", session_id="cap")
    history = passenger._sessions["cap"]
    assert len(history) == passenger.MAX_TURNS * 2  # 10 turns -> 20 messages
    # Oldest turns evicted, newest kept.
    assert "(ask 14)" in history[-2]["content"]
    assert all("(ask 0)" not in m["content"] for m in history)


def test_sessions_are_isolated():
    client = make_client()
    _chat(client, "Where is train 12952?", session_id="a")
    _chat(client, "Where is train 12302?", session_id="b")
    assert len(passenger._sessions["a"]) == 2
    assert len(passenger._sessions["b"]) == 2


def test_sim_failure_never_500s():
    reply = _chat(make_client(BrokenSim()), "Where is train 12952?")
    assert reply  # graceful capability message, not an error


def test_provider_chain_failure_falls_back_to_template(monkeypatch: pytest.MonkeyPatch):
    """AGENT_LLM=on with keys set but every provider raising → template reply."""
    monkeypatch.setattr(settings, "agent_llm", "on")
    monkeypatch.setattr(settings, "anthropic_api_key", "key")
    monkeypatch.setattr(settings, "openai_api_key", "key")
    monkeypatch.setattr(settings, "groq_api_key", "key")

    calls: list[str] = []

    def make_failing(name: str):
        async def fail(messages):
            calls.append(name)
            raise RuntimeError(f"{name} down")
        return fail

    monkeypatch.setattr(
        passenger,
        "_PROVIDERS",
        [
            ("anthropic", "anthropic_api_key", make_failing("anthropic")),
            ("openai", "openai_api_key", make_failing("openai")),
            ("groq", "groq_api_key", make_failing("groq")),
        ],
    )

    reply = _chat(make_client(), "Where is train 12952?")
    assert calls == ["anthropic", "openai", "groq"]  # full fallback chain walked
    assert "25 min late" in reply  # deterministic template answered anyway


def test_providers_skipped_without_keys(monkeypatch: pytest.MonkeyPatch):
    """AGENT_LLM=on but no keys → no provider is ever invoked."""
    monkeypatch.setattr(settings, "agent_llm", "on")

    async def boom(messages):  # pragma: no cover - must not run
        raise AssertionError("provider called without a key")

    monkeypatch.setattr(
        passenger,
        "_PROVIDERS",
        [("anthropic", "anthropic_api_key", boom)],
    )
    reply = _chat(make_client(), "Where is train 12952?")
    assert "25 min late" in reply
