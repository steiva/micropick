"""What a deck tile says of its labware: its kind (the icon's color) and
one line of facts, the well volume always (REDESIGN §6.1: two Corning 384
plates differ only by it)."""

import pytest

from micropick.config.labware import resolve_definition
from micropick.gui.labware_kinds import facts, kind


@pytest.mark.parametrize("load_name, expected", [
    ("corning_384_wellplate_112ul_flat", "384 wells · 112 µl · flat"),
    ("corning_24_wellplate_3.4ml_flat", "24 wells · 3.4 ml · flat"),
    ("nest_12_reservoir_15ml", "12 wells · 15 ml · V-bottom"),
    ("opentrons_96_tiprack_300ul", "96 tips · 300 µl"),
])
def test_facts(load_name, expected):
    assert facts(resolve_definition(load_name)) == expected


@pytest.mark.parametrize("load_name, expected", [
    ("corning_384_wellplate_112ul_flat", "plate"),
    ("nest_12_reservoir_15ml", "plate"),
    ("opentrons_96_tiprack_300ul", "tips"),
    ("opentrons_1_trash_1100ml_fixed", None),
])
def test_kind(load_name, expected):
    assert kind(resolve_definition(load_name)) == expected
