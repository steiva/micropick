"""Destination built from a labware definition; plan, ordering and tables."""
from __future__ import annotations

import pytest

from micropick.config.labware import LabwareDefinition
from micropick.core.routine import (Destination, Routine, RoutineError,
                                     empty_plate_table, plan_from_table)


def tiny_def(load_name="tiny", version=1, namespace="custom_beta"):
    """A 2-row (A, B) x 3-column plate, ordering column-major."""
    ordering = [["A1", "B1"], ["A2", "B2"], ["A3", "B3"]]
    data = {"parameters": {"loadName": load_name}, "ordering": ordering,
            "wells": {w: {} for col in ordering for w in col},
            "namespace": namespace, "version": version}
    return LabwareDefinition(load_name=load_name, namespace=namespace,
                             version=version, display_name=load_name,
                             ordering=ordering, data=data, source="local")


# ---------------------------------------------------------------------------
# destination from a definition
# ---------------------------------------------------------------------------

def test_from_definition_wells_and_order():
    d = Destination.from_definition(tiny_def(), slot=5)
    assert d.is_plate and d.slot == 5 and d.load_name == "tiny"
    assert d.targets == ["A1", "B1", "A2", "B2", "A3", "B3"]   # column-major
    assert d.contains("B2") and not d.contains("Z9")


def test_wells_by_row_and_column():
    d = Destination.from_definition(tiny_def(), slot=1)
    assert d.wells_by_column() == ["A1", "B1", "A2", "B2", "A3", "B3"]
    assert d.wells_by_row() == ["A1", "A2", "A3", "B1", "B2", "B3"]


def test_bad_slot_rejected():
    with pytest.raises(RoutineError):
        Destination.from_definition(tiny_def(), slot=12)


# ---------------------------------------------------------------------------
# ordering strategies driven by the definition
# ---------------------------------------------------------------------------

def _order(strategy):
    d = Destination.from_definition(tiny_def(), slot=1)
    r = Routine(d, {"A1": 1, "A3": 1, "B2": 1}, strategy=strategy)
    seq = []
    while (t := r.next()) is not None:
        seq.append(t)
        r.record(delivered=1)
    return seq


def test_strategy_orders():
    assert _order("in_order") == ["A1", "A3", "B2"]      # plan insertion order
    assert _order("by_column") == ["A1", "B2", "A3"]     # column-major filtered
    assert _order("by_row") == ["A1", "A3", "B2"]        # row-major filtered


def test_by_row_needs_a_plate():
    coords = Destination.coordinates([(1.0, 2.0)])
    with pytest.raises(RoutineError):
        Routine(coords, {(1.0, 2.0): 1}, strategy="by_row")


# ---------------------------------------------------------------------------
# plan validation against the definition
# ---------------------------------------------------------------------------

def test_plan_well_not_in_definition_raises():
    d = Destination.from_definition(tiny_def(), slot=1)
    with pytest.raises(RoutineError) as exc:
        Routine(d, {"Z9": 1})
    assert "not in the destination" in str(exc.value)


# ---------------------------------------------------------------------------
# planning tables shaped like the plate
# ---------------------------------------------------------------------------

def test_empty_and_plan_from_table():
    d = Destination.from_definition(tiny_def(), slot=1)
    table = empty_plate_table(d)
    assert list(table.index) == ["A", "B"]
    assert list(table.columns) == [1, 2, 3]
    table.at["B", 2] = 3
    assert plan_from_table(table, d) == {"B2": 3}


def test_plan_from_table_rejects_negative():
    d = Destination.from_definition(tiny_def(), slot=1)
    table = empty_plate_table(d)
    table.at["A", 1] = -1
    with pytest.raises(RoutineError):
        plan_from_table(table, d)


# ---------------------------------------------------------------------------
# resolving from a real local definition + disk round-trip that re-resolves
# ---------------------------------------------------------------------------

def test_from_labware_resolves_local_plate():
    d = Destination.from_labware("wide_bore_200ul", slot=5)
    assert d.load_name == "wide_bore_200ul" and len(d.targets) == 96


def test_disk_round_trip_reresolves_definition(tmp_path):
    d = Destination.from_labware("wide_bore_200ul", slot=5)
    path = tmp_path / "run.json"
    r = Routine(d, {"A1": 2, "C3": 1}, path=path)
    r.next()
    r.record(delivered=1, target="A1")

    back = Routine.load(path)
    assert back.destination.load_name == "wide_bore_200ul"
    assert back.destination.slot == 5
    assert back.plan == {"A1": 2, "C3": 1}
    assert back.remaining("A1") == 1
