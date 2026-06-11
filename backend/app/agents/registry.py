"""Agent registry — the WS3 seam (see docs/CONTRACTS.md).

`register_agents(bus, sim)` creates every agent and subscribes it to its
topics. Agents stay alive via their bus subscriptions (bound methods).
"""

from __future__ import annotations

import logging

from app.agents.base import CooldownRegistry, DecisionLedger, SimReader
from app.agents.crew_agent import CrewAgent
from app.agents.orchestrator import Orchestrator
from app.agents.passenger_info_agent import PassengerInfoAgent
from app.agents.station_agent import StationAgent
from app.agents.train_agent import TrainAgent
from app.bus.bus import EventBus

logger = logging.getLogger(__name__)


def register_agents(bus: EventBus, sim: SimReader) -> None:
    """Create + subscribe the full agent layer (train, stations, crew,
    passenger-info, orchestrator) against the given bus and sim."""
    ledger = DecisionLedger()
    cooldowns = CooldownRegistry(sim)

    agents = [
        TrainAgent(bus, sim, ledger, cooldowns),
        *(
            StationAgent(bus, sim, ledger, cooldowns, station_code=station.code)
            for station in sim.state().stations
        ),
        CrewAgent(bus, sim, ledger, cooldowns),
        PassengerInfoAgent(bus, sim, ledger, cooldowns),
        Orchestrator(bus, sim, ledger, cooldowns),
    ]
    for agent in agents:
        agent.register()
    logger.info("registered %d agents: %s", len(agents), [a.name for a in agents])
