"""Orchestrator — single approval point for agent proposals.

On decision.proposed it sanity-checks the proposal against the live twin
(quality LLM tier, template fallback) and resolves it: approved unless it
contends for a resource (platform/crew) committed within the last 60
sim-seconds — then rejected with a note. On a human rejection
(decision.resolved, resolved_by="human") it re-triggers the originating
flow with the rejected option excluded, so the proposing agent comes back
with its next-best option.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.agents.adapter import AgentRuntime
from app.agents.base import BaseAgent
from app.contracts.entities import DecisionStatus
from app.contracts.events import DecisionProposed, DecisionResolved

logger = logging.getLogger(__name__)

RESOURCE_CONFLICT_SEC = 60.0  # same-resource decisions within this window conflict

INSTRUCTIONS = (
    "You are the Orchestrator of an Indian Railways multi-agent control room — the "
    "single approval point. Given an agent's proposed decision and the live network "
    "state, sanity-check it in one sentence: does the chosen option come from the "
    "stated feasible options, and is it consistent with the network state? Be terse "
    "and decisive."
)


class Orchestrator(BaseAgent):
    name = "orchestrator"

    def __init__(self, bus, sim, ledger, cooldowns) -> None:
        super().__init__(bus, sim, ledger, cooldowns)
        self.runtime = AgentRuntime(
            name=self.name, instructions=INSTRUCTIONS, tools=[], model_tier="quality"
        )
        self._resource_commits: dict[str, datetime] = {}  # resource → sim_time approved

    def register(self) -> None:
        self.subscribe("decision.proposed", self.on_proposed)
        self.subscribe("decision.resolved", self.on_resolved)

    # ── proposals ──────────────────────────────────────────────────────────

    async def on_proposed(self, event: DecisionProposed) -> None:
        decision = event.decision
        if decision.status != DecisionStatus.PROPOSED:
            return  # AUTO decisions (e.g. passenger alerts) need no approval

        state = self.sim.state()
        fallback = (
            f"Evaluating {decision.id} from {decision.agent}: '{decision.chosen}' is one of "
            f"{len(decision.options_considered)} rule-feasible options; consistent with the "
            f"network at {state.sim_time:%H:%M} ({len(state.trains)} trains tracked)."
        )
        evaluation = await self.runtime.run(
            f"Agent {decision.agent} proposes: {decision.chosen}. "
            f"Trigger: {decision.trigger}. Options considered: {decision.options_considered}. "
            f"Rationale: {decision.rationale}. Sanity-check this in one sentence.",
            context={
                "sim_time": state.sim_time,
                "trains": [
                    {"number": t.number, "status": t.status, "delay_min": t.delay_min}
                    for t in state.trains
                ],
                "fallback": fallback,
            },
        )
        await self.think(str(evaluation), decision_id=decision.id)

        record = self.ledger.get(decision.id)
        resource = record.resource if record else f"decision:{decision.id}"
        now = state.sim_time
        committed = self._resource_commits.get(resource)
        if committed is not None and (now - committed).total_seconds() < RESOURCE_CONFLICT_SEC:
            note = (
                f"Rejected: conflicts with a decision on {resource} resolved "
                f"{int((now - committed).total_seconds())} sim-seconds ago."
            )
            await self.think(
                f"{decision.id} contends for {resource} already committed moments ago — "
                "rejecting to prevent oscillation.",
                decision_id=decision.id,
            )
            await self.bus.publish(
                DecisionResolved(
                    decision_id=decision.id,
                    status=DecisionStatus.REJECTED,
                    resolved_by="orchestrator",
                    note=note,
                )
            )
            return

        self._resource_commits[resource] = now
        await self.think(
            f"Approving {decision.id}: feasible, no resource contention.",
            decision_id=decision.id,
        )
        await self.bus.publish(
            DecisionResolved(
                decision_id=decision.id,
                status=DecisionStatus.APPROVED,
                resolved_by="orchestrator",
            )
        )

    # ── human overrides ────────────────────────────────────────────────────

    async def on_resolved(self, event: DecisionResolved) -> None:
        if event.resolved_by != "human" or event.status != DecisionStatus.REJECTED:
            return
        record = self.ledger.get(event.decision_id)
        if record is None or record.retry is None:
            return
        record.excluded.add(record.decision.chosen)
        # Free the resource so the recomputed proposal isn't auto-rejected.
        self._resource_commits.pop(record.resource, None)
        await self.think(
            f"Operator rejected {event.decision_id} ('{record.decision.chosen}'"
            f"{f' — {event.note}' if event.note else ''}). Re-running {record.decision.agent}'s "
            "flow excluding that option.",
            decision_id=event.decision_id,
        )
        await record.retry(set(record.excluded))
