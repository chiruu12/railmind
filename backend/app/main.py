"""RailMind backend entrypoint.

Phase 0 skeleton: boots, serves /health and the contracts-derived OpenAPI.
WS2 owns this file from Phase 1 — wires sim, agents, REST routes, /ws.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bus.bus import EventBus

app = FastAPI(title="RailMind — IR Agent OS", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bus = EventBus()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
