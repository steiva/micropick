"""The definition resolver: custom labware/ first, then stock, offline."""
from __future__ import annotations

import importlib.util
import json

import pytest

from micropick.config import labware

_HAS_SHARED_DATA = importlib.util.find_spec("opentrons_shared_data") is not None
requires_shared_data = pytest.mark.skipif(
    not _HAS_SHARED_DATA, reason="opentrons-shared-data not installed (extra 'stock')")


def _write_def(directory, load_name, *, version=1, namespace="custom_beta",
               ordering=(("A1", "B1"), ("A2", "B2"))):
    wells = {w: {} for col in ordering for w in col}
    data = {
        "parameters": {"loadName": load_name},
        "namespace": namespace,
        "version": version,
        "metadata": {"displayName": load_name},
        "ordering": [list(col) for col in ordering],
        "wells": wells,
    }
    (directory / f"{load_name}.json").write_text(json.dumps(data))
    return data


def test_local_resolves_and_flattens_ordering(tmp_path):
    _write_def(tmp_path, "myplate", ordering=(("A1", "B1"), ("A2", "B2")))
    d = labware.resolve_definition("myplate", directory=tmp_path)
    assert d.source == "local"
    assert d.load_name == "myplate" and d.version == 1
    assert d.ordering == [["A1", "B1"], ["A2", "B2"]]
    assert d.wells == ["A1", "B1", "A2", "B2"]      # column-major flatten
    assert d.well_count == 4


def test_local_takes_precedence_over_stock(tmp_path):
    # a real stock load name, but shadowed by a custom file in labware/
    name = "nest_96_wellplate_100ul_pcr_full_skirt"
    _write_def(tmp_path, name)
    d = labware.resolve_definition(name, directory=tmp_path)
    assert d.source == "local"


@requires_shared_data
def test_stock_resolves_from_shared_data(tmp_path):
    # nothing local, so it must come from opentrons-shared-data
    d = labware.resolve_definition("nest_96_wellplate_100ul_pcr_full_skirt",
                                   directory=tmp_path)
    assert d.source == "shared"
    assert len(d.ordering) == 12 and d.well_count == 96


def test_unknown_raises_with_available_list(tmp_path):
    _write_def(tmp_path, "onlyplate")
    with pytest.raises(labware.LabwareError) as exc:
        labware.resolve_definition("nope_not_a_plate", directory=tmp_path)
    assert "onlyplate" in str(exc.value)


def test_local_version_mismatch_raises(tmp_path):
    _write_def(tmp_path, "myplate", version=1)
    with pytest.raises(labware.LabwareError):
        labware.resolve_definition("myplate", version=3, directory=tmp_path)


def test_missing_ordering_is_rejected(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps(
        {"parameters": {"loadName": "bad"}, "wells": {}}))
    with pytest.raises(labware.LabwareError):
        labware.local_definitions(tmp_path)


def test_hardware_reexports_still_work():
    from micropick.hardware import labware as hw
    assert hw.LabwareDefinition is labware.LabwareDefinition
    assert callable(hw.list_definitions) and callable(hw.load_definition)
