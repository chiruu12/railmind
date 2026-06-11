"""AuditSink: event log + decision mirror persistence (WS2)."""

from datetime import datetime
from pathlib import Path

from app.bus.audit import AuditSink
from app.bus.bus import EventBus
from app.contracts.entities import AgentDecision, DecisionStatus
from app.contracts.events import DecisionProposed, DecisionResolved, DelayDetected

SIM_TS = datetime(2026, 6, 13, 8, 30)


def make_decision(decision_id: str = "dec-0001") -> AgentDecision:
    return AgentDecision(
        id=decision_id,
        ts=SIM_TS,
        agent="station-agent:CNB",
        trigger="platform conflict at CNB between 12302 and 12952",
        options_considered=["move 12302 to PF4", "hold 12952 by 8 min"],
        chosen="move 12302 to PF4",
        rationale="PF4 is free with full headway; premium train not held.",
        status=DecisionStatus.PROPOSED,
    )


async def test_events_and_decisions_persist_across_restart(tmp_path: Path) -> None:
    db = str(tmp_path / "audit.sqlite")
    bus = EventBus()
    sink = AuditSink(bus, db)
    await bus.publish(
        DelayDetected(
            train_number="12302", delay_min=25, cause="loco failure", downstream_stops=["CNB"]
        )
    )
    await bus.publish(DecisionProposed(decision=make_decision()))
    sink.close()

    # "Restart": fresh bus + sink over the same DB file.
    reopened = AuditSink(EventBus(), db)
    events = reopened.recent_events()
    assert [e.topic for e in events] == ["decision.proposed", "delay.detected"]
    assert events[1].payload["train_number"] == "12302"

    decisions = reopened.recent_decisions()
    assert len(decisions) == 1
    persisted = decisions[0]
    assert persisted.id == "dec-0001"
    assert persisted.status == DecisionStatus.PROPOSED
    assert persisted.ts == SIM_TS
    assert persisted.options_considered == ["move 12302 to PF4", "hold 12952 by 8 min"]
    reopened.close()


async def test_decision_resolution_updates_status(tmp_path: Path) -> None:
    bus = EventBus()
    sink = AuditSink(bus, str(tmp_path / "audit.sqlite"))
    await bus.publish(DecisionProposed(decision=make_decision("dec-0001")))
    await bus.publish(DecisionProposed(decision=make_decision("dec-0002")))
    await bus.publish(
        DecisionResolved(
            decision_id="dec-0001",
            status=DecisionStatus.APPROVED,
            resolved_by="human",
            note="looks right",
        )
    )

    resolved = sink.get_decision("dec-0001")
    assert resolved is not None
    assert resolved.status == DecisionStatus.APPROVED

    # Newest first; resolution does not reorder.
    assert [d.id for d in sink.recent_decisions()] == ["dec-0002", "dec-0001"]
    assert sink.get_decision("dec-9999") is None
    sink.close()


async def test_resolve_for_unknown_decision_is_ignored(tmp_path: Path) -> None:
    bus = EventBus()
    sink = AuditSink(bus, str(tmp_path / "audit.sqlite"))
    await bus.publish(
        DecisionResolved(
            decision_id="dec-0404", status=DecisionStatus.REJECTED, resolved_by="human"
        )
    )
    assert sink.recent_decisions() == []
    # The raw envelope is still in the event log.
    assert [e.topic for e in sink.recent_events()] == ["decision.resolved"]
    sink.close()
