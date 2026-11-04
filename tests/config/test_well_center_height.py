"""A plate's measurement belongs to the height of the module it was taken
on: `DeckConfig.well_center` is the one place that says so."""
from __future__ import annotations

import json

from micropick.config.schema import DeckConfig, DeckModule, WellCenter

PLATE = "corning_96"


def _deck(height=None, module=None):
    modules = ([] if module is None else
               [DeckModule(slots=[5], offset=[0, 0, module], name="Platform")])
    return DeckConfig(modules=modules, well_centers=[WellCenter(
        slot=5, load_name=PLATE, offset=[0.4, -0.3, -1.5], well="B2",
        module_height_mm=height)])


def test_the_height_it_was_measured_on_survives_a_round_trip():
    deck = _deck(height=64.2, module=64.2)
    saved = json.loads(deck.model_dump_json())
    again = DeckConfig.model_validate(saved)
    assert again.well_center(5, PLATE).module_height_mm == 64.2


def test_a_measurement_on_the_slots_module_height_is_the_plates():
    assert _deck(height=64.2, module=64.2).well_center(5, PLATE) is not None
    assert _deck(height=0.0).well_center("5", PLATE) is not None


def test_a_measurement_on_another_height_is_nobodys():
    assert _deck(height=64.2, module=50.0).well_center(5, PLATE) is None
    assert _deck(height=64.2).well_center(5, PLATE) is None     # module gone
    assert _deck(height=0.0, module=64.2).well_center(5, PLATE) is None


def test_a_record_from_before_has_no_height_and_is_accepted():
    old = {"modules": [{"slots": [5], "offset": [0, 0, 64.2]}],
           "well_centers": [{"slot": 5, "load_name": PLATE,
                             "offset": [0.4, -0.3, -1.5], "well": "B2"}]}
    center = DeckConfig.model_validate(old).well_center(5, PLATE)
    assert center is not None and center.module_height_mm is None
