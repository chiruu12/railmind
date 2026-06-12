"""Station Agent — one instance per station; owns its platform board.

On delay.detected touching its station it re-projects the delayed train's
occupancy, detects 5-min-headway conflicts, publishes platform.conflict,
collects rule-feasible reassignment options (sim.find_feasible_platforms)
and proposes the move (LLM picks between feasible candidates; premium
trains keep their platform when possible). On orchestrator/human approval
it publishes platform.reassigned.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.agents.adapter import AgentRuntime
from app.agents.base import BaseAgent
from app.contracts.entities import DecisionStatus, StationStop, TrainPriority
from app.contracts.events import (
    DecisionResolved,
    DelayDetected,
    PlatformConflict,
    PlatformReassigned,
)

logger = logging.getLogger(__name__)

HEADWAY = timedelta(minutes=5)  # platform occupied arrival → departure + 5 min
ORIGIN_BOARDING = timedelta(minutes=15)  # origin platform occupied 15 min pre-departure

INSTRUCTIONS = (
    "You are the Station Agent for {station} on an Indian Railways corridor. You own "
    "the platform board. When two trains contend for a platform you pick which train "
    "moves where, choosing ONLY from the rule-validated feasible options given to you. "
    "Respect train priority: premium trains (Rajdhani/Vande Bharat) keep their platform "
    "whenever possible. Answer with the exact option text and a crisp one-sentence "
    "operational rationale."
)


class PlatformChoice(BaseModel):
    chosen: str
    rationale: str


def _occupancy(stop: StationStop) -> tuple[datetime, datetime] | None:
    """Buffered occupancy window for a stop, per CONTRACTS headway rules."""
    arr, dep = stop.sched_arrival, stop.sched_departure
    if arr is None and dep is None:
        return None
    if arr is None:  # origin: occupied 15 min before departure
        arr = dep - ORIGIN_BOARDING  # type: ignore[operator]
    if dep is None:  # terminus: treat departure as arrival
        dep = arr
    return arr, dep + HEADWAY


class StationAgent(BaseAgent):
    def __init__(self, bus, sim, ledger, cooldowns, station_code: str) -> None:
        super().__init__(bus, sim, ledger, cooldowns)
        self.code = station_code
        self.name = f"station-agent:{station_code}"
        self.runtime = AgentRuntime(
            name=self.name,
            instructions=INSTRUCTIONS.format(station=station_code),
            tools=[],
            model_tier="fast",
            output_schema=PlatformChoice,
        )
        # decision_id → (train_number, old_platform, new_platform)
        self._pending: dict[str, tuple[str, int, int]] = {}

    def on_sim_reset(self) -> None:
        self._pending.clear()

    def register(self) -> None:
        self.subscribe("delay.detected", self.on_delay)
        self.subscribe("decision.resolved", self.on_resolved)

    # ── delay → conflict → proposal ────────────────────────────────────────

    async def on_delay(self, event: DelayDetected) -> None:
        if self.code not in event.downstream_stops:
            return
        if not self.cooldowns.should_handle(
            self.name, event.train_number, self.code, f"delay:{event.delay_min}"
        ):
            return

        projected = self.sim.project_downstream_impact(event.train_number, event.delay_min)
        my_stop = next((s for s in projected if s.station_code == self.code), None)
        if my_stop is None:
            return
        window = _occupancy(my_stop)
        if window is None:
            return

        board = self.sim.get_platform_board(self.code)
        conflicts = []
        for asg in board:
            if asg.train_number == event.train_number or asg.platform != my_stop.platform:
                continue
            other = (asg.arrival, asg.departure + HEADWAY)
            if window[0] < other[1] and other[0] < window[1]:
                conflicts.append(asg)

        if not conflicts:
            await self.think(
                f"{event.train_number} now occupies platform {my_stop.platform} at "
                f"{self.code} around {window[0]:%H:%M} — board still clear, no action needed."
            )
            return

        other = conflicts[0]
        overlap_start = max(window[0], other.arrival)
        overlap_end = min(window[1], other.departure + HEADWAY)
        await self.think(
            f"Conflict on platform {my_stop.platform} at {self.code}: delayed "
            f"{event.train_number} now overlaps {other.train_number} "
            f"({overlap_start:%H:%M}–{overlap_end:%H:%M}, 5-min headway)."
        )
        await self.bus.publish(
            PlatformConflict(
                station_code=self.code,
                platform=my_stop.platform,
                train_numbers=[event.train_number, other.train_number],
                window_start=overlap_start,
                window_end=overlap_end,
            )
        )

        involved = [event.train_number, other.train_number]
        trigger = (
            f"platform.conflict at {self.code}: {' vs '.join(involved)} on "
            f"platform {my_stop.platform} ({event.train_number} {event.delay_min} min late)"
        )
        await self._resolve_conflict(trigger, involved, my_stop.platform, excluded=set())

    async def _resolve_conflict(
        self, trigger: str, involved: list[str], platform: int, excluded: set[str]
    ) -> None:
        """Collect feasible options, choose (LLM or rules), propose the move."""
        priorities = {t.number: t.priority for t in self.sim.state().trains if t.number in involved}
        feasible: dict[str, list[int]] = {
            t: sorted(self.sim.find_feasible_platforms(self.code, t)) for t in involved
        }
        options = [
            f"move {t} to platform {p}"
            for t in involved
            for p in feasible[t]
            if f"move {t} to platform {p}" not in excluded
        ]
        if not options:
            await self.think(
                f"No feasible reassignment left at {self.code} for {' / '.join(involved)} "
                f"(excluded: {sorted(excluded) or 'none'}). Holding for operator."
            )
            return
        await self.think(
            f"Feasible options at {self.code}: {'; '.join(options)}. "
            "Choosing with priority rules (premium keeps its platform)."
        )

        # Deterministic rule ranking: lowest-priority train moves (premium keeps
        # its platform when possible), to its lowest-numbered feasible platform.
        by_keep_pref = sorted(involved, key=lambda t: priorities.get(t, TrainPriority.LOCAL))
        mover = next(
            (
                t
                for t in reversed(by_keep_pref)
                if any(f"move {t} to platform {p}" in options for p in feasible[t])
            ),
            None,
        )
        if mover is None:
            logger.warning(
                "station %s: no movable train found for conflict on P%s", self.code, platform
            )
            return
        new_platform = next(
            p for p in feasible[mover] if f"move {mover} to platform {p}" in options
        )
        det_choice = f"move {mover} to platform {new_platform}"
        keeper = next((t for t in involved if t != mover), None)
        det_rationale = (
            f"Conflict on platform {platform} at {self.code} between {' and '.join(involved)}; "
            f"options: {'; '.join(options)}; chose '{det_choice}' because "
            f"{keeper or 'the other train'} has priority "
            f"{priorities.get(keeper, '?') if keeper else '?'} and keeps platform {platform}, "
            f"and platform {new_platform} is the lowest feasible alternative for {mover}."
        )

        choice = await self.runtime.run(
            f"{trigger}. Pick exactly one option from: {options}.",
            context={
                "station": self.code,
                "options": options,
                "train_priorities": {t: int(priorities.get(t, 3)) for t in involved},
                "fallback": PlatformChoice(chosen=det_choice, rationale=det_rationale),
            },
        )
        if not isinstance(choice, PlatformChoice) or choice.chosen not in options:
            choice = PlatformChoice(chosen=det_choice, rationale=det_rationale)

        chosen_mover, chosen_platform = self._parse_option(choice.chosen)
        old_platform = platform

        async def retry(excluded_now: set[str]) -> None:
            await self._resolve_conflict(trigger, involved, platform, excluded_now)

        await self.think(f"Proposing: {choice.chosen} — sending to orchestrator for approval.")
        await self.propose(
            trigger=trigger,
            options_considered=options,
            chosen=choice.chosen,
            rationale=choice.rationale,
            resource=f"platform:{self.code}:{chosen_platform}",
            retry=retry,
            excluded=excluded,
            on_created=lambda d: self._pending.__setitem__(
                d.id, (chosen_mover, old_platform, chosen_platform)
            ),
        )

    # ── approval → reassignment ───────────────────────────────────────────

    async def on_resolved(self, event: DecisionResolved) -> None:
        plan = self._pending.get(event.decision_id)
        if plan is None or event.status != DecisionStatus.APPROVED:
            return
        del self._pending[event.decision_id]
        train, old_platform, new_platform = plan
        record = self.ledger.get(event.decision_id)
        rationale = record.decision.rationale if record else "approved platform reassignment"
        await self.think(
            f"Approved — moving {train} from platform {old_platform} to {new_platform} "
            f"at {self.code}.",
            decision_id=event.decision_id,
        )
        await self.bus.publish(
            PlatformReassigned(
                station_code=self.code,
                train_number=train,
                old_platform=old_platform,
                new_platform=new_platform,
                rationale=rationale,
                decision_id=event.decision_id,
            )
        )

    @staticmethod
    def _parse_option(option: str) -> tuple[str, int]:
        # "move {train} to platform {n}"
        parts = option.split()
        return parts[1], int(parts[-1])
