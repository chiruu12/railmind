"""RailMind backend entrypoint (WS2).

create_app() wires, inside a lifespan handler:

- EventBus + SQLite AuditSink (always available),
- SimEngine (WS1), agent registry (WS3) and passenger router (WS5) — each
  guarded so the backend boots with a logged warning while those
  workstreams are still in flight.

Collaborators live on app.state (bus, sim, audit) — no module globals.
The sim boots PAUSED; POST /api/sim/start begins ticking.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes, ws
from app.bus.audit import AuditSink
from app.bus.bus import EventBus
from app.settings import settings

logger = logging.getLogger(__name__)


def create_app(db_path: str | None = None, wire_workstreams: bool = True) -> FastAPI:
    """Build the FastAPI app.

    `db_path` overrides settings.db_path (tests). `wire_workstreams=False`
    skips sim/agents/passenger wiring so WS2 tests stay hermetic and inject
    a fake sim onto app.state.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bus = EventBus()
        audit = AuditSink(bus, db_path or settings.db_path)
        app.state.bus = bus
        app.state.audit = audit
        app.state.sim = None

        if not wire_workstreams:
            try:
                yield
            finally:
                audit.close()
            return

        try:
            from app.sim.engine import SimEngine

            # Boots paused — POST /api/sim/start begins the tick loop.
            app.state.sim = SimEngine(bus, data_dir=settings.data_dir, speed=settings.sim_speed)
        except ImportError as exc:
            logger.warning("sim engine unavailable (WS1 not landed yet): %s", exc)
        except Exception:  # noqa: BLE001 — a broken sim must not block the API
            logger.exception("sim engine failed to initialize; continuing without it")

        if app.state.sim is None:
            logger.warning("agents not registered: sim engine unavailable")
        else:
            try:
                from app.agents.registry import register_agents

                register_agents(bus, app.state.sim)
            except ImportError as exc:
                logger.warning("agent registry unavailable (WS3 not landed yet): %s", exc)
            except Exception:  # noqa: BLE001
                logger.exception("agent registration failed; continuing without agents")

        try:
            from app.api.passenger import build_router

            app.include_router(build_router(app.state.sim, bus))
        except ImportError as exc:
            logger.warning("passenger router unavailable (WS5 not landed yet): %s", exc)

        try:
            yield
        finally:
            sim = app.state.sim
            if sim is not None:
                try:
                    await sim.pause()
                except Exception:  # noqa: BLE001
                    logger.exception("sim pause on shutdown failed")
            audit.close()

    app = FastAPI(title="RailMind — IR Agent OS", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://*.vercel.app",
        ],
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes.router)
    app.include_router(ws.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
