"""Passenger chat + voice endpoints (WS5).

`build_router(sim, bus)` returns an APIRouter exposing:

- POST /api/chat  {message, session_id} -> {reply}
- POST /api/voice multipart audio (or `text` fallback field) ->
  {reply_text, reply_audio_b64, reply_audio_mime}

Pipeline: gather live context from the sim twin -> Anthropic -> OpenAI -> Groq
-> deterministic template (when AGENT_LLM=off or every provider fails). The
endpoints never 500 because of provider issues — every external call is
guarded and falls through to the rule-based template.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from app.contracts.entities import NetworkState, Train, TrainStatus
from app.settings import settings

logger = logging.getLogger(__name__)

# In-module session memory: session_id -> [{"role": ..., "content": ...}], capped.
MAX_TURNS = 10  # user+assistant pairs kept per session
_sessions: dict[str, list[dict[str, str]]] = {}

SYSTEM_PROMPT = (
    "You are RailMind Yatri, a helpful Indian Railways passenger assistant. "
    "Answer concisely (1-3 short sentences), in plain language. Use ONLY facts "
    "from the live network state provided with the question — never invent "
    "schedules, platforms, delays or trains. All times are IST. If a train is "
    "delayed, state the delay and suggest a concrete alternative from the "
    "provided state when one exists. If the question is outside live train or "
    "station information, briefly say what you can help with."
)

FALLBACK_REPLY = (
    "I can help with live Indian Railways information on this corridor: ask me "
    "about a train (e.g. \"Where is train 12302?\") or a station board "
    "(e.g. \"Trains at CNB\")."
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


# ── session memory ───────────────────────────────────────────────────────────


def _remember(session_id: str, role: str, content: str) -> None:
    history = _sessions.setdefault(session_id, [])
    history.append({"role": role, "content": content})
    # Keep the last MAX_TURNS exchanges (2 messages per turn).
    if len(history) > MAX_TURNS * 2:
        del history[: len(history) - MAX_TURNS * 2]


# ── twin-state lookup helpers ────────────────────────────────────────────────


def _safe_state(sim: Any) -> NetworkState | None:
    try:
        return sim.state()
    except Exception:  # noqa: BLE001 — sim issues must not 500 the endpoint
        logger.exception("passenger: sim.state() failed")
        return None


def _find_train(message: str, state: NetworkState) -> Train | None:
    lowered = message.lower()
    for number in re.findall(r"\b(\d{4,5})\b", message):
        for train in state.trains:
            if train.number == number:
                return train
    for train in state.trains:
        if train.name.lower() in lowered:
            return train
    return None


def _find_station_code(message: str, state: NetworkState) -> str | None:
    lowered = message.lower()
    for station in state.stations:
        if re.search(rf"\b{re.escape(station.code.lower())}\b", lowered):
            return station.code
        if station.name.lower() in lowered:
            return station.code
    return None


def _station_name(state: NetworkState, code: str) -> str:
    for station in state.stations:
        if station.code == code:
            return station.name
    return code


def _fmt(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _next_stop(train: Train, sim_time: datetime) -> tuple[str, datetime] | None:
    """First downstream stop (station_code, projected ETA incl. delay)."""
    for stop in train.route:
        if stop.sched_arrival is None:
            continue
        eta = stop.sched_arrival + timedelta(minutes=train.delay_min)
        if eta >= sim_time:
            return stop.station_code, eta
    return None


def _platform_for(state: NetworkState, train_number: str, station_code: str) -> int | None:
    """Current platform — live assignments reflect reassignments; fall back to route."""
    for a in state.assignments:
        if a.train_number == train_number and a.station_code == station_code:
            return a.platform
    for train in state.trains:
        if train.number == train_number:
            for stop in train.route:
                if stop.station_code == station_code:
                    return stop.platform
    return None


def _alternatives(state: NetworkState, train: Train, station_code: str, eta: datetime) -> str:
    """Suggest another train serving the same next stop, arriving after the delayed one."""
    for other in state.trains:
        if other.number == train.number or other.status == TrainStatus.TERMINATED:
            continue
        for stop in other.route:
            if stop.station_code != station_code or stop.sched_arrival is None:
                continue
            other_eta = stop.sched_arrival + timedelta(minutes=other.delay_min)
            if other_eta >= eta:
                return (
                    f" Alternatively, train {other.number} {other.name} also serves "
                    f"{station_code} around {_fmt(other_eta)}."
                )
    return ""


# ── deterministic template answers (AGENT_LLM=off / provider failure) ────────


def _train_reply(state: NetworkState, train: Train) -> str:
    if train.status == TrainStatus.TERMINATED:
        return f"Train {train.number} {train.name} has completed its journey."
    if train.status == TrainStatus.SCHEDULED:
        origin = train.route[0]
        dep = origin.sched_departure
        when = f" at {_fmt(dep + timedelta(minutes=train.delay_min))}" if dep else ""
        return (
            f"Train {train.number} {train.name} has not departed yet; it is scheduled "
            f"to leave {_station_name(state, origin.station_code)}{when}."
        )

    if train.delay_min >= 5:
        status = f"is running {train.delay_min} min late"
    else:
        status = "is running on time"

    nxt = _next_stop(train, state.sim_time)
    if nxt is None:
        return f"Train {train.number} {train.name} {status} and is approaching its terminus."

    station_code, eta = nxt
    platform = _platform_for(state, train.number, station_code)
    plat = f" platform {platform}" if platform is not None else ""
    reply = (
        f"Train {train.number} {train.name} {status}; "
        f"next stop {station_code}{plat} at {_fmt(eta)}."
    )
    if train.delay_min >= 15:
        reply += _alternatives(state, train, station_code, eta)
    return reply


def _station_reply(state: NetworkState, sim: Any, code: str) -> str:
    boards = []
    try:
        boards = list(sim.get_platform_board(code))
    except Exception:  # noqa: BLE001
        boards = [a for a in state.assignments if a.station_code == code]
    upcoming = sorted(
        (a for a in boards if a.departure >= state.sim_time),
        key=lambda a: a.arrival,
    )[:4]
    if not upcoming:
        return f"No upcoming arrivals on the board at {_station_name(state, code)} right now."
    lines = ", ".join(
        f"train {a.train_number} on platform {a.platform} at {_fmt(a.arrival)}" for a in upcoming
    )
    return f"At {_station_name(state, code)} ({code}): {lines}."


def _template_reply(message: str, sim: Any) -> str:
    state = _safe_state(sim)
    if state is None:
        return FALLBACK_REPLY
    train = _find_train(message, state)
    if train is not None:
        return _train_reply(state, train)
    station = _find_station_code(message, state)
    if station is not None:
        return _station_reply(state, sim, station)
    return FALLBACK_REPLY


# ── LLM context + provider chain ─────────────────────────────────────────────


def _context_blob(message: str, sim: Any) -> str:
    """Compact live-twin context for the LLM, enriched when the question names things."""
    state = _safe_state(sim)
    if state is None:
        return "{}"
    ctx: dict[str, Any] = {
        "sim_time": state.sim_time.isoformat(),
        "stations": [s.model_dump(mode="json") for s in state.stations],
        "trains": [t.model_dump(mode="json") for t in state.trains],
        "platform_assignments": [a.model_dump(mode="json") for a in state.assignments],
    }
    train = _find_train(message, state)
    if train is not None and train.delay_min > 0:
        try:
            ctx["downstream_impact"] = [
                s.model_dump(mode="json")
                for s in sim.project_downstream_impact(train.number, train.delay_min)
            ]
        except Exception:  # noqa: BLE001
            pass
    station = _find_station_code(message, state)
    if station is not None:
        try:
            ctx["platform_board"] = [
                a.model_dump(mode="json") for a in sim.get_platform_board(station)
            ]
        except Exception:  # noqa: BLE001
            pass
    return json.dumps(ctx, default=str)


async def _ask_anthropic(messages: list[dict[str, str]]) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=messages,  # type: ignore[arg-type]
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


async def _ask_openai(messages: list[dict[str, str]]) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=512,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],  # type: ignore[list-item]
    )
    return (response.choices[0].message.content or "").strip()


async def _ask_groq(messages: list[dict[str, str]]) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        max_tokens=512,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],  # type: ignore[arg-type]
    )
    return (response.choices[0].message.content or "").strip()


_PROVIDERS = [
    ("anthropic", "anthropic_api_key", _ask_anthropic),
    ("openai", "openai_api_key", _ask_openai),
    ("groq", "groq_api_key", _ask_groq),
]


async def _chat_pipeline(message: str, session_id: str, sim: Any) -> str:
    """Answer a passenger question. Never raises."""
    reply = ""
    try:
        if settings.agent_llm.lower() != "off":
            history = list(_sessions.get(session_id, []))
            user_turn = {
                "role": "user",
                "content": (
                    f"Live network state (JSON):\n{_context_blob(message, sim)}\n\n"
                    f"Passenger question: {message}"
                ),
            }
            llm_messages = [*history, user_turn]
            for name, key_attr, ask in _PROVIDERS:
                if not getattr(settings, key_attr, ""):
                    continue
                try:
                    reply = await ask(llm_messages)
                    if reply:
                        break
                except Exception:  # noqa: BLE001 — fall through the chain
                    logger.exception("passenger chat: %s provider failed", name)
    except Exception:  # noqa: BLE001
        logger.exception("passenger chat: LLM pipeline failed")

    if not reply:
        try:
            reply = _template_reply(message, sim)
        except Exception:  # noqa: BLE001
            logger.exception("passenger chat: template fallback failed")
            reply = FALLBACK_REPLY

    _remember(session_id, "user", message)
    _remember(session_id, "assistant", reply)
    return reply


# ── Deepgram STT / TTS (degrade gracefully without a key) ────────────────────

VOICE_TTS_MODEL = "aura-asteria-en"
VOICE_AUDIO_MIME = "audio/mpeg"  # Aura TTS encoded as mp3


def _deepgram_stt(data: bytes) -> str:
    from deepgram import DeepgramClient, PrerecordedOptions

    client = DeepgramClient(settings.deepgram_api_key)
    options = PrerecordedOptions(model="nova-2", smart_format=True)
    response = client.listen.rest.v("1").transcribe_file({"buffer": data}, options)
    return response.results.channels[0].alternatives[0].transcript.strip()


def _deepgram_tts(text: str) -> bytes:
    from deepgram import DeepgramClient, SpeakOptions

    client = DeepgramClient(settings.deepgram_api_key)
    options = SpeakOptions(model=VOICE_TTS_MODEL, encoding="mp3")
    response = client.speak.rest.v("1").stream_memory({"text": text[:1000]}, options)
    buffer = getattr(response, "stream_memory", None) or getattr(response, "stream", None)
    if buffer is None:
        raise RuntimeError("deepgram TTS returned no audio stream")
    return buffer.getvalue()


# ── router ───────────────────────────────────────────────────────────────────


def build_router(sim: Any, bus: Any) -> APIRouter:
    """WS5 seam per CONTRACTS.md — POST /api/chat and POST /api/voice."""
    del bus  # injected per convention; chat/voice publish nothing yet
    router = APIRouter(prefix="/api", tags=["passenger"])

    @router.post("/chat")
    async def chat(body: ChatRequest) -> dict[str, str]:
        reply = await _chat_pipeline(body.message, body.session_id, sim)
        return {"reply": reply}

    @router.post("/voice")
    async def voice(
        audio: UploadFile | None = File(default=None),
        text: str | None = Form(default=None),
        session_id: str = Form(default="voice"),
    ) -> dict[str, Any]:
        transcript: str | None = None

        if audio is not None and settings.deepgram_api_key:
            try:
                data = await audio.read()
                if data:
                    transcript = await asyncio.to_thread(_deepgram_stt, data)
            except Exception:  # noqa: BLE001 — degrade to the text fallback
                logger.exception("passenger voice: Deepgram STT failed")

        if not transcript:
            transcript = (text or "").strip() or None

        if transcript is None:
            return {
                "reply_text": (
                    "I couldn't make out any audio. Please try again, or type "
                    "your question instead."
                ),
                "reply_audio_b64": None,
                "reply_audio_mime": None,
            }

        reply_text = await _chat_pipeline(transcript, session_id, sim)

        reply_audio_b64: str | None = None
        reply_audio_mime: str | None = None
        if settings.deepgram_api_key:
            try:
                audio_bytes = await asyncio.to_thread(_deepgram_tts, reply_text)
                reply_audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                reply_audio_mime = VOICE_AUDIO_MIME
            except Exception:  # noqa: BLE001 — reply text still goes out
                logger.exception("passenger voice: Deepgram TTS failed")

        return {
            "reply_text": reply_text,
            "reply_audio_b64": reply_audio_b64,
            "reply_audio_mime": reply_audio_mime,
        }

    return router
