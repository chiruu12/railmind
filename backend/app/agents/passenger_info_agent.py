"""Passenger Info Agent — turns operational events into passenger.alert broadcasts.

Bus-driven (the interactive chat/voice assistant is WS5's, separate). On
delay.detected / platform.reassigned / crew.swapped it composes a concise
passenger-facing message (quality LLM tier, deterministic template
fallback) and broadcasts it with a sensible severity + channel set. These
are AUTO decisions — no orchestrator approval.
"""

from __future__ import annotations

import logging

from app.agents.adapter import AgentRuntime
from app.agents.base import BaseAgent
from app.contracts.entities import AlertSeverity, DecisionStatus
from app.contracts.events import CrewSwapped, DelayDetected, PassengerAlert, PlatformReassigned

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "You are the Passenger Information Agent for Indian Railways. Convert internal "
    "operational events into a single short, calm, helpful passenger announcement "
    "(max 2 sentences). Include train number, what changed, and what passengers should "
    "do. Never mention internal jargon (agents, decisions, duty rules)."
)


class PassengerInfoAgent(BaseAgent):
    name = "passenger-info-agent"

    def __init__(self, bus, sim, ledger, cooldowns) -> None:
        super().__init__(bus, sim, ledger, cooldowns)
        self.runtime = AgentRuntime(
            name=self.name, instructions=INSTRUCTIONS, tools=[], model_tier="quality"
        )

    def register(self) -> None:
        self.subscribe("delay.detected", self.on_delay)
        self.subscribe("platform.reassigned", self.on_reassigned)
        self.subscribe("crew.swapped", self.on_swapped)

    async def on_delay(self, event: DelayDetected) -> None:
        if not self.cooldowns.should_handle(
            self.name, event.train_number, "", f"delay:{event.delay_min}"
        ):
            return
        severity = AlertSeverity.CRITICAL if event.delay_min >= 30 else AlertSeverity.WARNING
        template = (
            f"Train {event.train_number} is running approximately {event.delay_min} minutes "
            f"late ({event.cause}). We regret the inconvenience; please recheck arrival "
            "times before heading to the platform."
        )
        await self._broadcast(
            trigger=f"delay.detected: {event.train_number} +{event.delay_min} min ({event.cause})",
            train_number=event.train_number,
            severity=severity,
            channels=["app", "display", "announcement"],
            template=template,
            facts={
                "train": event.train_number,
                "delay_min": event.delay_min,
                "cause": event.cause,
            },
        )

    async def on_reassigned(self, event: PlatformReassigned) -> None:
        if not self.cooldowns.should_handle(
            self.name, event.train_number, event.station_code, f"platform:{event.new_platform}"
        ):
            return
        template = (
            f"Platform change at {event.station_code}: train {event.train_number} will now "
            f"arrive on platform {event.new_platform} instead of platform "
            f"{event.old_platform}. Please proceed to platform {event.new_platform}."
        )
        await self._broadcast(
            trigger=(
                f"platform.reassigned: {event.train_number} to platform "
                f"{event.new_platform} at {event.station_code}"
            ),
            train_number=event.train_number,
            severity=AlertSeverity.WARNING,
            channels=["app", "display", "announcement"],
            template=template,
            facts={
                "train": event.train_number,
                "station": event.station_code,
                "old_platform": event.old_platform,
                "new_platform": event.new_platform,
            },
        )

    async def on_swapped(self, event: CrewSwapped) -> None:
        if not self.cooldowns.should_handle(
            self.name, event.train_number, event.station_code, f"crew:{event.new_crew_id}"
        ):
            return
        template = (
            f"Train {event.train_number} will make a brief crew change at "
            f"{event.station_code}. No impact on your journey beyond the current delay."
        )
        await self._broadcast(
            trigger=(
                f"crew.swapped: {event.new_crew_id} relieves {event.old_crew_id} on "
                f"{event.train_number} at {event.station_code}"
            ),
            train_number=event.train_number,
            severity=AlertSeverity.INFO,
            channels=["app"],
            template=template,
            facts={"train": event.train_number, "station": event.station_code},
        )

    async def _broadcast(
        self,
        trigger: str,
        train_number: str,
        severity: AlertSeverity,
        channels: list[str],
        template: str,
        facts: dict,
    ) -> None:
        await self.think(f"Drafting passenger update for {train_number} ({trigger}).")
        message = await self.runtime.run(
            "Write the passenger announcement for this event.",
            context={**facts, "fallback": template},
        )
        message = str(message).strip() or template
        decision = await self.propose(
            trigger=trigger,
            options_considered=[f"broadcast via {'/'.join(channels)}"],
            chosen=message,
            rationale=f"Auto passenger broadcast ({severity.value}) for {trigger}.",
            resource=f"passenger:{train_number}",
            status=DecisionStatus.AUTO,
        )
        await self.think(
            f"Broadcasting ({severity.value} → {', '.join(channels)}): {message}",
            decision_id=decision.id,
        )
        await self.bus.publish(
            PassengerAlert(
                severity=severity, train_number=train_number, message=message, channels=channels
            )
        )
