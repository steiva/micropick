"""The movement keys (#57): as bound in Settings, they move the robot on
every screen whose buttons move it, and on no screen hidden behind the one
shown."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import PipetteOffset                # noqa: E402
from micropick.config.schema import PixelMap as PixelMapConfig   # noqa: E402
from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.readiness import DISH_POSITION                # noqa: E402
from micropick.gui.shell import (SETUP_PAGES, STEP_PAGES,        # noqa: E402
                                 MainWindow)
from micropick.hardware.protocols import xyz                     # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
W, H, K = 2592, 1944, 0.026


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    # Every step's needs met: the keys are what is tested, not the steps.
    real = tasks.step_state
    monkeypatch.setattr(tasks, "step_state", lambda s, t, step: tasks.StepState(
        real(s, t, step).done, None, tasks.optional(t, step)))
    w = MainWindow(Options(mock=True))
    w.show()
    w.session.probe_robot()
    w.session.new_run()
    w.session.load_labware(resolve_definition(PLATE), 5)
    _wait(app, lambda: not w.session.robot_lease.busy)
    yield w
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def _wait(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _free(app, window):
    panels = [getattr(p, "jog", None) for p in (*window.pages.values(),
                                                 *window.sheets.values())]
    return _wait(app, lambda: not window.session.robot_lease.busy
                 and not any(j is not None and j.busy for j in panels))


def _press(app, window, key):
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window)
    QTest.keyClick(window, key)
    app.processEvents()


def _moves_x(app, window, key=Qt.Key.Key_Right, sign=+1):
    """`key` moves the robot one step along X, and only one: no second
    page took it too."""
    assert _free(app, window)
    x = float(xyz(window.session.robot)[0])
    _press(app, window, key)
    assert _wait(app, lambda: float(xyz(window.session.robot)[0]) != x)
    assert _free(app, window)
    moved = float(xyz(window.session.robot)[0]) - x
    assert moved * sign > 0
    return abs(moved)


def _calibrated(session):
    """A linear toy map, a pipette offset and the position over the dish."""
    s = W / 2
    profile = session.profile
    profile.calibration.pixel_map = PixelMapConfig(
        degree=1, cu=W / 2, cv=H / 2, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0],
        ref=[W / 2, H / 2], bounds=[100.0, 100.0, W - 100.0, H - 100.0],
        image_size=[W, H], sweep_z=0.0)
    profile.calibration.pipette_offset = PipetteOffset(dx=10.0, dy=-5.0)
    profile.positions[DISH_POSITION] = (200.0, 150.0, 120.0)


def test_no_key_moves_the_robot_from_home(app, window):
    assert window.current_page() is window.home
    assert _free(app, window)
    before = xyz(window.session.robot)
    for key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
        _press(app, window, key)
    assert _free(app, window)
    assert xyz(window.session.robot) == pytest.approx(before)


def test_the_keys_move_the_robot_on_well_center(app, window):
    window.open_task_step("liquid", "well_center")
    assert window.current_page() is window.well_center_page
    step = window.well_center_page.jog.pad.step
    assert _moves_x(app, window) == pytest.approx(step, abs=0.01)


@pytest.mark.parametrize("task, step", [("manual", "work"),
                                        ("auto", "picking")])
def test_the_keys_move_the_robot_on_a_page_with_a_picture(app, window, task,
                                                          step):
    window.open_task_step(task, step)
    page = window.pages[STEP_PAGES[step]]
    assert window.current_page() is page
    assert _moves_x(app, window) == pytest.approx(page.jog.pad.step,
                                                  abs=0.01)


def test_the_keys_move_the_robot_on_dish_bottom(app, window):
    _calibrated(window.session)
    window.open_task_step("auto", "dish_bottom")
    page = window.dish_bottom_page
    assert window.current_page() is page
    page.refresh()
    assert _free(app, window)                  # its pose read, else refused
    page.move_button.click()
    assert _wait(app, lambda: page.up_button.isEnabled())
    assert _free(app, window)
    z = float(xyz(window.session.robot)[2])
    _press(app, window, Qt.Key.Key_PageDown)
    assert _wait(app, lambda: float(xyz(window.session.robot)[2]) < z)
    assert _free(app, window)
    assert float(xyz(window.session.robot)[2]) == pytest.approx(
        z - page.carousel.step, abs=0.01)


@pytest.mark.parametrize("item", ["calibration_position", "dish_position",
                                  "camera_calibration"])
def test_the_keys_move_the_robot_on_a_robot_setup_sheet(app, window, item):
    window.open_task_step("liquid", "well_center")
    window.open_setup_item(item)
    sheet = window.sheets[SETUP_PAGES[item]]
    assert sheet.isVisible()
    assert sheet.jog.pad.keys.isVisible()             # Keys, #58
    # One step, the sheet's: the page under it takes none.
    assert _moves_x(app, window) == pytest.approx(sheet.jog.pad.step,
                                                  abs=0.01)


def test_a_key_rebound_in_settings_moves_the_robot_at_once(app, window):
    session = window.session
    session.set_settings(session.settings.model_copy(
        update={"keys": {"x+": "J"}}))
    window.open_task_step("liquid", "well_center")
    _moves_x(app, window, key=Qt.Key.Key_J)
    x = float(xyz(session.robot)[0])
    _press(app, window, Qt.Key.Key_Right)             # no longer bound
    assert _free(app, window)
    assert float(xyz(session.robot)[0]) == pytest.approx(x)


def _field_has_keys(app, window, field):
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window)
    field.setFocus()
    assert _wait(app, lambda: QApplication.focusWidget() is field
                 or field.isAncestorOf(QApplication.focusWidget()))


FIELD_KEYS = [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp,
              Qt.Key.Key_PageDown]


def _the_name_editor(app, window):
    from PySide6.QtWidgets import QLineEdit
    from micropick.gui.widgets.point_list import PointRow
    page = window.manual_page
    window.session.add_memo_point(100.0, 100.0, None)
    assert _wait(app, lambda: page.points_list.findChildren(PointRow))
    row = page.points_list.findChildren(PointRow)[0]
    row.edit("name")
    assert _wait(app, lambda: row.findChildren(QLineEdit))
    return row.findChildren(QLineEdit)[0]


@pytest.mark.parametrize("key", FIELD_KEYS)
@pytest.mark.parametrize("which", ["number", "name"])
def test_no_jog_key_moves_the_robot_while_a_field_has_the_keyboard(
        app, window, which, key):
    window.open_task_step("manual", "work")
    page = window.manual_page
    assert _free(app, window)
    field = (page.in_place_amount if which == "number"
             else _the_name_editor(app, window))
    _field_has_keys(app, window, field)
    assert page.jog.typing
    moves = window.session.robot.moves
    QTest.keyClick(QApplication.focusWidget(), key)
    app.processEvents()
    assert _free(app, window)
    assert window.session.robot.moves == moves


def test_up_steps_the_number_field_on_a_page_with_a_pad(app, window):
    window.open_task_step("manual", "work")
    field = window.manual_page.in_place_amount
    assert _free(app, window)
    _field_has_keys(app, window, field)
    value = field.value()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Up)
    app.processEvents()
    assert field.value() > value
