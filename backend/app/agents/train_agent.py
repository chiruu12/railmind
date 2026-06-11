"""Train Agent — watches train.status, raises delay.detected for the network.

One bus-driven instance covers all trains (cooldowns are keyed per train).
Triggers when a train crosses delay >= 5 min, or when an already-handled
delay grows by >= 5 min. Cause comes from the injected scenario when known,
otherwise "running late".
"""

from __future__ import annotations

import logging

from app.agents.adapter import AgentRuntime
from app.agents.base import BaseAgent
from app.contracts.entities import ScenarioType
from app.contracts.events import DelayDetected, ScenarioInjected, TrainStatusChanged

logger = logging.getLogger(__name__)

DELAY_THRESHOLD_MIN = 5

INSTRUCTIONS = (
    "You are a Train Agent in an Indian Railways control room, responsible for one "
    "running train. You watch its delay and brief the network in one short, factual "
    "sentence: how late, why (if known), and which downstream stations are affected. "
    "Terse operational radio style. No fluff."
)


class TrainAgent(BaseAgent):
    name = "train-agent"

    def __init__(self, bus, sim, ledger, cooldowns) -> None:
        super().__init__(bus, sim, ledger, cooldowns)
        self.runtime = AgentRuntime(
            name=self.name, instructions=INSTRUCTIONS, tools=[], model_tier="fast"
        )
        self._last_handled_delay: dict[str, int] = {}
        self._causes: dict[str, str] = {}

    def register(self) -> None:
        self.subscribe("train.status", self.on_train_status)
        self.subscribe("scenario.injected", self.on_scenario)

    async def on_scenario(self, event: ScenarioInjected) -> None:
        """Remember injected delay causes so delay.detected can name them."""
        if event.scenario_type == ScenarioType.DELAY:
            train = str(event.params.get("train_number", ""))
            cause = str(event.params.get("cause", "") or "")
            if train and cause:
                self._causes[train] = cause

    async def on_train_status(self, event: TrainStatusChanged) -> None:
        train, delay = event.train_number, event.delay_min
        if delay < DELAY_THRESHOLD_MIN:
            return
        last = self._last_handled_delay.get(train)
        if last is not None and delay - last < DELAY_THRESHOLD_MIN:
            return  # not a new delay, and not grown enough to re-handle
        if not self.cooldowns.should_handle(self.name, train, "", f"delay:{delay}"):
            return
        self._last_handled_delay[train] = delay

        cause = self._causes.get(train, "running late")
        stops = self.sim.project_downstream_impact(train, delay)
        downstream = [s.station_code for s in stops]

        await self.think(
            f"Train {train} is {delay} min late ({cause}). Assessing downstream impact."
        )

        fallback = (
            f"{train} running {delay} min late — {cause}. "
            f"Projected impact at {', '.join(downstream) if downstream else 'no remaining stops'}."
        )
        situation = await self.runtime.run(
            f"Train {train} is now {delay} minutes late (cause: {cause}). "
            f"Remaining stations: {', '.join(downstream) or 'none'}. "
            "Give a one-sentence situation read for the control room.",
            context={
                "train": train,
                "delay_min": delay,
                "downstream_stops": downstream,
                "fallback": fallback,
            },
        )
        await self.think(str(situation))
        await self.think(
            f"Raising delay alert for {train}: downstream stations {', '.join(downstream) or '—'} "
            "must re-check platforms and crew duty."
        )
        await self.bus.publish(
            DelayDetected(
                train_number=train, delay_min=delay, cause=cause, downstream_stops=downstream
            )
        )
