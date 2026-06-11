"""Control-plane REST routes (WS2) — see docs/CONTRACTS.md "REST surface".

All handlers resolve their collaborators (bus, sim, audit) from
``request.app.state`` at request time — no module globals — so the API
degrades gracefully (503) while the sim engine (WS1) has not landed yet.

/api/chat and /api/voice are NOT here: WS5's app/api/passenger.py provides
them via build_router(), included in main.py.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.contracts.entities import AgentDecision, DecisionStatus, NetworkState, ScenarioType, Train
from app.contracts.events import DecisionResolved, ScenarioInjected

router = APIRouter(prefix="/api", tags=["control"])


def _sim(request: Request) -> Any:
    sim = getattr(request.app.state, "sim", None)
    if sim is None:
        raise HTTPException(status_code=503, detail="simulation engine not available yet")
    return sim


# ── twin snapshots ────────────────────────────────────────────────────────────


@router.get("/state", response_model=NetworkState)
async def get_state(request: Request) -> NetworkState:
    """Full twin snapshot."""
    return _sim(request).state()


@router.get("/trains/{number}", response_model=Train)
async def get_train(number: str, request: Request) -> Train:
    state: NetworkState = _sim(request).state()
    for train in state.trains:
        if train.number == number:
            return train
    raise HTTPException(status_code=404, detail=f"unknown train {number}")


# ── audit log ─────────────────────────────────────────────────────────────────


@router.get("/decisions", response_model=list[AgentDecision])
async def list_decisions(request: Request, limit: int = 100) -> list[AgentDecision]:
    """Audit log of agent decisions, newest first."""
    return request.app.state.audit.recent_decisions(limit=limit)


class ResolveRequest(BaseModel):
    status: Literal["approved", "rejected"]
    note: str | None = None


@router.post("/decisions/{decision_id}/resolve", response_model=AgentDecision)
async def resolve_decision(
    decision_id: str, body: ResolveRequest, request: Request
) -> AgentDecision:
    """Human override: approve/reject a proposed decision (recorded in audit)."""
    audit = request.app.state.audit
    if audit.get_decision(decision_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown decision {decision_id}")
    await request.app.state.bus.publish(
        DecisionResolved(
            decision_id=decision_id,
            status=DecisionStatus(body.status),
            resolved_by="human",
            note=body.note,
        )
    )
    decision = audit.get_decision(decision_id)
    if decision is None:  # pragma: no cover — row cannot vanish mid-request
        raise HTTPException(status_code=500, detail="decision lost during resolve")
    return decision


# ── scenario injection ────────────────────────────────────────────────────────


class ScenarioRequest(BaseModel):
    scenario_type: ScenarioType
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/scenarios", status_code=202)
async def inject_scenario(body: ScenarioRequest, request: Request) -> dict[str, Any]:
    """Publish scenario.injected; the sim applies it on its next tick."""
    event = ScenarioInjected(scenario_type=body.scenario_type, params=body.params)
    await request.app.state.bus.publish(event)
    return {"status": "injected", "scenario_type": body.scenario_type.value, "params": body.params}


# ── sim control ───────────────────────────────────────────────────────────────


@router.post("/sim/start")
async def sim_start(request: Request) -> dict[str, Any]:
    await _sim(request).start()
    return {"status": "started"}


@router.post("/sim/pause")
async def sim_pause(request: Request) -> dict[str, Any]:
    await _sim(request).pause()
    return {"status": "paused"}


@router.post("/sim/reset")
async def sim_reset(request: Request) -> dict[str, Any]:
    await _sim(request).reset()
    return {"status": "reset"}


class SpeedRequest(BaseModel):
    speed: float = Field(gt=0, le=60, description="Sim-minutes per real second")


@router.post("/sim/speed")
async def sim_speed(body: SpeedRequest, request: Request) -> dict[str, Any]:
    _sim(request).set_speed(body.speed)
    return {"status": "ok", "speed": body.speed}
