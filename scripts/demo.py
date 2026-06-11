#!/usr/bin/env python3
"""End-to-end demo cascade runner (Phase 2 verification + demo rehearsal).

Boots bus + real SimEngine + real agents in-process (no HTTP), fast-forwards
the sim, injects the scripted scenario (25-min delay on 12302 at sim 08:05)
and prints every bus event. Exits non-zero if the expected cascade events
don't all appear.

Run:  cd backend && AGENT_LLM=off uv run python ../scripts/demo.py [--speed 20]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.agents.registry import register_agents  # noqa: E402
from app.bus.bus import EventBus
from app.contracts.events import Event, ScenarioInjected
from app.sim.engine import SimEngine

EXPECTED = [
    "scenario.injected",
    "delay.detected",
    "platform.conflict",
    "decision.proposed",
    "decision.resolved",
    "platform.reassigned",
    "crew.duty_breach",
    "crew.swapped",
    "passenger.alert",
    "agent.thought",
    "kpi.updated",
]

NOISY = {"sim.tick", "train.position"}


async def main(speed: float, quiet: bool) -> int:
    bus = EventBus()
    seen: dict[str, int] = {}

    async def watcher(event: Event) -> None:
        seen[event.topic] = seen.get(event.topic, 0) + 1
        if event.topic in NOISY:
            return
        payload = event.model_dump(mode="json")
        if not quiet:
            print(f"[{event.topic}] {payload}")

    bus.subscribe("*", watcher)

    sim = SimEngine(bus, data_dir="../data", speed=speed)
    register_agents(bus, sim)
    await sim.start()

    injected = False
    for _ in range(240):  # up to 4 real minutes
        await asyncio.sleep(1)
        now = sim.state().sim_time
        if not injected and now.hour == 8 and now.minute >= 5:
            print(f"--- injecting demo scenario at sim {now:%H:%M} ---")
            await bus.publish(
                ScenarioInjected(
                    scenario_type="delay",
                    params={
                        "train_number": "12302",
                        "delay_min": 25,
                        "cause": "loco failure near GZB",
                    },
                )
            )
            injected = True
        if all(t in seen for t in EXPECTED):
            break
        if now.hour >= 12:
            break
    await sim.pause()

    print("\n--- event counts ---")
    for topic, count in sorted(seen.items()):
        print(f"{topic:24s} {count}")
    missing = [t for t in EXPECTED if t not in seen]
    if missing:
        print(f"\nFAIL — missing: {missing}")
        return 1
    print(f"\nOK — full cascade observed at sim {sim.state().sim_time:%H:%M}")
    print(f"KPIs: {sim.state().kpis.model_dump()}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=20.0, help="sim-minutes per real second")
    ap.add_argument("--quiet", action="store_true", help="suppress per-event output")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.speed, args.quiet)))
