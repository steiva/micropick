"""Liquid in tip, the Picking page's card: a little liquid drawn into the
tip before a picking run, from the dish center or a well of a plate on the
deck, and given back there. Its volume and height are picking settings,
saved with Save; what is in the tip is the session's count. During a run
only what the page's `RunHold` allows."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

from micropick.config.labware import (LabwareDefinition,          # noqa: E402
                                      resolve_definition)
from micropick.config.schema import PipetteOffset                # noqa: E402
from micropick.config.schema import PixelMap as PixelMapConfig   # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.dish_bottom import dish_center          # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402
from micropick.gui.widgets.tip_liquid import (RunHold,            # noqa: E402
                                              TipLiquidBox)
from micropick.gui.readiness import DISH_POSITION                # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.hardware.protocols import xyz                     # noqa: E402

W, H, K = 2592, 1944, 0.026
PLATE = "corning_96_wellplate_360ul_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    _calibrated(s)
    s.set_dish_bottom(66.4)
    _tip_on(s)
    yield s
    s.shutdown()


@pytest.fixture
def page(app, session):
    host = QWidget()
    jog = JogPanel(session, machine_controls=False, parent=host)
    jog.hide()
    p = TipLiquidBox(session, jog, parent=host)
    host.show()
    assert _until(app, lambda: not p.jog.busy)
    yield p
    host.hide()
    host.deleteLater()


def _until(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _calibrated(session):
    """A linear toy map, a pipette offset and the position over the dish:
    what the dish center needs."""
    s = W / 2
    profile = session.profile
    profile.calibration.pixel_map = PixelMapConfig(
        degree=1, cu=W / 2, cv=H / 2, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0],
        ref=[W / 2, H / 2], bounds=[100.0, 100.0, W - 100.0, H - 100.0],
        image_size=[W, H], sweep_z=0.0)
    profile.calibration.pipette_offset = PipetteOffset(dx=10.0, dy=-5.0)
    profile.positions[DISH_POSITION] = (200.0, 150.0, 120.0)


def _tip_on(session):
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")


def _aspirated_at(session):
    """Where the tip was at each aspirate, from here on."""
    robot = session.robot
    poses = []
    real = robot.aspirate_in_place

    def aspirate_in_place(volume, flow_rate, verbose=False):
        poses.append(tuple(float(v) for v in xyz(robot)))
        return real(volume, flow_rate, verbose=verbose)

    robot.aspirate_in_place = aspirate_in_place
    return poses


def test_the_defaults_are_20_ul_at_1_5_mm_above_the_dish_bottom(page):
    assert page.settings_line.text() == (
        "20 µl · 1.5 mm above the dish bottom · 50 µl/s")
    assert page.aspirate_button.text() == "Aspirate 20 µl"
    assert page.holds.isHidden()


def test_dish_center_aspirates_over_the_center_above_the_bottom(
        app, session, page):
    poses = _aspirated_at(session)
    assert page.aspirate_button.isEnabled()
    page.aspirate_button.click()
    assert _until(app, lambda: poses and not page.jog.busy)
    (x, y), _why = dish_center(session.profile)
    assert poses[0] == pytest.approx((x, y, 66.4 + 1.5), abs=0.01)
    assert ("aspirate_in_place", 20.0, 50.0) in session.robot.calls
    assert session.in_tip == pytest.approx(20.0)
    assert page.holds.text() == "The tip holds 20 µl."
    # Raised to the travel height afterwards.
    assert float(xyz(session.robot)[2]) > 66.4 + 10


def test_the_answer_is_a_toast_in_a_sentence(app, session, page):
    page.aspirate_button.click()
    assert _until(app, lambda: session.in_tip > 0 and not page.jog.busy)
    said = toast_of(page)
    assert said.text().startswith("Aspirated 20 µl from ")
    assert said.text().endswith(".")
    assert said.property("tone") == "answer"
    assert not hasattr(page, "said")


def test_aspirating_again_adds_to_what_is_in_the_tip(app, session, page):
    page.aspirate_button.click()
    assert _until(app, lambda: session.in_tip > 0 and not page.jog.busy)
    page.aspirate_button.click()
    assert _until(app, lambda: session.in_tip > 20 and not page.jog.busy)
    assert session.in_tip == pytest.approx(40.0)
    assert page.holds.text() == "The tip holds 40 µl."


def test_change_saves_the_volume_and_height_only_with_save(session, page):
    assert page.change_box.isHidden()
    page.change_button.click()
    assert not page.change_box.isHidden()
    page.volume.setValue(30.0)
    page.volume.editingFinished.emit()               # left the box: no save
    page.above_bottom.setValue(2.0)
    picking = session.profile.picking
    assert picking.tip_liquid_vol == 20.0
    assert page.changing
    page.save_button.click()
    picking = session.profile.picking
    assert (picking.tip_liquid_vol, picking.tip_liquid_above_bottom) == (
        30.0, 2.0)
    assert page.change_box.isHidden()
    assert not page.changing
    assert page.aspirate_button.text() == "Aspirate 30 µl"
    assert page.settings_line.text() == (
        "30 µl · 2 mm above the dish bottom · 50 µl/s")


def test_cancel_drops_what_was_typed(session, page):
    page.change_button.click()
    page.volume.setValue(33.0)
    page.cancel_button.click()
    assert page.change_box.isHidden()
    assert session.profile.picking.tip_liquid_vol == 20.0
    page.change_button.click()
    assert page.volume.value() == 20.0


def test_enter_in_a_box_saves(session, page):
    page.change_button.click()
    page.volume.lineEdit().selectAll()
    QTest.keyClicks(page.volume.lineEdit(), "25")
    QTest.keyClick(page.volume.lineEdit(), Qt.Key.Key_Return)
    assert session.profile.picking.tip_liquid_vol == 25.0
    assert page.change_box.isHidden()


def test_dispense_gives_back_all_that_is_in_the_tip(app, session, page):
    assert not page.dispense_button.isEnabled()          # the tip is empty
    page.aspirate_button.click()
    assert _until(app, lambda: session.in_tip > 0 and not page.jog.busy)
    assert page.dispense_button.text() == "Dispense 20 µl"
    assert page.dispense_button.isEnabled()
    page.dispense_button.click()
    assert _until(app, lambda: session.in_tip == 0 and not page.jog.busy)
    assert page.summary() == "Tip empty"
    assert not page.dispense_button.isEnabled()
    # Raised to the travel height afterwards.
    assert float(xyz(session.robot)[2]) > 66.4 + 10


def test_during_a_run_only_what_the_run_allows(app, session, page):
    said, lent = ["Pause to use the tip."], []
    page.run_hold = RunHold(lambda: said[0], lambda: lent.append(1),
                            lambda: None)
    page.refresh()
    assert not page.aspirate_button.isEnabled()
    assert page.aspirate_button.toolTip() == "Pause to use the tip."
    said[0] = ""                                         # paused
    page.refresh()
    assert page.aspirate_button.isEnabled()
    page.aspirate_button.click()
    assert lent == [1]
    assert _until(app, lambda: session.in_tip > 0 and not page.jog.busy)


def test_a_well_is_chosen_on_the_mini_deck_and_aspirated_from_its_bottom(
        app, session, page):
    definition = resolve_definition(PLATE)
    session.load_labware(definition, 5)
    session.set_well_center(5, PLATE, (0.3, -0.2, 0.0), "A1")
    page.source.buttons[1].click()                       # A well
    assert not page.plate_dropdown.isHidden()
    assert page.settings_line.text() == "20 µl · 50 µl/s"
    assert not page.aspirate_button.isEnabled()          # no well yet
    page.plate_dropdown._chosen(5)
    page.plate_map.well_clicked.emit("B3")
    assert page.aspirate_button.isEnabled()
    page.aspirate_button.click()
    assert _until(app, lambda: session.in_tip > 0 and not page.jog.busy)
    lid = session.run_state.labware["5"].labware_id
    assert ("move_to_well", lid, "B3", "bottom") in session.robot.calls
    assert session.robot.well_offset == pytest.approx((0.3, -0.2, 0.0))
    assert session.in_tip == pytest.approx(20.0)


def test_nothing_is_offered_without_a_tip(app, session, page):
    session.drop_tip_in_place()
    assert _until(app, lambda: session.tip.attached is not True)
    page.refresh()
    assert not page.aspirate_button.isEnabled()
