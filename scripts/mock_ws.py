#!/usr/bin/env python3
"""Mock WebSocket server for WS4 frontend development (port 8001 ONLY).

Replays the canned §2 demo cascade as EventEnvelope JSON frames matching
docs/CONTRACTS.md topics exactly. On every client connect it:

  1. immediately emits one custom envelope {topic: "state.snapshot",
     payload: <NetworkState-shaped>} for initial store hydration
     (in production the store hydrates from GET /api/state instead),
  2. starts a continuous tick loop (sim.tick + train.position for all 8
     seed trains, lat/lon interpolated along NDLS→CNB→PRYJ→DDU),
  3. after a short ramp, fires the scripted cascade every ~1.5s:
     scenario.injected → train.status → agent.thought* → delay.detected
     → platform.conflict → decision.proposed → decision.resolved
     → platform.reassigned → crew.duty_breach → decision.proposed
     → decision.resolved → crew.swapped → passenger.alert → kpi.updated.

Run:  cd backend && uv run python ../scripts/mock_ws.py
Then: cd frontend && VITE_WS_URL=ws://localhost:8001 pnpm dev
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

HOST, PORT = "localhost", 8001
SIM_START = datetime.fromisoformat("2026-06-13T08:00:00")
SIM_SPEED = 2.0  # sim-minutes advanced per real second (keeps the map lively)
TICK_REAL_S = 1.0
CASCADE_DELAY_S = 6.0  # ramp time before the scripted cascade fires
CASCADE_GAP_S = 1.5


def load_data() -> tuple[dict, list[dict], list[dict]]:
    stations: dict[str, dict] = {}
    with open(DATA / "stations.csv", newline="") as f:
        for row in csv.DictReader(f):
            stations[row["code"]] = {
                "code": row["code"],
                "name": row["name"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "platform_count": int(row["platform_count"]),
                "km_offset": float(row["km_offset"]),
            }
    trains = json.loads((DATA / "timetable.json").read_text())["trains"]
    crews = json.loads((DATA / "crews.json").read_text())["crews"]
    return stations, trains, crews


STATIONS, TRAINS, CREWS = load_data()


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(topic: str, payload: dict) -> str:
    return json.dumps({"topic": topic, "ts": now_utc(), "payload": payload})


class ConnState:
    """Per-connection mutable demo state — refresh the page, replay the demo."""

    def __init__(self) -> None:
        self.sim_time = SIM_START
        self.delays: dict[str, int] = {t["number"]: 0 for t in TRAINS}
        self.statuses: dict[str, str] = {}
        # platform overrides applied by the cascade: (station, train) -> platform
        self.platform_overrides: dict[tuple[str, str], int] = {}
        self.crew_overrides: dict[str, str | None] = {}  # train -> crew id
        self.kpis = {
            "total_delay_min": 0,
            "knock_on_delays_avoided": 0,
            "pct_instant_platforming": 100.0,
            "decisions_made": 0,
        }

    # ── twin math ────────────────────────────────────────────────────────────

    def stop_times(self, stop: dict, delay_min: int) -> tuple[datetime, datetime]:
        """Effective (arrival, departure) for a stop. Origin: arrival = dep-15min
        (boarding occupancy); terminus: departure = arrival + 15min dwell."""
        d = timedelta(minutes=delay_min)
        arr = stop["sched_arrival"]
        dep = stop["sched_departure"]
        arr_dt = parse(arr) + d if arr else parse(dep) + d - timedelta(minutes=15)
        dep_dt = parse(dep) + d if dep else parse(arr) + d + timedelta(minutes=15)
        return arr_dt, dep_dt

    def position(self, train: dict) -> dict:
        """Interpolated lat/lon/km along the corridor at current sim time."""
        n = train["number"]
        delay = self.delays[n]
        route = train["route"]
        t = self.sim_time

        first_arr, first_dep = self.stop_times(route[0], delay)
        origin = STATIONS[route[0]["station_code"]]
        if t <= first_dep:
            status = "at_platform" if t >= first_arr else "scheduled"
            return self._pos(n, origin["lat"], origin["lon"], origin["km_offset"], 0.0, status, delay)

        for a, b in zip(route, route[1:]):
            _, dep_a = self.stop_times(a, delay)
            arr_b, dep_b = self.stop_times(b, delay)
            sa, sb = STATIONS[a["station_code"]], STATIONS[b["station_code"]]
            if t <= dep_a:
                status = "delayed" if delay > 0 else "at_platform"
                return self._pos(n, sa["lat"], sa["lon"], sa["km_offset"], 0.0, status, delay)
            if t < arr_b:
                f = (t - dep_a).total_seconds() / max((arr_b - dep_a).total_seconds(), 1.0)
                lat = sa["lat"] + (sb["lat"] - sa["lat"]) * f
                lon = sa["lon"] + (sb["lon"] - sa["lon"]) * f
                km = sa["km_offset"] + (sb["km_offset"] - sa["km_offset"]) * f
                hours = max((arr_b - dep_a).total_seconds() / 3600.0, 0.01)
                speed = abs(sb["km_offset"] - sa["km_offset"]) / hours
                status = "delayed" if delay > 0 else "running"
                return self._pos(n, lat, lon, km, round(speed, 1), status, delay)

        last = STATIONS[route[-1]["station_code"]]
        _, last_dep = self.stop_times(route[-1], delay)
        status = "terminated" if t > last_dep else ("delayed" if delay > 0 else "at_platform")
        return self._pos(n, last["lat"], last["lon"], last["km_offset"], 0.0, status, delay)

    def _pos(self, n: str, lat: float, lon: float, km: float, speed: float,
             status: str, delay: int) -> dict:
        self.statuses[n] = status
        return {
            "train_number": n,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "km_offset": round(km, 2),
            "speed_kmph": speed,
            "status": status,
            "delay_min": delay,
        }

    # ── snapshot (NetworkState-shaped) ───────────────────────────────────────

    def assignments(self) -> list[dict]:
        out = []
        for train in TRAINS:
            n = train["number"]
            delay = self.delays[n]
            for stop in train["route"]:
                arr, dep = self.stop_times(stop, delay)
                platform = self.platform_overrides.get(
                    (stop["station_code"], n), stop["platform"]
                )
                out.append({
                    "station_code": stop["station_code"],
                    "platform": platform,
                    "train_number": n,
                    "arrival": iso(arr),
                    "departure": iso(dep),
                })
        return out

    def snapshot(self) -> dict:
        trains = []
        for train in TRAINS:
            n = train["number"]
            pos = self.position(train)
            trains.append({
                "number": n,
                "name": train["name"],
                "priority": train["priority"],
                "route": train["route"],
                "status": pos["status"],
                "delay_min": self.delays[n],
                "km_offset": pos["km_offset"],
                "speed_kmph": pos["speed_kmph"],
                "crew_id": self.crew_overrides.get(n, train["crew_id"]),
            })
        crews = []
        for crew in CREWS:
            c = dict(crew)
            for train_no, crew_id in self.crew_overrides.items():
                if c["id"] == crew_id:
                    c["assigned_train"], c["status"] = train_no, "on_duty"
                elif c["assigned_train"] == train_no and c["id"] != crew_id:
                    c["assigned_train"], c["status"] = None, "off_duty"
            crews.append(c)
        return {
            "sim_time": iso(self.sim_time),
            "sim_speed": SIM_SPEED,
            "running": True,
            "trains": trains,
            "stations": list(STATIONS.values()),
            "assignments": self.assignments(),
            "crews": crews,
            "kpis": self.kpis,
        }


# ── the scripted cascade (SPEC §2) ───────────────────────────────────────────


def cascade_script(state: ConnState) -> list[tuple[float, str, dict]]:
    """(gap_seconds, topic, payload). Mutations to state are applied inline
    when each frame is sent (see effects in run_cascade)."""
    dec1 = {
        "id": "dec-0001",
        "ts": "2026-06-13T08:07:30",
        "agent": "station-agent:CNB",
        "trigger": "platform.conflict @ CNB P1 (12302 vs 12560)",
        "options_considered": [
            "Hold 12560 outside CNB home signal (~14 min knock-on delay)",
            "Move 12560 to platform 3 (free 09:30-10:05, zero delay)",
            "Move 12302 to platform 3 (breaks premium P1 convention)",
        ],
        "chosen": "Move 12560 to platform 3",
        "rationale": "P2 is blocked by terminating 12420 until 10:00 and P4 is held by "
        "64581. P3 is conflict-free for 12560's full occupancy window; priority-1 "
        "Rajdhani 12302 keeps P1 and no train takes knock-on delay.",
        "status": "proposed",
    }
    dec2 = {
        "id": "dec-0002",
        "ts": "2026-06-13T08:08:45",
        "agent": "crew-agent",
        "trigger": "crew.duty_breach CR-101 on 12302 (9.4h projected vs 9.0h limit)",
        "options_considered": [
            "Authorize duty extension for CR-101 (regulatory breach — disallowed)",
            "Swap to spare crew CR-201 at PRYJ during the scheduled halt",
            "Terminate 12302 at PRYJ (cancels DDU leg, ~400 pax stranded)",
        ],
        "chosen": "Swap to spare crew CR-201 at PRYJ",
        "rationale": "CR-201 is the only spare based on the remaining route and is rested. "
        "The swap fits inside 12302's PRYJ halt, adds zero delay, and keeps CR-101 "
        "within the 9-hour duty limit.",
        "status": "proposed",
    }
    return [
        (0.0, "scenario.injected", {
            "scenario_type": "delay",
            "params": {"train_number": "12302", "delay_min": 25,
                       "cause": "loco traction failure near GZB"},
        }),
        (1.5, "train.status", {
            "train_number": "12302", "status": "delayed", "delay_min": 25,
            "next_station": "CNB", "eta_next": "2026-06-13T09:35:00",
        }),
        (1.5, "agent.thought", {
            "agent": "train-agent:12302", "decision_id": None,
            "text": "Delta vs schedule jumped to +25 min (loco traction failure near GZB). "
            "This is above the 10-min escalation threshold — projecting downstream impact.",
        }),
        (1.5, "agent.thought", {
            "agent": "train-agent:12302", "decision_id": None,
            "text": "Projection: CNB arr 09:35 (+25), PRYJ arr 10:35 (+25), DDU arr 11:25 "
            "(+25). Raising delay.detected for downstream station and crew agents.",
        }),
        (1.5, "delay.detected", {
            "train_number": "12302", "delay_min": 25,
            "cause": "loco traction failure near GZB",
            "downstream_stops": ["CNB", "PRYJ", "DDU"],
        }),
        (1.5, "agent.thought", {
            "agent": "station-agent:CNB", "decision_id": None,
            "text": "12302 now hits CNB at 09:35 — exactly inside 12560's platform 1 window "
            "(09:35–09:45 incl. 5-min headway). That is a hard occupancy conflict.",
        }),
        (1.5, "platform.conflict", {
            "station_code": "CNB", "platform": 1,
            "train_numbers": ["12302", "12560"],
            "window_start": "2026-06-13T09:35:00",
            "window_end": "2026-06-13T09:45:00",
        }),
        (1.5, "agent.thought", {
            "agent": "station-agent:CNB", "decision_id": "dec-0001",
            "text": "Feasible escapes for 12560: P3 only. P2 is blocked by terminating "
            "12420 until 10:00, P4 is held by MEMU 64581 until 09:55. Drafting proposal.",
        }),
        (1.5, "decision.proposed", {"decision": dec1}),
        (1.5, "agent.thought", {
            "agent": "orchestrator", "decision_id": "dec-0001",
            "text": "Reviewing dec-0001: moving priority-2 12560 to P3 costs zero minutes "
            "and preserves P1 for the priority-1 Rajdhani. No downstream side effects "
            "found in the network snapshot. Approving.",
        }),
        (1.5, "decision.resolved", {
            "decision_id": "dec-0001", "status": "approved",
            "resolved_by": "orchestrator", "note": "zero-delay resolution",
        }),
        (1.5, "platform.reassigned", {
            "station_code": "CNB", "train_number": "12560",
            "old_platform": 1, "new_platform": 3,
            "rationale": "P1 occupied by delayed 12302; P3 is the only conflict-free "
            "platform for 12560's window.",
            "decision_id": "dec-0001",
        }),
        (1.5, "kpi.updated", {
            "total_delay_min": 25, "knock_on_delays_avoided": 1,
            "pct_instant_platforming": 87.5, "decisions_made": 1,
        }),
        (1.5, "agent.thought", {
            "agent": "crew-agent", "decision_id": None,
            "text": "Re-checking duty clocks after the delay: CR-101 on 12302 started "
            "02:00 with a 9h limit → must sign off by 11:00. New DDU arrival 11:25 "
            "projects 9.4h on duty. That is a breach.",
        }),
        (1.5, "crew.duty_breach", {
            "crew_id": "CR-101", "train_number": "12302",
            "projected_hours": 9.4, "limit_hours": 9.0, "breach_station": "DDU",
        }),
        (1.5, "agent.thought", {
            "agent": "crew-agent", "decision_id": "dec-0002",
            "text": "Spare roster scan: CR-201 (Anil Kumar) is rested and based at PRYJ — "
            "12302 halts there 10:35–10:40, enough for a footplate handover. Proposing swap.",
        }),
        (1.5, "decision.proposed", {"decision": dec2}),
        (1.5, "agent.thought", {
            "agent": "orchestrator", "decision_id": "dec-0002",
            "text": "dec-0002 keeps every duty clock legal, adds zero delay, and leaves "
            "CR-202 free as the remaining spare for the corridor. Approving.",
        }),
        (1.5, "decision.resolved", {
            "decision_id": "dec-0002", "status": "approved",
            "resolved_by": "orchestrator", "note": None,
        }),
        (1.5, "crew.swapped", {
            "old_crew_id": "CR-101", "new_crew_id": "CR-201",
            "train_number": "12302", "station_code": "PRYJ",
            "rationale": "CR-101 would exceed the 9h duty limit before DDU; CR-201 is the "
            "spare based at PRYJ and takes over during the scheduled halt.",
            "decision_id": "dec-0002",
        }),
        (1.5, "agent.thought", {
            "agent": "passenger-agent", "decision_id": None,
            "text": "Composing passenger comms: 12302 running +25 min, and 12560 now "
            "arrives at CNB platform 3 instead of 1. Pushing app + display alerts.",
        }),
        (1.5, "passenger.alert", {
            "severity": "warning", "train_number": "12302",
            "message": "Train 12302 Howrah Rajdhani is running approx. 25 minutes late due "
            "to a locomotive fault. Revised arrivals — Kanpur 09:35, Prayagraj 10:35, "
            "DDU 11:25. We apologise for the inconvenience.",
            "channels": ["app", "display", "announcement"],
        }),
        (1.5, "passenger.alert", {
            "severity": "info", "train_number": "12560",
            "message": "Platform change at Kanpur Central: train 12560 Shiv Ganga Express "
            "will now arrive on platform 3 (was platform 1). Arrival remains 09:35.",
            "channels": ["app", "display"],
        }),
        (1.5, "kpi.updated", {
            "total_delay_min": 25, "knock_on_delays_avoided": 2,
            "pct_instant_platforming": 87.5, "decisions_made": 2,
        }),
    ]


def apply_effect(state: ConnState, topic: str, payload: dict) -> None:
    """Keep the twin state coherent with the frames already sent."""
    if topic == "train.status":
        state.delays[payload["train_number"]] = payload["delay_min"]
    elif topic == "platform.reassigned":
        key = (payload["station_code"], payload["train_number"])
        state.platform_overrides[key] = payload["new_platform"]
    elif topic == "crew.swapped":
        state.crew_overrides[payload["train_number"]] = payload["new_crew_id"]
    elif topic == "kpi.updated":
        state.kpis = dict(payload)


# ── connection handling ──────────────────────────────────────────────────────


async def run_ticks(ws: ServerConnection, state: ConnState) -> None:
    while True:
        state.sim_time += timedelta(minutes=SIM_SPEED * TICK_REAL_S)
        frames = [envelope("sim.tick", {
            "sim_time": iso(state.sim_time),
            "sim_speed": SIM_SPEED,
            "running": True,
        })]
        frames += [envelope("train.position", state.position(t)) for t in TRAINS]
        for frame in frames:
            await ws.send(frame)
        await asyncio.sleep(TICK_REAL_S)


async def run_cascade(ws: ServerConnection, state: ConnState) -> None:
    await asyncio.sleep(CASCADE_DELAY_S)
    for gap, topic, payload in cascade_script(state):
        await asyncio.sleep(gap)
        apply_effect(state, topic, payload)
        await ws.send(envelope(topic, payload))
    print("cascade complete — ticks keep flowing")


async def drain_incoming(ws: ServerConnection) -> None:
    """Mock server ignores client frames (e.g. nothing is sent today, but be safe)."""
    async for _ in ws:
        pass


async def handler(ws: ServerConnection) -> None:
    print(f"client connected: {ws.remote_address}")
    state = ConnState()
    await ws.send(envelope("state.snapshot", state.snapshot()))
    tasks = [
        asyncio.create_task(run_ticks(ws, state)),
        asyncio.create_task(run_cascade(ws, state)),
        asyncio.create_task(drain_incoming(ws)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    finally:
        for task in tasks:
            task.cancel()
        print("client disconnected")


async def main() -> None:
    async with serve(handler, HOST, PORT):
        print(f"mock WS server on ws://{HOST}:{PORT} — demo cascade replays per connection")
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
