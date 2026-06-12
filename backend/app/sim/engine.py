"""Digital twin simulation engine (WS1).

Owns the live network state: trains moving along the corridor per
schedule + delay, platform occupancy, crews and KPIs. All mutations
flow through the bus (`scenario.injected`, `platform.reassigned`,
`crew.swapped`) — agents never touch twin state directly.

Time model: naive IST sim-time, sim day 2026-06-13, start 08:00.
Each real second advances sim_time by `speed` sim-minutes. Sim time is
derived from a monotonic anchor, so tick jitter never accumulates drift.

Movement model: trains move linearly by km_offset between consecutive
stations; lat/lon are interpolated the same way. Effective stop times =
scheduled times + current delay; once a train actually passes a stop the
crossing time is recorded so later delay injections never rewrite the past.

Baseline mode (`baseline=True`): platform/crew decisions from agents are
ignored and a train arriving at an occupied platform waits (its delay
grows until the platform frees) — used for the KPI comparison run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from app.bus.bus import EventBus
from app.contracts.entities import (
    Crew,
    CrewStatus,
    KPISnapshot,
    NetworkState,
    PlatformAssignment,
    ScenarioType,
    Station,
    StationStop,
    Train,
    TrainStatus,
)
from app.contracts.events import (
    CrewSwapped,
    Event,
    KPIUpdated,
    PlatformReassigned,
    ScenarioInjected,
    SimTick,
    TrainPosition,
    TrainStatusChanged,
)
from app.sim.loader import load_seed

logger = logging.getLogger(__name__)

SIM_START: Final = datetime(2026, 6, 13, 8, 0)
TICK_REAL_SECONDS: Final = 1.0
HEADWAY: Final = timedelta(minutes=5)  # platform occupied until departure + 5 min
BOARDING: Final = timedelta(minutes=15)  # origin platform occupied 15 min before departure
TERMINUS_DWELL: Final = timedelta(minutes=20)  # terminus occupancy when no departure is seeded
DELAY_STATUS_THRESHOLD: Final = 5  # delay_min >= 5 -> status "delayed"


@dataclass(frozen=True)
class PlatformBlock:
    station_code: str
    platform: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class _Loc:
    """Computed live position/status of one train at one instant."""

    km_offset: float
    lat: float
    lon: float
    speed_kmph: float
    status: TrainStatus
    next_station: str | None
    eta_next: datetime | None


class SimEngine:
    """The digital twin. See docs/CONTRACTS.md "Cross-workstream interfaces"."""

    def __init__(
        self,
        bus: EventBus,
        data_dir: str = "../data",
        speed: float = 1.0,
        baseline: bool = False,
    ) -> None:
        self._bus = bus
        self._data_dir = data_dir
        self._speed = max(speed, 0.01)
        self._baseline = baseline
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._anchor_real = 0.0
        self._anchor_sim = SIM_START
        self._load_state()
        bus.subscribe(ScenarioInjected.topic, self._on_scenario)
        bus.subscribe(PlatformReassigned.topic, self._on_platform_reassigned)
        bus.subscribe(CrewSwapped.topic, self._on_crew_swapped)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Begin/resume the tick loop as an asyncio task."""
        if self._running:
            return
        self._running = True
        self._anchor_real = time.monotonic()
        self._anchor_sim = self._sim_time
        self._task = asyncio.create_task(self._loop(), name="sim-tick-loop")

    async def pause(self) -> None:
        self._running = False
        await self._cancel_task()
        await self._bus.publish(
            SimTick(sim_time=self._sim_time, sim_speed=self._speed, running=False)
        )

    async def reset(self) -> None:
        """Reload seed data, sim_time back to 08:00, KPIs zeroed."""
        self._running = False
        await self._cancel_task()
        self._load_state()
        await self._bus.publish(
            SimTick(sim_time=self._sim_time, sim_speed=self._speed, running=False)
        )
        await self._bus.publish(KPIUpdated(**self._kpis.model_dump()))

    def set_speed(self, speed: float) -> None:
        """Change sim speed (sim-minutes per real second); re-anchors the clock."""
        self._speed = max(speed, 0.01)
        self._anchor_real = time.monotonic()
        self._anchor_sim = self._sim_time

    def state(self) -> NetworkState:
        assignments: list[PlatformAssignment] = []
        for station in self._stations:
            assignments.extend(self.get_platform_board(station.code))
        return NetworkState(
            sim_time=self._sim_time,
            sim_speed=self._speed,
            running=self._running,
            trains=self._trains,
            stations=self._stations,
            assignments=assignments,
            crews=self._crews,
            kpis=self._kpis,
        )

    @property
    def delay_causes(self) -> dict[str, str]:
        """train_number -> recorded cause of its current delay (read-only copy)."""
        return dict(self._delay_causes)

    # ── deterministic read-only helpers (agent tools) ────────────────────────

    def get_platform_board(self, station_code: str) -> list[PlatformAssignment]:
        """All platform occupancies at a station (effective times, no headway)."""
        board: list[PlatformAssignment] = []
        for train in self._trains:
            for stop in train.route:
                if stop.station_code != station_code:
                    continue
                start, end = self._window(train, stop)
                board.append(
                    PlatformAssignment(
                        station_code=station_code,
                        platform=stop.platform,
                        train_number=train.number,
                        arrival=start,
                        departure=end,
                    )
                )
        board.sort(key=lambda a: (a.platform, a.arrival))
        return board

    def find_feasible_platforms(self, station_code: str, train_number: str) -> list[int]:
        """Platforms where the train's stop fits: 5-min headway rule, no blocks,
        15-min origin boarding window honored."""
        station = self._station(station_code)
        train = self._train(train_number)
        stop = self._stop_at(train, station_code)
        if station is None or train is None or stop is None:
            return []
        w_start, w_end = self._window(train, stop)
        return [
            platform
            for platform in range(1, station.platform_count + 1)
            if not self._conflicts_on(station_code, platform, w_start, w_end, train_number)
        ]

    def project_downstream_impact(self, train_number: str, delay_min: int) -> list[StationStop]:
        """Remaining stops with scheduled times shifted by delay_min."""
        train = self._train(train_number)
        if train is None:
            return []
        shift = timedelta(minutes=delay_min)
        projected: list[StationStop] = []
        for i, stop in enumerate(train.route):
            key = (train.number, i)
            passed = key in self._dep_t if stop.sched_departure else key in self._arr_t
            if passed:
                continue
            projected.append(
                StationStop(
                    station_code=stop.station_code,
                    sched_arrival=stop.sched_arrival + shift if stop.sched_arrival else None,
                    sched_departure=(
                        stop.sched_departure + shift if stop.sched_departure else None
                    ),
                    platform=stop.platform,
                )
            )
        return projected

    def check_duty(self, crew_id: str, train_number: str) -> tuple[float, float, str]:
        """-> (projected_hours, limit_hours, breach_station_code or "").

        Projected hours = duty_start -> effective end-of-run of the train.
        Breach station = first remaining stop reached after the duty limit.
        """
        crew = self._crews_by_id.get(crew_id)
        train = self._train(train_number)
        if crew is None or train is None:
            return (0.0, crew.max_duty_hours if crew else 0.0, "")
        duty_start = crew.duty_start or self._sim_time
        duty_end = duty_start + timedelta(hours=crew.max_duty_hours)
        delay = timedelta(minutes=train.delay_min)
        last = train.route[-1]
        end_of_run = (last.sched_arrival or last.sched_departure) + delay  # type: ignore[operator]
        projected_hours = round((end_of_run - duty_start).total_seconds() / 3600, 2)
        breach = ""
        for stop in train.route:
            t = stop.sched_arrival or stop.sched_departure
            if t is not None and t + delay > duty_end:
                breach = stop.station_code
                break
        return (projected_hours, crew.max_duty_hours, breach)

    def find_spare_crews(self, station_code: str | None = None) -> list[Crew]:
        return [
            crew
            for crew in self._crews
            if crew.status is CrewStatus.SPARE
            and (station_code is None or crew.home_station == station_code)
        ]

    # ── bus handlers (the only mutation paths) ───────────────────────────────

    async def _on_scenario(self, event: Event) -> None:
        if not isinstance(event, ScenarioInjected):
            logger.warning("sim: unexpected payload on scenario.injected: %r", event)
            return
        params = event.params
        try:
            if event.scenario_type is ScenarioType.DELAY:
                await self._apply_delay(
                    str(params["train_number"]),
                    int(params["delay_min"]),
                    str(params.get("cause", "unspecified")),
                )
            elif event.scenario_type is ScenarioType.PLATFORM_BLOCK:
                self._blocks.append(
                    PlatformBlock(
                        station_code=str(params["station_code"]),
                        platform=int(params["platform"]),
                        start=self._sim_time,
                        end=self._sim_time + timedelta(minutes=int(params["duration_min"])),
                    )
                )
            elif event.scenario_type is ScenarioType.CREW_SICK:
                crew = self._crews_by_id.get(str(params["crew_id"]))
                if crew is not None:
                    crew.status = CrewStatus.OFF_DUTY
        except (KeyError, TypeError, ValueError):
            logger.exception("sim: bad scenario params %r", params)

    async def _apply_delay(self, train_number: str, delay_min: int, cause: str) -> None:
        train = self._train(train_number)
        if train is None:
            logger.warning("sim: delay scenario for unknown train %s", train_number)
            return
        train.delay_min += delay_min
        self._delay_causes[train.number] = cause
        loc = self._locate(train, self._sim_time)
        train.status = loc.status
        train.km_offset = loc.km_offset
        train.speed_kmph = loc.speed_kmph
        self._emitted[train.number] = (loc.status, train.delay_min)
        await self._bus.publish(
            TrainStatusChanged(
                train_number=train.number,
                status=loc.status,
                delay_min=train.delay_min,
                next_station=loc.next_station,
                eta_next=loc.eta_next,
            )
        )
        await self._publish_kpis()

    async def _on_platform_reassigned(self, event: Event) -> None:
        if not isinstance(event, PlatformReassigned):
            return
        if self._baseline:
            return  # baseline run: agent decisions are not applied
        train = self._train(event.train_number)
        stop = self._stop_at(train, event.station_code) if train else None
        if train is None or stop is None:
            logger.warning(
                "sim: reassignment for unknown train/stop %s@%s",
                event.train_number,
                event.station_code,
            )
            return
        stop.platform = event.new_platform
        self._knock_on += 1
        self._decisions += 1
        await self._publish_kpis()

    async def _on_crew_swapped(self, event: Event) -> None:
        if not isinstance(event, CrewSwapped):
            return
        if self._baseline:
            return
        old = self._crews_by_id.get(event.old_crew_id)
        new = self._crews_by_id.get(event.new_crew_id)
        train = self._train(event.train_number)
        if new is None or train is None:
            logger.warning("sim: crew swap with unknown crew/train: %r", event)
            return
        if old is not None:
            old.assigned_train = None
            old.status = CrewStatus.OFF_DUTY
        new.assigned_train = train.number
        new.status = CrewStatus.ON_DUTY
        if new.duty_start is None:
            new.duty_start = self._sim_time
        train.crew_id = new.id
        self._decisions += 1
        await self._publish_kpis()

    # ── tick loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while self._running:
                elapsed = time.monotonic() - self._anchor_real
                target = self._anchor_sim + timedelta(minutes=elapsed * self._speed)
                await self._advance_to(target)
                await asyncio.sleep(TICK_REAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sim: tick loop crashed")

    async def _advance_to(self, now: datetime) -> None:
        """One tick: move the twin to sim time `now`, emit events."""
        self._sim_time = now
        events: list[Event] = [SimTick(sim_time=now, sim_speed=self._speed, running=self._running)]
        # Premium trains are admitted to contested platforms first.
        for train in sorted(self._trains, key=lambda t: (t.priority, t.number)):
            self._process_crossings(train, now)
            loc = self._locate(train, now)
            prev = self._emitted.get(train.number)
            train.status = loc.status
            train.km_offset = loc.km_offset
            train.speed_kmph = loc.speed_kmph
            if prev != (loc.status, train.delay_min):
                self._emitted[train.number] = (loc.status, train.delay_min)
                events.append(
                    TrainStatusChanged(
                        train_number=train.number,
                        status=loc.status,
                        delay_min=train.delay_min,
                        next_station=loc.next_station,
                        eta_next=loc.eta_next,
                    )
                )
            active = loc.status in (
                TrainStatus.RUNNING,
                TrainStatus.DELAYED,
                TrainStatus.AT_PLATFORM,
            )
            just_terminated = loc.status is TrainStatus.TERMINATED and (
                prev is None or prev[0] is not TrainStatus.TERMINATED
            )
            if active or just_terminated:
                events.append(
                    TrainPosition(
                        train_number=train.number,
                        lat=loc.lat,
                        lon=loc.lon,
                        km_offset=loc.km_offset,
                        speed_kmph=loc.speed_kmph,
                        status=loc.status,
                        delay_min=train.delay_min,
                    )
                )
        for event in events:
            await self._bus.publish(event)
        await self._publish_kpis()

    def _process_crossings(self, train: Train, now: datetime) -> None:
        """Record arrivals/departures crossed by `now`; in baseline mode hold
        trains whose platform is occupied (delay grows until it frees)."""
        for i, stop in enumerate(train.route):
            key = (train.number, i)
            delay = timedelta(minutes=train.delay_min)
            if stop.sched_arrival is None:
                # Origin: occupies its platform from 15 min before departure.
                if key not in self._boarding and stop.sched_departure is not None:
                    if now >= stop.sched_departure + delay - BOARDING:
                        self._boarding.add(key)
            else:
                eff_arr = stop.sched_arrival + delay
                if key not in self._arr_t and now >= eff_arr:
                    occupied = self._occupied_now(stop.station_code, stop.platform, train.number)
                    if self._baseline and occupied:
                        # Wait short of the platform; effective arrival keeps moving.
                        held = int((now - stop.sched_arrival).total_seconds() // 60) + 1
                        train.delay_min = max(train.delay_min, held)
                        self._held.add(key)
                        return  # cannot progress past a held stop
                    if self._baseline:
                        late = int((now - stop.sched_arrival).total_seconds() // 60)
                        train.delay_min = max(train.delay_min, late)
                    self._arr_t[key] = stop.sched_arrival + timedelta(minutes=train.delay_min)
                    self._arrivals += 1
                    if not occupied and key not in self._held:
                        self._instant += 1
            if stop.sched_departure is not None and key not in self._dep_t:
                eff_dep = stop.sched_departure + timedelta(minutes=train.delay_min)
                if now >= eff_dep and (stop.sched_arrival is None or key in self._arr_t):
                    self._dep_t[key] = eff_dep

    # ── geometry / state math ────────────────────────────────────────────────

    def _locate(self, train: Train, at: datetime) -> _Loc:
        route = train.route
        n = train.number
        delay = timedelta(minutes=train.delay_min)

        def arr(i: int) -> datetime | None:
            recorded = self._arr_t.get((n, i))
            if recorded is not None:
                return recorded
            sched = route[i].sched_arrival
            return sched + delay if sched else None

        def dep(i: int) -> datetime | None:
            recorded = self._dep_t.get((n, i))
            if recorded is not None:
                return recorded
            sched = route[i].sched_departure
            return sched + delay if sched else None

        last = len(route) - 1
        final_arr = arr(last) or dep(last)
        if final_arr is not None and at >= final_arr:
            return self._loc_at_station(route[last].station_code, TrainStatus.TERMINATED)

        for i in range(len(route)):
            a, d = arr(i), dep(i)
            if a is not None and at < a:
                if i == 0:
                    # Enters the corridor at its first stop (e.g. a MEMU starting mid-line).
                    return self._loc_at_station(
                        route[0].station_code, TrainStatus.SCHEDULED, route[0].station_code, a
                    )
                return self._loc_between(train, i - 1, i, dep(i - 1), a, at)
            if d is None or at < d:
                station = route[i].station_code
                if a is None:  # before origin departure
                    return self._loc_at_station(station, TrainStatus.SCHEDULED, station, d)
                nxt = route[i + 1].station_code if i < last else None
                eta = arr(i + 1) if i < last else None
                return self._loc_at_station(station, TrainStatus.AT_PLATFORM, nxt, eta)
        return self._loc_at_station(route[last].station_code, TrainStatus.TERMINATED)

    def _loc_at_station(
        self,
        station_code: str,
        status: TrainStatus,
        next_station: str | None = None,
        eta_next: datetime | None = None,
    ) -> _Loc:
        s = self._stations_by_code[station_code]
        return _Loc(s.km_offset, s.lat, s.lon, 0.0, status, next_station, eta_next)

    def _loc_between(
        self,
        train: Train,
        i_from: int,
        i_to: int,
        dep_time: datetime | None,
        arr_time: datetime,
        at: datetime,
    ) -> _Loc:
        s1 = self._stations_by_code[train.route[i_from].station_code]
        s2 = self._stations_by_code[train.route[i_to].station_code]
        span = (arr_time - dep_time).total_seconds() if dep_time else 0.0
        if span <= 0:
            frac, speed = 1.0, 0.0
        else:
            frac = min(max((at - dep_time).total_seconds() / span, 0.0), 1.0)  # type: ignore[operator]
            speed = abs(s2.km_offset - s1.km_offset) / (span / 3600)
        status = (
            TrainStatus.DELAYED
            if train.delay_min >= DELAY_STATUS_THRESHOLD
            else TrainStatus.RUNNING
        )
        return _Loc(
            km_offset=s1.km_offset + (s2.km_offset - s1.km_offset) * frac,
            lat=s1.lat + (s2.lat - s1.lat) * frac,
            lon=s1.lon + (s2.lon - s1.lon) * frac,
            speed_kmph=speed,
            status=status,
            next_station=s2.code,
            eta_next=arr_time,
        )

    # ── occupancy math ───────────────────────────────────────────────────────

    def _window(self, train: Train, stop: StationStop) -> tuple[datetime, datetime]:
        """Platform occupancy [start, end) — headway buffer NOT included."""
        delay = timedelta(minutes=train.delay_min)
        if stop.sched_arrival is None:
            dep = stop.sched_departure + delay  # type: ignore[operator]
            return dep - BOARDING, dep
        arr = stop.sched_arrival + delay
        if stop.sched_departure is not None:
            return arr, stop.sched_departure + delay
        return arr, arr + TERMINUS_DWELL

    def _conflicts_on(
        self,
        station_code: str,
        platform: int,
        w_start: datetime,
        w_end: datetime,
        exclude_train: str,
    ) -> list[str]:
        """Occupancies (incl. 5-min headway) overlapping [w_start, w_end)."""
        conflicts: list[str] = []
        for block in self._blocks:
            if (
                block.station_code == station_code
                and block.platform == platform
                and block.start < w_end + HEADWAY
                and w_start < block.end
            ):
                conflicts.append("blocked")
        for other in self._trains:
            if other.number == exclude_train:
                continue
            for stop in other.route:
                if stop.station_code != station_code or stop.platform != platform:
                    continue
                o_start, o_end = self._window(other, stop)
                if o_start < w_end + HEADWAY and w_start < o_end + HEADWAY:
                    conflicts.append(other.number)
        return conflicts

    def _occupied_now(self, station_code: str, platform: int, exclude_train: str) -> bool:
        """Is the platform physically occupied (or in headway/blocked) right now?"""
        at = self._sim_time
        for block in self._blocks:
            if (
                block.station_code == station_code
                and block.platform == platform
                and block.start <= at < block.end
            ):
                return True
        for other in self._trains:
            if other.number == exclude_train:
                continue
            for i, stop in enumerate(other.route):
                if stop.station_code != station_code or stop.platform != platform:
                    continue
                key = (other.number, i)
                present = key in self._arr_t or (
                    stop.sched_arrival is None and key in self._boarding
                )
                if not present:
                    continue
                o_start, o_end = self._window(other, stop)
                if o_start <= at < o_end + HEADWAY:
                    return True
        return False

    # ── KPIs ─────────────────────────────────────────────────────────────────

    async def _publish_kpis(self) -> None:
        snapshot = KPISnapshot(
            total_delay_min=sum(t.delay_min for t in self._trains),
            knock_on_delays_avoided=self._knock_on,
            pct_instant_platforming=(
                round(100.0 * self._instant / self._arrivals, 1) if self._arrivals else 100.0
            ),
            decisions_made=self._decisions,
        )
        if snapshot != self._kpis:
            self._kpis = snapshot
            await self._bus.publish(KPIUpdated(**snapshot.model_dump()))

    # ── internals ────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        seed = load_seed(self._data_dir)
        self._stations: list[Station] = seed.stations
        self._trains: list[Train] = seed.trains
        self._crews: list[Crew] = seed.crews
        self._stations_by_code = {s.code: s for s in self._stations}
        self._trains_by_number = {t.number: t for t in self._trains}
        self._crews_by_id = {c.id: c for c in self._crews}
        self._sim_time = SIM_START
        self._blocks: list[PlatformBlock] = []
        self._delay_causes: dict[str, str] = {}
        self._arr_t: dict[tuple[str, int], datetime] = {}
        self._dep_t: dict[tuple[str, int], datetime] = {}
        self._boarding: set[tuple[str, int]] = set()
        self._held: set[tuple[str, int]] = set()
        self._emitted: dict[str, tuple[TrainStatus, int]] = {}
        self._arrivals = 0
        self._instant = 0
        self._knock_on = 0
        self._decisions = 0
        self._kpis = KPISnapshot()

    async def _cancel_task(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _train(self, number: str) -> Train | None:
        return self._trains_by_number.get(number)

    def _station(self, code: str) -> Station | None:
        return self._stations_by_code.get(code)

    @staticmethod
    def _stop_at(train: Train | None, station_code: str) -> StationStop | None:
        if train is None:
            return None
        return next((s for s in train.route if s.station_code == station_code), None)
