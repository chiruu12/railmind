"""WS5 — POST /api/voice degradation tests (no DEEPGRAM_API_KEY, never 500)."""

from __future__ import annotations

import pytest

from app.api import passenger
from app.settings import settings
from tests.test_passenger_fixtures import make_client


@pytest.fixture(autouse=True)
def llm_off_no_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "agent_llm", "off")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    passenger._sessions.clear()
    yield
    passenger._sessions.clear()


VOICE_KEYS = {"reply_text", "reply_audio_b64", "reply_audio_mime"}


def test_text_fallback_without_deepgram_key():
    resp = make_client().post("/api/voice", data={"text": "Where is train 12952?"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == VOICE_KEYS
    assert "25 min late" in body["reply_text"]
    assert body["reply_audio_b64"] is None  # degraded: no TTS without a key
    assert body["reply_audio_mime"] is None


def test_audio_plus_text_fallback_without_key_uses_text():
    """No key → STT is skipped, the `text` field drives the chat pipeline."""
    resp = make_client().post(
        "/api/voice",
        data={"text": "Where is train 12302?", "session_id": "v1"},
        files={"audio": ("clip.webm", b"\x1aE\xdf\xa3fake-webm-bytes", "audio/webm")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "12302" in body["reply_text"]
    assert "on time" in body["reply_text"]
    assert body["reply_audio_b64"] is None


def test_audio_only_without_key_degrades_politely():
    resp = make_client().post(
        "/api/voice",
        files={"audio": ("clip.webm", b"fake-webm-bytes", "audio/webm")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply_text"]  # polite "couldn't make out audio" style message
    assert body["reply_audio_b64"] is None


def test_empty_request_never_500s():
    resp = make_client().post("/api/voice", data={})
    assert resp.status_code == 200
    assert resp.json()["reply_text"]


def test_stt_failure_falls_back_to_text(monkeypatch: pytest.MonkeyPatch):
    """Key present but Deepgram STT raising → text fallback still answers."""
    monkeypatch.setattr(settings, "deepgram_api_key", "key")

    def stt_boom(data: bytes) -> str:
        raise RuntimeError("deepgram unreachable")

    def tts_boom(text: str) -> bytes:
        raise RuntimeError("deepgram unreachable")

    monkeypatch.setattr(passenger, "_deepgram_stt", stt_boom)
    monkeypatch.setattr(passenger, "_deepgram_tts", tts_boom)

    resp = make_client().post(
        "/api/voice",
        data={"text": "Where is train 12952?"},
        files={"audio": ("clip.webm", b"fake", "audio/webm")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "25 min late" in body["reply_text"]
    assert body["reply_audio_b64"] is None  # TTS failed → text-only, no 500


def test_voice_shares_chat_session_memory():
    client = make_client()
    resp = client.post("/api/voice", data={"text": "Where is 12952?", "session_id": "shared"})
    assert resp.status_code == 200
    assert len(passenger._sessions["shared"]) == 2  # user + assistant turn stored
