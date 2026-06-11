"""WebSocket fan-out (WS2): WS /ws streams every bus envelope as JSON.

On connect a client first receives the bus replay buffer (recent history,
oldest first) and then every new envelope live. Each connection gets its own
asyncio.Queue fed by a per-connection envelope listener, removed on
disconnect — any number of concurrent clients is fine.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.bus.bus import EventBus
from app.contracts.events import EventEnvelope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


async def _stream(
    websocket: WebSocket, history: list[EventEnvelope], queue: asyncio.Queue[EventEnvelope]
) -> None:
    for env in history:
        await websocket.send_text(env.model_dump_json())
    while True:
        env = await queue.get()
        await websocket.send_text(env.model_dump_json())


async def _watch_disconnect(websocket: WebSocket) -> None:
    """Consume client frames so we notice the disconnect while idle."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/ws")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()
    bus: EventBus = websocket.app.state.bus

    queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
    listener = queue.put_nowait
    # No await between snapshot and registration -> no envelope is missed
    # or duplicated between replay history and the live queue.
    history = bus.replay()
    bus.add_envelope_listener(listener)

    stream_task = asyncio.create_task(_stream(websocket, history, queue))
    watch_task = asyncio.create_task(_watch_disconnect(websocket))
    try:
        done, _ = await asyncio.wait(
            {stream_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning("ws client dropped: %s", exc)
    finally:
        # Cancel without awaiting: awaiting cancelled tasks here re-raises a
        # foreign CancelledError into the server's cancel scope on shutdown.
        bus.remove_envelope_listener(listener)
        stream_task.cancel()
        watch_task.cancel()
