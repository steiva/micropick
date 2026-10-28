"""The shell (REDESIGN §4): Home and its task tiles, a task's steps under
the StepBar with Back and Next, the one banner naming a dimmed step's
first unmet need, and the breadcrumb."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton        # noqa: E402

from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.shell import MainWindow                     # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    w.show()
    yield w
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def _settle(app, window, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while window.session.robot_lease.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _connect(app, window):
    window.session.probe_robot()
    window.session.new_run()
    _settle(app, window)


def test_the_window_opens_on_home_with_the_three_tasks(window):
    assert window.current_page() is window.home
    assert window.top.path == []
    tiles = window.home.tiles
    assert [tiles[t].title for t in ("auto", "manual", "liquid")] == [
        "Auto pick", "Manual pick", "Liquid"]
    assert tiles["auto"].text == ("Cuboids from a dish into a plate, by the "
                                  "robot, following your plan.")
    assert tiles["manual"].text == ("Move cuboids one at a time: you choose "
                                    "which and where.")
    assert tiles["liquid"].text == "Add or remove liquid in many wells at once."


def _chips(window):
    return [(chip.text(), chip.done) for chip in window.step_bar.chips]


def test_a_tile_opens_its_task_and_home_goes_back(app, window):
    window.home.tiles["auto"].clicked.emit()
    assert window.top.path == ["Auto pick"]
    assert [name for name, _done in _chips(window)] == [
        "Deck", "Tip", "Dish bottom", "Plan", "Picking"]
    assert window.step_bar.isVisible() and window.footer.isVisible()
    assert window.current_page() is window.labware_page
    window.top.home_clicked.emit()
    assert window.current_page() is window.home
    assert window.top.path == []
    assert not window.step_bar.isVisible()


def _banner(window):
    if not window.banner.isVisible():
        return None
    buttons = window.banner.findChildren(QPushButton)
    return window.banner.label.text(), [b for b in buttons if b.isVisible()]


def test_a_step_without_its_needs_is_dimmed_under_one_banner(app, window):
    window.home.tiles["auto"].clicked.emit()
    text, (go,) = _banner(window)
    assert text == "The robot is not connected."
    assert go.text() == "Go to Home"
    assert not window.labware_page.isEnabled()
    go.click()
    assert window.current_page() is window.home


def test_the_banner_names_only_the_first_need_and_goes_there(app, window):
    _connect(app, window)
    window.home.tiles["auto"].clicked.emit()
    assert _banner(window) is None                 # Deck needs the robot only
    assert window.labware_page.isEnabled()
    window.step_bar.clicked.emit(4)                # Picking: everything missing
    text, (go,) = _banner(window)
    assert text == "Check the deck first."
    go.click()
    assert window.current_page() is window.labware_page
    assert _banner(window) is None


def test_plans_banner_asks_for_a_plate_and_goes_to_deck(app, window):
    _connect(app, window)
    window.home.tiles["auto"].clicked.emit()
    window.step_bar.clicked.emit(3)                # Plan
    assert window.step == "plan"
    text, (go,) = _banner(window)
    assert (text, go.text()) == ("Put a plate on the deck first.",
                                 "Go to Deck")
    go.click()
    assert window.step == "deck"


def test_a_robot_setup_need_opens_its_page_and_its_crumb_comes_back(
        app, window):
    from micropick.config.labware import resolve_definition
    _connect(app, window)
    window.session.load_labware(
        resolve_definition("opentrons_96_tiprack_300ul"), 10)
    window.home.tiles["auto"].clicked.emit()       # opens on Tip
    assert window.step == "tip"
    text, (go,) = _banner(window)
    assert text == "Calibrate the camera first."
    go.click()
    sheet = window.calibration_sheet
    assert sheet.isVisible()
    assert sheet.title_label.text() == "Camera calibration"
    assert window.top.path == ["Auto pick", "Camera calibration"]
    window.top.crumb_clicked.emit(0)
    assert not sheet.isVisible()
    assert window.step == "tip"
    assert window.top.path == ["Auto pick"]


def _tip_calibrated(window):
    """A tip on, and the tip calibration measured on it in this robot
    session, as the pipette calibration leaves it."""
    from micropick.config.labware import LabwareDefinition
    from micropick.config.schema import PipetteOffset
    session = window.session
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")
    session.profile.calibration.pipette_offset = PipetteOffset(
        dx=0.0, dy=0.0, robot_session=session.run_state.run_id,
        tip_pickup=session.tip.pickup)
    session.profile_changed.emit(session.profile)


def test_next_is_enabled_when_the_step_is_done_and_marks_it(app, window):
    from micropick.config.labware import resolve_definition
    _connect(app, window)
    window.home.tiles["manual"].clicked.emit()
    assert window.step == "deck"
    assert not window.footer.next.isEnabled()      # nothing on the deck
    assert _chips(window)[0] == ("Deck", False)
    window.session.load_labware(
        resolve_definition("opentrons_96_tiprack_300ul"), 10)
    assert _chips(window)[0] == ("Deck", True)     # done without Next
    assert window.footer.next.isEnabled()
    window.footer.next.click()
    assert window.step == "tip"
    assert _chips(window)[0] == ("Deck", True)
    assert not window.footer.next.isEnabled()      # no tip calibrated
    _tip_calibrated(window)
    assert _chips(window)[1] == ("Tip", True)
    assert window.footer.next.isEnabled()
    window.footer.back.click()
    assert window.step == "deck"
    window.footer.back.click()
    assert window.current_page() is window.home


def test_switching_from_auto_to_manual_keeps_the_shared_steps(app, window):
    _connect(app, window)
    _tip_calibrated(window)                        # the rack on the deck first
    window.home.tiles["auto"].clicked.emit()
    assert window.step == "dish_bottom"            # the first not done
    window.session.confirm("dish_bottom")
    window.top.home_clicked.emit()
    window.home.tiles["manual"].clicked.emit()
    assert window.top.path == ["Manual pick"]
    assert window.step == "work"
    assert window.current_page() is window.manual_page
    assert _chips(window) == [("Deck", True), ("Tip", True),
                              ("Dish bottom", True), ("Work", False)]
    assert window.footer.isVisible() and not window.footer.next.isVisible()


def test_no_profile_is_named_or_chosen(app, window):
    from PySide6.QtWidgets import QComboBox, QLabel
    assert window.session.profile is not None      # loaded by itself
    shown = [w for kind in (QLabel, QPushButton)
             for w in window.home.findChildren(kind) if w.isVisible()]
    assert not [w.text() for w in shown if "profile" in w.text().lower()]
    assert not [box for box in window.findChildren(QComboBox)
                if "profile" in box.objectName().lower()
                or type(box).__name__ == "_ProfileChooser"]


def test_a_page_asked_for_from_a_step_keeps_the_task(app, window):
    window.home.tiles["auto"].clicked.emit()
    window.step_bar.clicked.emit(4)                # Picking
    window.session.page_requested.emit("picking")
    assert window.step == "picking"                # not Dish bottom
    window.session.page_requested.emit("settings")
    assert window.top.path == ["Auto pick", "Settings"]
    assert window.settings_sheet.isVisible()
    window.session.page_requested.emit("labware")   # closes the sheet
    assert not window.settings_sheet.isVisible()
    assert window.step == "deck" and window.top.path == ["Auto pick"]


def test_dish_bottom_is_its_own_page_and_done_by_use_it_not_by_next(
        app, window):
    from datetime import datetime, timezone
    _connect(app, window)
    _tip_calibrated(window)
    window.session.profile.positions["observe"] = (200.0, 150.0, 120.0)
    window.session.profile.calibration.dish_bottom_set_at = datetime.now(
        timezone.utc)
    window.home.tiles["manual"].clicked.emit()     # opens on Dish bottom
    page = window.dish_bottom_page
    assert window.current_page() is page
    assert window.top.path == ["Manual pick"]
    assert not window.footer.next.isEnabled()
    page.use_button.click()
    assert window.footer.next.isEnabled()
    assert _chips(window)[2] == ("Dish bottom", True)


def test_liquids_plan_is_its_own_page_and_done_by_use_this_plan(app, window):
    from micropick.core.liquid import Aspirate, Group, Location, Program
    _connect(app, window)
    window.home.tiles["liquid"].clicked.emit()
    window.step_bar.clicked.emit(3)                # Plan
    page = window.liquid_plan_page
    assert window.current_page() is page
    assert window.step == "liquid_plan"
    assert _chips(window)[3] == ("Plan", False)
    page.program = Program(
        trash=Location(kind="point", point="waste"),
        groups=[Group(name="g", slot="5", load_name="plate", wells=["A1"],
                      steps=[Aspirate()])])
    window.session.set_liquid_plan(page.program)
    assert _chips(window)[3] == ("Plan", True)
    window.step_bar.clicked.emit(4)                # Run
    assert window.current_page() is window.liquid_page
    assert window.liquid_page.program == page.program
