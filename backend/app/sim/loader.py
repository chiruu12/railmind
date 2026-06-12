"""Seed data loading — data/*.{json,csv} -> contract entities (WS1).

Reads timetable.json, stations.csv and crews.json into the frozen Pydantic
contracts. The data dir defaults to "../data" (cwd = backend/); when that
does not resolve (tests run from elsewhere) we fall back to the repo-root
data/ directory next to backend/.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from app.contracts.entities import Crew, Station, StationStop, Train, TrainPriority

DATA_FILES = ("timetable.json", "stations.csv", "crews.json")


@dataclass(frozen=True)
class SeedData:
    stations: list[Station]
    trains: list[Train]
    crews: list[Crew]


def resolve_data_dir(data_dir: str | Path) -> Path:
    """Resolve the seed data directory, falling back to <repo>/data."""
    path = Path(data_dir)
    if path.is_dir():
        return path
    fallback = Path(__file__).resolve().parents[3] / "data"
    if fallback.is_dir():
        return fallback
    raise FileNotFoundError(f"seed data dir not found: {data_dir!r} (fallback: {fallback})")


def load_stations(data_dir: str | Path) -> list[Station]:
    path = resolve_data_dir(data_dir) / "stations.csv"
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        Station(
            code=row["code"],
            name=row["name"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            platform_count=int(row["platform_count"]),
            km_offset=float(row["km_offset"]),
        )
        for row in rows
    ]


def load_trains(data_dir: str | Path) -> list[Train]:
    path = resolve_data_dir(data_dir) / "timetable.json"
    raw = json.loads(path.read_text())
    trains: list[Train] = []
    for entry in raw["trains"]:
        trains.append(
            Train(
                number=entry["number"],
                name=entry["name"],
                priority=TrainPriority(entry["priority"]),
                crew_id=entry.get("crew_id"),
                route=[StationStop(**stop) for stop in entry["route"]],
            )
        )
    return trains


def load_crews(data_dir: str | Path) -> list[Crew]:
    path = resolve_data_dir(data_dir) / "crews.json"
    raw = json.loads(path.read_text())
    return [Crew(**entry) for entry in raw["crews"]]


def load_seed(data_dir: str | Path) -> SeedData:
    """Load + cross-validate all three seed files."""
    base = resolve_data_dir(data_dir)
    stations = load_stations(base)
    trains = load_trains(base)
    crews = load_crews(base)

    by_code = {s.code: s for s in stations}
    for train in trains:
        if not train.route:
            raise ValueError(f"train {train.number} has an empty route")
        for stop in train.route:
            if stop.station_code not in by_code:
                raise ValueError(
                    f"train {train.number} references unknown station {stop.station_code!r}"
                )
        # park the train at its origin until the engine computes live positions
        train.km_offset = by_code[train.route[0].station_code].km_offset

    train_numbers = {t.number for t in trains}
    for crew in crews:
        if crew.assigned_train is not None and crew.assigned_train not in train_numbers:
            raise ValueError(f"crew {crew.id} assigned to unknown train {crew.assigned_train!r}")
        if crew.home_station not in by_code:
            raise ValueError(f"crew {crew.id} based at unknown station {crew.home_station!r}")

    return SeedData(stations=stations, trains=trains, crews=crews)
