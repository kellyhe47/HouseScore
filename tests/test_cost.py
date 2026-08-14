"""API cost ledger (T001).

Feeds the R14 cost-per-door line in `make eval`. The n == 0 case is the one that
matters operationally: an eval run that scored no doors must report 0.0, not blow
up the harness with ZeroDivisionError.
"""

import json

import pytest

from houseaccount.cost import CostLedger


@pytest.fixture
def ledger():
    return CostLedger()


# --- accumulation -----------------------------------------------------------


def test_empty_ledger_totals_zero(ledger):
    assert ledger.total_usd() == 0.0


def test_single_record(ledger):
    ledger.record("vision", units=3, usd=0.045)
    assert ledger.total_usd() == pytest.approx(0.045)


def test_repeated_records_for_one_source_accumulate(ledger):
    ledger.record("vision", units=1, usd=0.015)
    ledger.record("vision", units=2, usd=0.030)
    assert ledger.total_usd() == pytest.approx(0.045)


def test_totals_sum_across_sources(ledger):
    ledger.record("vision", units=10, usd=0.150)
    ledger.record("geocode", units=200, usd=1.000)
    ledger.record("acs", units=1, usd=0.000)
    assert ledger.total_usd() == pytest.approx(1.150)


def test_zero_cost_records_are_still_accepted(ledger):
    ledger.record("parcels", units=5671, usd=0.0)
    assert ledger.total_usd() == 0.0


# --- per_door ---------------------------------------------------------------


def test_per_door_divides_total_by_doors(ledger):
    ledger.record("vision", units=100, usd=2.50)
    assert ledger.per_door(500) == pytest.approx(0.005)


def test_per_door_of_zero_doors_is_zero_not_a_crash(ledger):
    ledger.record("vision", units=100, usd=2.50)
    assert ledger.per_door(0) == 0.0


def test_per_door_of_zero_doors_on_an_empty_ledger(ledger):
    assert ledger.per_door(0) == 0.0


def test_per_door_of_an_empty_ledger_is_zero(ledger):
    assert ledger.per_door(5671) == 0.0


# --- persistence ------------------------------------------------------------


def test_save_load_round_trip(tmp_path, ledger):
    ledger.record("vision", units=40, usd=0.600)
    ledger.record("geocode", units=5671, usd=0.284)
    path = tmp_path / "cost.json"
    ledger.save(path)

    restored = CostLedger.load(path)
    assert restored.total_usd() == pytest.approx(ledger.total_usd())
    assert restored.per_door(5671) == pytest.approx(ledger.per_door(5671))


def test_saved_file_is_json_naming_its_sources(tmp_path, ledger):
    ledger.record("vision", units=40, usd=0.600)
    ledger.record("geocode", units=5671, usd=0.284)
    path = tmp_path / "cost.json"
    ledger.save(path)

    text = path.read_text(encoding="utf-8")
    json.loads(text)  # must be valid JSON
    assert "vision" in text
    assert "geocode" in text


def test_save_load_round_trip_of_an_empty_ledger(tmp_path, ledger):
    path = tmp_path / "cost.json"
    ledger.save(path)
    assert CostLedger.load(path).total_usd() == 0.0


def test_a_restored_ledger_keeps_accumulating(tmp_path, ledger):
    ledger.record("vision", units=1, usd=0.100)
    path = tmp_path / "cost.json"
    ledger.save(path)

    restored = CostLedger.load(path)
    restored.record("geocode", units=1, usd=0.050)
    assert restored.total_usd() == pytest.approx(0.150)


def test_save_creates_missing_parent_directories(tmp_path, ledger):
    ledger.record("vision", units=1, usd=0.1)
    path = tmp_path / "out" / "run-2026-08-14" / "cost.json"
    ledger.save(path)
    assert path.is_file()
