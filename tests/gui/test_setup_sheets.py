"""Robot setup's platform and position sheets (#12): what stands in each
slot, and the three stored positions - over the dish, shake, calibration -
set from a sheet and shown with a ✓ on their rows."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.core.deck import slot_center                      # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.hardware.protocols import xyz                     # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    w.show()
    w.session.probe_robot()
    w.session.new_run()
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
    assert condition()


def _open(window, item):
    window.open_robot_setup()
    window.robot_setup_page.rows[item].clicked.emit()


def _saved(app, window, name):
    """Done reads the pose from the robot first: wait for it to be saved."""
    _wait(app, lambda: name in window.session.profile.positions)


def _row(window, item):
    row = window.robot_setup_page.rows[item]
    return row.done, row.value.text()


# -- platforms -----------------------------------------------------------------

def _choose(sheet, slot, kind):
    sheet.tiles[slot].click()
    assert sheet.slot == slot
    sheet.kind.setCurrentText(kind)


def test_the_platforms_row_opens_the_deck(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    assert sheet.isVisible()
    assert window.top.path == ["Robot setup", "Platforms"]
    assert sorted(sheet.tiles) == list(range(1, 13))
    # Slot 12 is the fixed trash: nothing stands in it.
    assert not sheet.tiles[12].isEnabled()
    assert [sheet.kind.itemText(i) for i in range(sheet.kind.count())] == [
        "Picking platform", "Calibration module", "Nothing"]
    # Done and × only close: there is no Cancel.
    assert sheet.cancel_button.isHidden()
    assert sheet.done_button.text() == "Done"


def test_save_changes_saves_what_stands_in_the_slots(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    assert not sheet.save_button.isEnabled()
    for slot, kind in ((5, "Picking platform"), (8, "Picking platform"),
                       (9, "Calibration module")):
        _choose(sheet, slot, kind)
    assert "Calibration module" in sheet.tiles[9].text()
    assert sheet.save_button.isEnabled()
    sheet.save_button.click()
    # Saved at once; the sheet stays open and says so.
    assert sheet.isVisible()
    assert not sheet.save_button.isEnabled()
    assert sheet.saved_note.text() == (
        "Saved: slot 5 · Picking platform, slot 8 · Picking platform, "
        "slot 9 · Calibration module")
    deck = window.session.profile.deck
    assert {s: deck.module_for(s).name for s in (5, 8, 9)} == {
        5: "Picking platform", 8: "Picking platform", 9: "Calibration module"}
    assert deck.module_for(9).height_mm == deck.module_type(
        "Calibration module").height_mm
    assert _row(window, "platforms") == (
        True, "Picking platform in 5 and 8 · Calibration module in 9")
    # A clean draft closes without a question.
    sheet.done_button.click()
    assert sheet.question is None
    assert not sheet.isVisible()
    assert window.top.path == ["Robot setup"]


def test_a_change_back_to_what_is_saved_is_no_change(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    _choose(sheet, 5, "Picking platform")
    assert sheet.save_button.isEnabled()
    sheet.kind.setCurrentText("Nothing")
    assert not sheet.save_button.isEnabled()


def test_nothing_takes_a_platform_away(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    _choose(sheet, 5, "Picking platform")
    sheet.save_button.click()
    sheet.kind.setCurrentText("Nothing")
    sheet.save_button.click()
    assert sheet.saved_note.text() == "Saved: slot 5 · Nothing"
    assert window.session.profile.deck.modules == []
    assert _row(window, "platforms") == (False, "Not set.")


def test_closing_with_changes_asks_and_discard_drops_them(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    _choose(sheet, 5, "Picking platform")
    sheet.close_button.click()
    # Asked, still open, nothing saved.
    assert sheet.question is not None and sheet.question.isVisible()
    assert sheet.isVisible()
    assert sheet.question.safe.text() == "Save"
    assert sheet.question.destructive.text() == "Discard"
    sheet.question.destructive.click()
    assert not sheet.isVisible()
    assert window.top.path == ["Robot setup"]
    assert window.session.profile.deck.modules == []
    # Opened again: the draft is what is saved.
    _open(window, "platforms")
    sheet.tiles[5].click()
    assert sheet.kind.currentText() == "Nothing"
    assert not sheet.save_button.isEnabled()


def test_closing_with_changes_asks_and_save_saves_them(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    _choose(sheet, 9, "Calibration module")
    sheet.done_button.click()
    assert sheet.isVisible()
    sheet.question.safe.click()
    assert not sheet.isVisible()
    assert window.session.profile.deck.module_for(9).name == (
        "Calibration module")
    assert _row(window, "platforms")[0]


def test_a_slots_height_is_changed_and_saved(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    _choose(sheet, 5, "Picking platform")
    assert sheet.height.value() == pytest.approx(64.2)    # the type's own
    sheet.height.setValue(63.9)
    sheet.save_button.click()
    deck = window.session.profile.deck
    assert deck.module_for(5).height_mm == pytest.approx(63.9)
    # The type keeps its usual height.
    assert deck.module_type("Picking platform").height_mm == pytest.approx(64.2)
    sheet.height.setValue(64.0)
    assert sheet.save_button.isEnabled()


def test_a_new_type_is_offered_and_saved(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    sheet.new_name.setText("Tall platform")
    sheet.new_height.setValue(80.0)
    sheet.add_type()
    assert sheet.save_button.isEnabled()
    _choose(sheet, 4, "Tall platform")
    assert sheet.height.value() == pytest.approx(80.0)
    sheet.save_button.click()
    deck = window.session.profile.deck
    assert deck.module_type("Tall platform").height_mm == pytest.approx(80.0)
    assert deck.module_for(4).name == "Tall platform"


def test_a_type_of_that_name_is_not_added_twice(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    sheet.new_name.setText("picking platform")
    sheet.add_type()
    names = [sheet.kind.itemText(i) for i in range(sheet.kind.count())]
    assert names == ["Picking platform", "Calibration module", "Nothing"]
    assert sheet.type_note.text() == "There is a type of that name."
    assert not sheet.save_button.isEnabled()


def test_delete_type_keeps_what_stands(app, window):
    _open(window, "platforms")
    sheet = window.platforms_sheet
    _choose(sheet, 9, "Calibration module")
    sheet.save_button.click()
    sheet.delete_type()
    # Still standing in slot 9, under its own name.
    assert sheet.kind.currentText() == "Calibration module"
    sheet.save_button.click()
    assert sheet.saved_note.text() == "Saved: platform types"
    deck = window.session.profile.deck
    assert deck.module_type("Calibration module") is None
    assert deck.module_for(9).name == "Calibration module"


# -- the positions: numbered steps, Save position (#33) ------------------------

def _steps(sheet):
    """Each card's heading and mark, in order."""
    return [(card.heading.text(), card.state)
            for card in sheet.cards.values()]


