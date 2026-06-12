"""Standalone sim runner — `uv run python -m app.sim.run` (cwd: backend/).

Boots a bus + engine at speed 5, prints every published envelope to stdout
and auto-injects the demo scenario (25-min delay on 12302, cause
"loco failure near GZB") when sim time reaches 08:05. Runs until every
train has terminated, or Ctrl-C.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from app.bus.bus import EventBus
from app.contracts.entities import ScenarioType, TrainStatus
from app.contracts.events import Event, EventEnvelope, ScenarioInjected, SimTick
from app.sim.engine import SimEngine

DEMO_SPEED = 5.0
DEMO_INJECT_AT = datetime(2026, 6, 13, 8, 5)
DEMO_SCENARIO = ScenarioInjected(
    scenario_type=ScenarioType.DELAY,
    params={"train_number": "12302", "delay_min": 25, "cause": "loco failure near GZB"},
)


def _print_envelope(env: EventEnvelope) -> None:
    print(f"{env.ts:%H:%M:%S} {env.topic:<20} {json.dumps(env.payload, default=str)}", flush=True)


async def main() -> None:
    bus = EventBus()
    bus.add_envelope_listener(_print_envelope)
    engine = SimEngine(bus, speed=DEMO_SPEED)

    injected = False

    async def auto_inject(event: Event) -> None:
        nonlocal injected
        if injected or not isinstance(event, SimTick):
            return
        if event.sim_time >= DEMO_INJECT_AT:
            injected = True
            print(f"--- injecting demo scenario at sim {event.sim_time:%H:%M} ---", flush=True)
            await bus.publish(DEMO_SCENARIO)

    bus.subscribe(SimTick.topic, auto_inject)

    await engine.start()
    print(f"--- sim started at speed {DEMO_SPEED} (1 real s = {DEMO_SPEED} sim min) ---")
    try:
        while not all(t.status is TrainStatus.TERMINATED for t in engine.state().trains):
            await asyncio.sleep(1)
    finally:
        await engine.pause()
        kpis = engine.state().kpis
        print(f"--- all trains terminated; final KPIs: {kpis.model_dump()} ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("--- interrupted ---")
