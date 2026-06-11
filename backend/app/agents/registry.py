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
from app.contracts.events import SimTick

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

    # Sim-reset watcher: POST /api/sim/reset rewinds the sim clock to 08:00.
    # When sim_time jumps backwards, all per-run agent state (cooldowns,
    # pending decisions, last-handled delays) is stale — clear it so the
    # demo cascade can be re-run from a clean slate.
    last_seen: dict[str, object] = {"t": None}

    async def watch_reset(event: SimTick) -> None:
        prev = last_seen["t"]
        last_seen["t"] = event.sim_time
        if prev is not None and event.sim_time < prev:  # type: ignore[operator]
            logger.info("sim clock reversed (%s -> %s): resetting agent state", prev, event.sim_time)
            cooldowns.clear()
            ledger.clear()
            for agent in agents:
                agent.on_sim_reset()

    bus.subscribe(SimTick.topic, watch_reset)
    logger.info("registered %d agents: %s", len(agents), [a.name for a in agents])
