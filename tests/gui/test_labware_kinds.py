"""What a deck tile says of its labware: its kind (the icon's color) and
one line of facts, the well volume always (REDESIGN §6.1: two Corning 384
plates differ only by it)."""

from types import SimpleNamespace

import pytest

from micropick.config.labware import resolve_definition
from micropick.gui.labware_kinds import deck_tiles, facts, kind


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


def _deck_session(held=None, modules=()):
    deck = SimpleNamespace(module_for=lambda slot: next(
        (m for m in modules if slot in m.slots), None))
    return SimpleNamespace(
        run_state=SimpleNamespace(labware=held or {}), robot=object(),
        profile=SimpleNamespace(deck=deck))


def test_the_deck_has_a_tile_per_slot_the_trash_named():
    tiles = {t.slot: t for t in deck_tiles(_deck_session())}
    assert sorted(tiles) == list(range(1, 13))
    assert tiles[12].kind == "trash"
    assert tiles[5].name == "" and tiles[5].valid


def test_a_tile_says_what_stands_in_the_slot():
    held = {"5": SimpleNamespace(load_name="corning_24_wellplate_3.4ml_flat")}
    platform = SimpleNamespace(slots=[9], name="Calibration module")
    tiles = {t.slot: t for t in deck_tiles(_deck_session(held, [platform]))}
    assert tiles[5].name == "Corning 24 Well Plate 3.4 mL Flat"
    assert tiles[5].kind == "plate"
    assert tiles[9].platform == "Calibration module"


def test_no_robot_holds_nothing():
    session = _deck_session({"5": SimpleNamespace(load_name="x")})
    session.robot = None
    assert all(not t.name for t in deck_tiles(session))
