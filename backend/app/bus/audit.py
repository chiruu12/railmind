"""SQLite audit sink (WS2).

Registers a synchronous envelope listener on the EventBus and persists:

- ``event_log`` — every published envelope (topic, ts, payload JSON), the
  raw traceability record.
- ``decisions`` — one row per AgentDecision, inserted/updated from
  ``decision.proposed`` payloads and resolved (status / resolved_by / note)
  from ``decision.resolved``.

Writes happen synchronously inside ``EventBus.publish`` (the listener is
sync); a single SQLite connection pool with ``check_same_thread=False``
plus a process-wide lock keeps them safe and quick at demo scale. The sink
never raises into the bus — failures are logged and dropped.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime

from sqlmodel import Field, Session, SQLModel, create_engine, desc, select

from app.bus.bus import EventBus
from app.contracts.entities import AgentDecision, DecisionStatus
from app.contracts.events import DecisionProposed, DecisionResolved, EventEnvelope

logger = logging.getLogger(__name__)


class EventLogRow(SQLModel, table=True):
    """Raw audit record of every bus envelope."""

    __tablename__ = "event_log"

    id: int | None = Field(default=None, primary_key=True)
    topic: str = Field(index=True)
    ts: str = Field(description="Envelope wire timestamp (UTC ISO)")
    payload: str = Field(description="Event payload as JSON")


class DecisionRow(SQLModel, table=True):
    """Mirror of AgentDecision, kept current as decisions resolve."""

    __tablename__ = "decisions"

    id: str = Field(primary_key=True)
    ts: str = Field(default="", description="AgentDecision.ts (sim-time ISO)")
    agent: str = ""
    trigger: str = ""
    options_considered: str = Field(default="[]", description="JSON list")
    chosen: str = ""
    rationale: str = ""
    status: str = DecisionStatus.PROPOSED.value
    resolved_by: str | None = None
    resolve_note: str | None = None
    created_ts: str = Field(default="", index=True, description="Envelope ts (UTC ISO)")


class AuditSink:
    """SQLite-backed audit log fed by a bus envelope listener."""

    def __init__(self, bus: EventBus, db_path: str = "railmind.sqlite") -> None:
        self._engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self._engine)
        self._lock = threading.Lock()
        self._bus = bus
        bus.add_envelope_listener(self.handle)

    # ── write path (sync, called from EventBus.publish) ──────────────────────

    def handle(self, env: EventEnvelope) -> None:
        """Persist one envelope. Never raises (bus also guards, belt+braces)."""
        try:
            with self._lock, Session(self._engine) as session:
                session.add(
                    EventLogRow(
                        topic=env.topic,
                        ts=env.ts.isoformat(),
                        payload=json.dumps(env.payload, default=str),
                    )
                )
                if env.topic == DecisionProposed.topic:
                    self._upsert_decision(session, env)
                elif env.topic == DecisionResolved.topic:
                    self._resolve_decision(session, env)
                session.commit()
        except Exception:  # noqa: BLE001 — audit must never break the cascade
            logger.exception("audit sink failed to persist %s", env.topic)

    def _upsert_decision(self, session: Session, env: EventEnvelope) -> None:
        data = env.payload.get("decision") or {}
        decision_id = data.get("id")
        if not decision_id:
            logger.warning("decision.proposed without decision.id — skipped")
            return
        row = session.get(DecisionRow, decision_id)
        if row is None:
            row = DecisionRow(id=decision_id, created_ts=env.ts.isoformat())
            session.add(row)
        row.ts = str(data.get("ts", ""))
        row.agent = str(data.get("agent", ""))
        row.trigger = str(data.get("trigger", ""))
        row.options_considered = json.dumps(data.get("options_considered", []))
        row.chosen = str(data.get("chosen", ""))
        row.rationale = str(data.get("rationale", ""))
        row.status = str(data.get("status", DecisionStatus.PROPOSED.value))

    def _resolve_decision(self, session: Session, env: EventEnvelope) -> None:
        decision_id = env.payload.get("decision_id")
        row = session.get(DecisionRow, decision_id) if decision_id else None
        if row is None:
            logger.warning("decision.resolved for unknown decision %r", decision_id)
            return
        row.status = str(env.payload.get("status", row.status))
        row.resolved_by = env.payload.get("resolved_by")
        row.resolve_note = env.payload.get("note")

    # ── query helpers ─────────────────────────────────────────────────────────

    def recent_decisions(self, limit: int = 100) -> list[AgentDecision]:
        """Most recent decisions, newest first."""
        with self._lock, Session(self._engine) as session:
            rows = session.exec(
                select(DecisionRow)
                .order_by(desc(DecisionRow.created_ts), desc(DecisionRow.id))
                .limit(limit)
            ).all()
            return [self._to_decision(row) for row in rows]

    def get_decision(self, decision_id: str) -> AgentDecision | None:
        with self._lock, Session(self._engine) as session:
            row = session.get(DecisionRow, decision_id)
            return self._to_decision(row) if row else None

    def recent_events(self, limit: int = 100) -> list[EventEnvelope]:
        """Most recent raw envelopes, newest first."""
        with self._lock, Session(self._engine) as session:
            rows = session.exec(
                select(EventLogRow).order_by(desc(EventLogRow.id)).limit(limit)
            ).all()
            return [
                EventEnvelope(topic=r.topic, ts=r.ts, payload=json.loads(r.payload)) for r in rows
            ]

    @staticmethod
    def _to_decision(row: DecisionRow) -> AgentDecision:
        return AgentDecision(
            id=row.id,
            ts=datetime.fromisoformat(row.ts) if row.ts else datetime(2026, 6, 13),
            agent=row.agent,
            trigger=row.trigger,
            options_considered=json.loads(row.options_considered or "[]"),
            chosen=row.chosen,
            rationale=row.rationale,
            status=DecisionStatus(row.status),
        )

    def close(self) -> None:
        """Detach from the bus and release the SQLite engine."""
        self._bus.remove_envelope_listener(self.handle)
        self._engine.dispose()
