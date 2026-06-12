"""Crew Agent — singleton guarding duty-hour rules (max 9h per CONTRACTS).

On delay.detected it re-runs the deterministic duty math for the train's
crew. On a projected breach it publishes crew.duty_breach, gathers spare
crews at downstream stations on the remaining route, has the LLM pick the
swap (station + crew) from feasible candidates, and proposes it. On
approval it publishes crew.swapped.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from app.agents.adapter import AgentRuntime
from app.agents.base import BaseAgent
from app.contracts.entities import Crew, DecisionStatus
from app.contracts.events import CrewDutyBreach, CrewSwapped, DecisionResolved, DelayDetected

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "You are the Crew Agent for an Indian Railways corridor. You enforce duty-hour "
    "rules (max 9h). When a crew is projected to breach, you pick a relief crew and "
    "swap station, choosing ONLY from the feasible candidates given (spare crews based "
    "at stations still ahead on the train's route). Prefer the earliest swap that "
    "comfortably clears the duty limit. Answer with the exact option text and a crisp "
    "one-sentence rationale."
)


class SwapChoice(BaseModel):
    chosen: str
    rationale: str


def _option(crew: Crew, station_code: str) -> str:
    return f"swap to {crew.id} at {station_code}"


class CrewAgent(BaseAgent):
    name = "crew-agent"

    def __init__(self, bus, sim, ledger, cooldowns) -> None:
        super().__init__(bus, sim, ledger, cooldowns)
        self.runtime = AgentRuntime(
            name=self.name,
            instructions=INSTRUCTIONS,
            tools=[],
            model_tier="fast",
            output_schema=SwapChoice,
        )
        # decision_id → (old_crew_id, new_crew_id, train_number, station_code)
        self._pending: dict[str, tuple[str, str, str, str]] = {}

    def on_sim_reset(self) -> None:
        self._pending.clear()

    def register(self) -> None:
        self.subscribe("delay.detected", self.on_delay)
        self.subscribe("decision.resolved", self.on_resolved)

    async def on_delay(self, event: DelayDetected) -> None:
        train = next((t for t in self.sim.state().trains if t.number == event.train_number), None)
        if train is None or not train.crew_id:
            return
        crew_id = train.crew_id
        if not self.cooldowns.should_handle(
            self.name, event.train_number, "", f"delay:{event.delay_min}"
        ):
            return

        projected, limit, breach_station = self.sim.check_duty(crew_id, event.train_number)
        if not breach_station:
            await self.think(
                f"Duty check for {crew_id} on {event.train_number}: projected "
                f"{projected:.1f}h within the {limit:.1f}h limit. No action."
            )
            return

        await self.think(
            f"Duty breach: {crew_id} on {event.train_number} projects {projected:.1f}h "
            f"against the {limit:.1f}h limit, breaching by {breach_station}. "
            "Scanning downstream stations for spare crews."
        )
        await self.bus.publish(
            CrewDutyBreach(
                crew_id=crew_id,
                train_number=event.train_number,
                projected_hours=round(projected, 2),
                limit_hours=limit,
                breach_station=breach_station,
            )
        )
        trigger = (
            f"crew.duty_breach: {crew_id} on {event.train_number} projects "
            f"{projected:.1f}h vs {limit:.1f}h limit (breach by {breach_station})"
        )
        await self._propose_swap(
            trigger, event.train_number, crew_id, event.downstream_stops, set()
        )

    async def _propose_swap(
        self,
        trigger: str,
        train_number: str,
        old_crew_id: str,
        downstream_stops: list[str],
        excluded: set[str],
    ) -> None:
        candidates: list[tuple[Crew, str]] = []  # in downstream (earliest-first) order
        for code in downstream_stops:
            for crew in self.sim.find_spare_crews(code):
                if crew.id != old_crew_id:
                    candidates.append((crew, code))
        options = [_option(c, code) for c, code in candidates if _option(c, code) not in excluded]
        if not options:
            await self.think(
                f"No spare crew available on {train_number}'s remaining route "
                f"(excluded: {sorted(excluded) or 'none'}). Escalating to operator."
            )
            return
        await self.think(f"Feasible relief options for {train_number}: {'; '.join(options)}.")

        # Deterministic rule ranking: earliest downstream station's spare crew.
        det_choice = options[0]
        det_rationale = (
            f"{trigger}; options: {'; '.join(options)}; chose '{det_choice}' because it is "
            f"the earliest downstream swap point, relieving {old_crew_id} before the limit."
        )
        choice = await self.runtime.run(
            f"{trigger}. Pick exactly one option from: {options}.",
            context={
                "train": train_number,
                "old_crew": old_crew_id,
                "remaining_route": downstream_stops,
                "options": options,
                "fallback": SwapChoice(chosen=det_choice, rationale=det_rationale),
            },
        )
        if not isinstance(choice, SwapChoice) or choice.chosen not in options:
            choice = SwapChoice(chosen=det_choice, rationale=det_rationale)

        new_crew_id, station_code = self._parse_option(choice.chosen)

        async def retry(excluded_now: set[str]) -> None:
            await self._propose_swap(
                trigger, train_number, old_crew_id, downstream_stops, excluded_now
            )

        await self.think(
            f"Proposing: {choice.chosen} for {train_number} — sending to orchestrator for approval."
        )
        await self.propose(
            trigger=trigger,
            options_considered=options,
            chosen=choice.chosen,
            rationale=choice.rationale,
            resource=f"crew:{new_crew_id}",
            retry=retry,
            excluded=excluded,
            on_created=lambda d: self._pending.__setitem__(
                d.id, (old_crew_id, new_crew_id, train_number, station_code)
            ),
        )

    async def on_resolved(self, event: DecisionResolved) -> None:
        plan = self._pending.get(event.decision_id)
        if plan is None or event.status != DecisionStatus.APPROVED:
            return
        del self._pending[event.decision_id]
        old_crew_id, new_crew_id, train_number, station_code = plan
        record = self.ledger.get(event.decision_id)
        rationale = record.decision.rationale if record else "approved crew swap"
        await self.think(
            f"Approved — {new_crew_id} relieves {old_crew_id} on {train_number} at {station_code}.",
            decision_id=event.decision_id,
        )
        await self.bus.publish(
            CrewSwapped(
                old_crew_id=old_crew_id,
                new_crew_id=new_crew_id,
                train_number=train_number,
                station_code=station_code,
                rationale=rationale,
                decision_id=event.decision_id,
            )
        )

    @staticmethod
    def _parse_option(option: str) -> tuple[str, str]:
        # "swap to {crew_id} at {station_code}"
        parts = option.split()
        return parts[2], parts[4]
