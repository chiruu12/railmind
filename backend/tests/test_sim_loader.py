"""Seed data loading (WS1)."""

from app.contracts.entities import CrewStatus, TrainPriority
from app.sim.loader import load_seed, resolve_data_dir

DATA_DIR = "../data"


def test_load_seed_counts() -> None:
    seed = load_seed(DATA_DIR)
    assert len(seed.stations) == 4
    assert len(seed.trains) == 8
    assert len(seed.crews) == 10


def test_stations_have_corridor_geometry() -> None:
    seed = load_seed(DATA_DIR)
    by_code = {s.code: s for s in seed.stations}
    assert by_code["NDLS"].km_offset == 0
    assert by_code["DDU"].km_offset > by_code["PRYJ"].km_offset > by_code["CNB"].km_offset


def test_trains_parked_at_first_station() -> None:
    seed = load_seed(DATA_DIR)
    by_code = {s.code: s for s in seed.stations}
    for train in seed.trains:
        assert train.km_offset == by_code[train.route[0].station_code].km_offset


def test_demo_actors_present() -> None:
    seed = load_seed(DATA_DIR)
    trains = {t.number: t for t in seed.trains}
    crews = {c.id: c for c in seed.crews}
    assert trains["12302"].priority is TrainPriority.PREMIUM
    assert trains["12302"].crew_id == "CR-101"
    assert crews["CR-201"].status is CrewStatus.SPARE
    assert crews["CR-201"].home_station == "PRYJ"


def test_resolve_data_dir_falls_back_to_repo_root() -> None:
    path = resolve_data_dir("nonexistent-dir")
    assert (path / "timetable.json").exists()