def _calibration_module(window, slot=9):
    from micropick.config.schema import DeckModule
    window.session.set_deck_modules(
        [DeckModule(slots=[slot], offset=[0, 0, 64], name="Calibration module")])


def _over_slot(app, window, slot):
    """The camera over `slot`'s center: the tip there less the offset."""
    sheet = window.position_sheet
    dx, dy = window.session.settings.tip_cal_start_offset
    cx, cy = slot_center(slot)
    _wait(app, lambda: sheet.jog.pose is not None
          and sheet.jog.pose[:2] == pytest.approx((cx - dx, cy - dy),
                                                  abs=0.01))
    _wait(app, lambda: sheet.slots.marked == slot)


def test_the_calibration_position_is_steps_one_after_another(app, window):
    _calibration_module(window)
    _open(window, "calibration_position")
    sheet = window.position_sheet
    assert window.top.path == ["Robot setup", "Calibration position"]
    assert [title for title, _ in _steps(sheet)] == [
        "1. Place the crosshair disc", "2. Which slot is it in?",
        "3. Center the crosshair", "4. Save position"]
    assert [mark for _, mark in _steps(sheet)] == [
        "todo", "blocked", "blocked", "blocked"]
    assert sheet.buttons["place"].text() == "It is there"
    # Each step waits for the one before it.
    assert not sheet.slots.isEnabled()
    assert not sheet.buttons["center"].isEnabled()
    assert not sheet.save_button.isEnabled()
    sheet.buttons["place"].click()
    _wait(app, lambda: sheet.slots.isEnabled())
    assert not sheet.buttons["center"].isEnabled()
    sheet.slots.button(9).click()
    _over_slot(app, window, 9)
    _wait(app, lambda: sheet.buttons["center"].isEnabled())
    assert not sheet.save_button.isEnabled()
    sheet.buttons["center"].click()
    _wait(app, lambda: sheet.save_button.isEnabled())
    assert "tip_calib" not in window.session.profile.positions
    x, y, z = xyz(window.session.robot)
    sheet.save_button.click()
    _saved(app, window, "tip_calib")
    assert window.session.profile.positions["tip_calib"] == pytest.approx(
        (x, y, z), abs=0.01)
    # Saved, said, and the sheet stays open.
    assert sheet.isVisible()
    assert sheet.saved_note.text() == (
        f"Saved: slot 9 · X {x:.1f} Y {y:.1f} Z {z:.1f}")
    assert [mark for _, mark in _steps(sheet)] == ["ok"] * 4
    assert set(window.session.profile.positions) == {"tip_calib"}
    sheet.done_button.click()
    assert not sheet.isVisible()
    assert _row(window, "calibration_position")[0]
    assert window.top.path == ["Robot setup"]


