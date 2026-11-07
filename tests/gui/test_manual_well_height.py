"""Dispense's well height on Manual pick's Run (#137): Top, Middle or Bottom and
a signed Offset, in words, starting at the deposit offset and kept by the page.

The mock's `move_to_well` stays where it is; here it goes where the robot's
would, from the numbers of the plate's drawing, so a test reads the Z reached.
The Corning 96 of `PLATE` stands in slot 5, its wells from 3.55 to 14.22."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import (LabwareDefinition,         # noqa: E402
                                      resolve_definition)
from micropick.config.schema import DeckModule                   # noqa: E402
from micropick.core.deck import slot_corner                      # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.manual import (LIFT, WELL_HEIGHT,       # noqa: E402
                                        ManualPage)
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
TOP, BOTTOM = 14.22, 3.55


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.profile.picking.well_offset_x = 0.0
    s.profile.picking.well_offset_y = 0.0
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


@pytest.fixture
def page(app, session):
    p = ManualPage(session)
    yield p
    p.deleteLater()


def _wait(app, page):
    end = time.monotonic() + 5
    while page.jog.job_pending and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _pose(session):
    pos = session.robot.get_position(verbose=False)[0]
    return pos["x"], pos["y"], pos["z"]


def _wells_reached(session, slot=5, module=0.0):
    """The mock's move_to_well going where the robot's goes."""
    robot, move = session.robot, session.robot.move_to_well
    definition = resolve_definition(PLATE)
    x0, y0 = slot_corner(slot)

    def goes(labware_id, well_name, well_location="top", offset=(0, 0, 0),
             **kwargs):
        move(labware_id, well_name, well_location, offset, **kwargs)
        well = definition.data["wells"][well_name]
        level = {"top": well["depth"], "center": well["depth"] / 2,
                 "bottom": 0.0}[well_location]
        robot.move_to_coordinates(
            (x0 + well["x"] + offset[0], y0 + well["y"] + offset[1],
             module + well["z"] + level + offset[2]), verbose=False)

    robot.move_to_well = goes


def _tip_on(app, page, session):
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")
    _wait(app, page)


def _ready(app, page, session, *, measured=False, module=0.0):
    """A plate in slot 5, A7 chosen, 10 µl in the tip, the tip staying where a
    Dispense leaves it. Measured: the rim 1 mm under the drawing's, 9 deep."""
    if module:
        session.set_deck_modules(
            [DeckModule(slots=[5], offset=[0, 0, module])])
    session.load_labware(resolve_definition(PLATE), 5)
    if measured:
        session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2",
                                depth_mm=9.0)
    _tip_on(app, page, session)
    _wells_reached(session, module=module)
    session.motion.retract()
    page._aspirate_in_place()
    _wait(app, page)
    page.dispense_retract.choice.buttons[LIFT.index("Stay")].click()
    page.work_cards.open(page.dispense_card)
    page.target.buttons[0].click()                       # In a well
    page.target_wells.map.well_clicked.emit("A7")
    _wait(app, page)


def _height(page, ref, offset):
    page.well_ref.buttons[WELL_HEIGHT.index(ref)].click()
    page.well_offset.setValue(offset)


def _dispensed(app, page):
    page.dispense_button.click()
    _wait(app, page)


def _moves(session):
    return len([c for c in session.robot.calls
                if c[0].startswith(("move", "retract"))])


def test_it_starts_at_the_bottom_plus_the_deposit_offset(app, session):
    session.profile.picking.deposit_offset_z = 1.5
    page = ManualPage(session)
    assert WELL_HEIGHT[page.well_ref.current()] == "Bottom"
    assert page.well_offset.value() == pytest.approx(1.5)
    page.deleteLater()


@pytest.mark.parametrize("ref, offset, z", [
    ("Bottom", 1.5, BOTTOM + 1.5),
    ("Middle", 0.0, (TOP + BOTTOM) / 2),
    ("Middle", 2.0, (TOP + BOTTOM) / 2 + 2.0),
    ("Top", -1.0, TOP - 1.0),
    ("Top", 3.0, TOP + 3.0),
])
def test_on_a_drawing_each_height_reaches_its_z(
        app, page, session, ref, offset, z):
    _ready(app, page, session)
    _height(page, ref, offset)
    _dispensed(app, page)
    assert _pose(session)[2] == pytest.approx(z, abs=0.01)
    assert session.in_tip == 0.0


