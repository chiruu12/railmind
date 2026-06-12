#!/usr/bin/env python3
"""Validate seed data + verify the engineered demo conflict (WS1).

Run from backend/ (deps live there): `make seed`
or: cd backend && uv run python ../scripts/seed.py

Checks that with the demo scenario (+25 min on 12302):
- CNB platform 1 conflicts for 12560, platform 3 is the feasible escape
- check_duty("CR-101", "12302") reports a duty breach at DDU
- CR-201 is spare at PRYJ (the natural swap)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.bus.bus import EventBus  # noqa: E402
from app.contracts.entities import CrewStatus, ScenarioType  # noqa: E402
from app.contracts.events import ScenarioInjected  # noqa: E402
from app.sim.engine import SimEngine  # noqa: E402
from app.sim.loader import load_seed  # noqa: E402

DATA_DIR = REPO_ROOT / "data"

_failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {message}")
    if not ok:
        _failures.append(message)


async def main() -> int:
    seed = load_seed(DATA_DIR)
    spares = [c for c in seed.crews if c.status is CrewStatus.SPARE]
    print("Seed data loaded:")
    print(f"  stations: {len(seed.stations)} ({', '.join(s.code for s in seed.stations)})")
    print(f"  trains:   {len(seed.trains)} ({', '.join(t.number for t in seed.trains)})")
    print(f"  crews:    {len(seed.crews)} ({len(spares)} spare: {', '.join(c.id for c in spares)})")

    bus = EventBus()
    engine = SimEngine(bus, data_dir=str(DATA_DIR))

    print("\nEngineered demo conflict (+25 min on 12302):")
    before = engine.find_feasible_platforms("CNB", "12560")
    check(1 in before, f"before delay: CNB platform 1 feasible for 12560 (feasible={before})")

    await bus.publish(
        ScenarioInjected(
            scenario_type=ScenarioType.DELAY,
            params={"train_number": "12302", "delay_min": 25, "cause": "loco failure near GZB"},
        )
    )

    after = engine.find_feasible_platforms("CNB", "12560")
    check(1 not in after, f"after delay: CNB platform 1 conflicts for 12560 (feasible={after})")
    check(3 in after, f"after delay: CNB platform 3 feasible for 12560 (feasible={after})")

    hours, limit, breach = engine.check_duty("CR-101", "12302")
    check(
        breach == "DDU" and hours > limit,
        f"CR-101 on 12302: projected {hours}h > limit {limit}h, breach at {breach or '—'}",
    )

    spare_pryj = engine.find_spare_crews("PRYJ")
    check(
        any(c.id == "CR-201" for c in spare_pryj),
        f"CR-201 spare at PRYJ ({[c.id for c in spare_pryj]})",
    )

    if _failures:
        print(f"\n{len(_failures)} check(s) FAILED")
        return 1
    print("\nAll seed checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