def test_the_dish_position_is_the_source_dish_on_its_platform(app, window):
    _open(window, "dish_position")
    sheet = window.position_sheet
    assert window.top.path == ["Robot setup", "Position over the dish"]
    assert [title for title, _ in _steps(sheet)] == [
        "1. Place the source dish", "2. Which slot is it in?",
        "3. Center the dish rim around the crosshair", "4. Save position"]
    # Nothing chosen for the operator.
    assert sheet.slots.marked is None
    sheet.buttons["place"].click()
    sheet.slots.button(8).click()
    _over_slot(app, window, 8)
    _wait(app, lambda: sheet.buttons["center"].isEnabled())
    sheet.buttons["center"].click()
    _wait(app, lambda: sheet.save_button.isEnabled())
    where = xyz(window.session.robot)
    sheet.save_button.click()
    _saved(app, window, "observe")
    assert window.session.profile.positions["observe"] == pytest.approx(
        where, abs=0.01)
    assert sheet.saved_note.text().startswith("Saved: slot 8 · X ")
    done, value = _row(window, "dish_position")
    assert done and value.startswith("≈ slot ")


def test_done_and_close_only_close(app, window):
    """No Cancel; Done and × store nothing, and a saved pose stays."""
    _open(window, "dish_position")
    sheet = window.position_sheet
    assert sheet.cancel_button.isHidden()
    assert sheet.done_button.isEnabled()
    _wait(app, lambda: sheet.jog.pose is not None)
    sheet.done_button.click()
    assert not sheet.isVisible()
    assert "observe" not in window.session.profile.positions
    assert _row(window, "dish_position") == (False, "Not set.")

    window.session.remember("observe", (200.0, 150.0, 110.0))
    _open(window, "dish_position")
    sheet.buttons["place"].click()
    sheet.slots.button(5).click()
    _over_slot(app, window, 5)
    sheet.close_button.click()
    assert not sheet.isVisible()
    assert window.session.profile.positions["observe"] == pytest.approx(
        (200.0, 150.0, 110.0))
    assert window.top.path == ["Robot setup"]


def test_a_saved_position_is_on_top_and_go_to_marks_the_steps(app, window):
    session = window.session
    motion = session.motion
    motion.raise_tip()
    top = motion.travel_z
    x, y, _z = xyz(session.robot)
    # Taught after a retract: higher than the robot can be sent to.
    session.remember("tip_calib", (x + 10.0, y, top + 1.0))
    _open(window, "calibration_position")
    sheet = window.position_sheet
    assert not sheet.saved_line.isHidden()
    assert sheet.saved_line.text().startswith("Saved: slot ")
    assert not sheet.goto_button.isHidden()
    assert not sheet.save_button.isEnabled()
    _wait(app, lambda: sheet.goto_button.isEnabled())
    sheet.goto_button.click()
    _wait(app, lambda: sheet.jog.pose is not None
          and sheet.jog.pose[0] == pytest.approx(x + 10.0, abs=0.01))
    assert xyz(session.robot)[2] <= top + 1e-6
    # The slot and the centering are done: saving again is one click.
    _wait(app, lambda: sheet.save_button.isEnabled())
    assert [mark for _, mark in _steps(sheet)] == [
        "ok", "ok", "ok", "todo"]


def test_a_position_not_saved_has_no_line_on_top(app, window):
    _open(window, "dish_position")
    sheet = window.position_sheet
    assert sheet.saved_line.isHidden()
    assert sheet.goto_button.isHidden()


def test_before_camera_calibration_a_click_on_the_picture_moves_nothing(
        app, window):
    """No pixel map yet: the mouse row has no click line, says to use the
    arrows, and the camera calibration sheet's row says the same."""
    assert window.session.profile.pixel_map is None
    _open(window, "dish_position")
    help_ = window.position_sheet.view.mouse_help
    assert "Use the arrows to move the camera." in help_
    assert not any(line.startswith("Click") for line in help_)
    window.position_sheet.close_button.click()
    view = window.calibration_sheet.view
    assert "Use the arrows to move the camera." in view.mouse_help
    assert not any(line.startswith("Click") for line in view.mouse_help)


# -- shake -----------------------------------------------------------------------

def test_the_shake_position_is_saved_only_by_save_position(app, window):
    _open(window, "shake_position")
    sheet = window.position_sheet
    assert window.top.path == ["Robot setup", "Shake position"]
    assert [title for title, _ in _steps(sheet)] == [
        "1. Lower the tip into the hole", "2. Save position"]
    assert sheet.note.label.text() == (
        "The camera can't see this. Watch the robot itself.")
    assert not sheet.jog.isHidden()
    assert not sheet.save_button.isEnabled()
    sheet.done_button.click()
    assert "shake" not in window.session.profile.positions
    _open(window, "shake_position")
    sheet.buttons["lower"].click()
    _wait(app, lambda: sheet.save_button.isEnabled())
    sheet.save_button.click()
    _saved(app, window, "shake")
    assert _row(window, "shake_position")[0]