@pytest.mark.parametrize("ref, offset, z", [
    ("Bottom", 1.5, 13.22 - 9.0 + 1.5),
    ("Middle", 0.0, 13.22 - 4.5),
    ("Top", 0.0, 13.22),
    ("Top", -1.0, 12.22),
    ("Top", 3.0, 16.22),
])
def test_on_a_measured_plate_each_height_goes_by_rim_and_depth(
        app, page, session, ref, offset, z):
    _ready(app, page, session, measured=True)
    _height(page, ref, offset)
    _dispensed(app, page)
    assert _pose(session)[2] == pytest.approx(z, abs=0.01)


def test_the_sentence_and_the_summary_follow_the_choice(app, page, session):
    _ready(app, page, session)
    _height(page, "Top", -1.0)
    assert page.height_sentence.text() == "1 mm below the top"
    assert "1 mm below the top" in page.dispense_card.summary.full
    _height(page, "Middle", 0.0)
    assert page.height_sentence.text() == "in the middle"
    _height(page, "Bottom", 0.5)
    assert page.height_sentence.text() == "0.5 mm above the bottom"


def test_the_sentence_says_a_measured_bottom(app, page, session):
    _ready(app, page, session, measured=True)
    _height(page, "Bottom", 0.5)
    assert page.height_sentence.text() == "0.5 mm above the measured bottom"
    assert "0.5 mm above the measured bottom" in page.dispense_card.summary.full
    _height(page, "Bottom", 0.0)
    assert page.height_sentence.text() == "at the measured bottom"


def test_the_choice_changes_no_picking_setting(app, page, session):
    _ready(app, page, session)
    before = session.profile.picking.model_dump()
    _height(page, "Top", -2.0)
    _dispensed(app, page)
    assert session.profile.picking.model_dump() == before
    assert WELL_HEIGHT[page.well_ref.current()] == "Top"
    assert page.well_offset.value() == pytest.approx(-2.0)


@pytest.mark.parametrize("measured, offset, words", [
    (True, -1.5, "measured bottom"), (False, -2.5, "drawing's bottom")])
def test_too_far_under_the_bottom_is_refused_nothing_moved(
        app, page, session, measured, offset, words):
    _ready(app, page, session, measured=measured)
    _height(page, "Bottom", offset)
    moved = _moves(session)
    _dispensed(app, page)
    assert _moves(session) == moved
    assert session.in_tip > 0
    toast = toast_of(page)
    assert toast.text().startswith("Not reachable: Z ")
    assert f"is below the {words} in slot 5" in toast.text()


@pytest.mark.parametrize("measured, offset, z", [
    (True, -1.0, 3.22), (False, -2.0, BOTTOM - 2.0)])
def test_a_little_under_the_bottom_is_reached(
        app, page, session, measured, offset, z):
    _ready(app, page, session, measured=measured)
    _height(page, "Bottom", offset)
    _dispensed(app, page)
    assert _pose(session)[2] == pytest.approx(z, abs=0.01)
    assert session.in_tip == 0.0


def test_above_the_travel_height_it_is_held_there(app, page, session):
    _ready(app, page, session, module=64.0)
    travel = session.motion.travel_z
    _height(page, "Top", 100.0)
    assert 64.0 + TOP + 100.0 > travel
    _dispensed(app, page)
    assert _pose(session)[2] == pytest.approx(travel, abs=0.01)
    assert session.in_tip == 0.0


def test_height_and_offset_show_only_with_in_a_well(app, page, session):
    _ready(app, page, session)
    assert page.well_height_row.isVisibleTo(page.dispense_card)
    page.target.buttons[1].click()                       # At a point
    assert not page.well_height_row.isVisibleTo(page.dispense_card)
