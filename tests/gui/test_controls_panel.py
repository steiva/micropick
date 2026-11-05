"""Controls: the jog pad over a picture (#110, MANUAL.md §2.3, §10.20-22).
Only the face: what a step does stays `JogPanel`'s."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.gui.widgets.jog_pad import JogPad                 # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pad(app):
    pad = JogPad(bindings={})
    pad.show()
    app.processEvents()
    yield pad
    pad.deleteLater()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    window = MainWindow(Options(mock=True))
    window.show()
    yield window
    window.hide()
    window.session.shutdown()
    window.deleteLater()


def test_the_panel_is_titled_controls_and_sized_as_the_prototype(pad):
    assert pad.fold_button.text() == "Controls"
    assert pad.width() == 340
    assert (pad.buttons["x+"].width(), pad.buttons["x+"].height()) == (48, 44)
    assert (pad.buttons["z+"].width(), pad.buttons["z+"].height()) == (64, 44)


def test_folded_the_header_stays_with_undo_step_and_keys(app, pad):
    pad.set_folded(True)
    app.processEvents()
    assert pad.undo_button.isVisible() and pad.keys.isVisible()
    assert not pad.carousel.isVisible() and not pad.pads.isVisible()
    assert pad.width() == 340


def test_keys_keeps_its_list_under_the_panel_folded_or_open(app, pad):
    """Keys stays in the folded header, so it has to answer there too."""
    pad.set_key_lines(["U   undo the last step"])
    pad.keys.setChecked(True)
    assert pad.key_list.isVisible()
    pad.set_folded(True)
    assert pad.key_list.isVisible()
    pad.keys.setChecked(False)
    assert not pad.key_list.isVisible()


def test_a_note_while_typing_follows_the_list_when_folded(app, pad):
    pad.set_note("The keys are the field's.")
    pad.set_folded(True)
    assert not pad.note.isVisible()
    pad.keys.setChecked(True)
    assert pad.note.isVisible()
    pad.keys.setChecked(False)
    assert not pad.note.isVisible()


def test_keys_shows_each_key_on_its_button_as_bound(pad):
    pad.set_bindings({"x+": "D"})
    pad.keys.setChecked(True)
    assert pad.buttons["x+"].keycap == "D"
    assert pad.buttons["y+"].keycap == "↑"
    assert pad.buttons["z+"].keycap == "PgUp"


def test_the_step_keys_stay_plus_and_minus(pad):
    pad.keys.setChecked(True)
    assert pad.carousel.smaller.keycap == "-"
    assert pad.carousel.bigger.keycap == "+"


def test_a_long_key_line_wraps_inside_the_panel(app, pad):
    pad.set_key_lines(["Ctrl+Shift+Alt+F12   " + "a very long line " * 12])
    pad.keys.setChecked(True)
    app.processEvents()
    assert pad.width() == 340
    assert pad.sizeHint().width() == 340


def test_the_fold_is_the_screens_and_survives_leaving_it(app, window):
    manual, picking = window.manual_page, window.picking_page
    assert picking.jog.pad.folded and not manual.jog.pad.folded
    manual.jog.pad.fold_button.click()
    window.top.home_clicked.emit()
    app.processEvents()
    assert manual.jog.pad.folded
    assert picking.jog.pad.folded
    assert not window.liquid_plan_page.jog.pad.folded


def test_the_tip_steps_two_pads_are_one_screens_fold(window):
    """Its fine-tune pad takes the main pad's place on the same picture."""
    block = window.tip_calibration_page.calibration
    block.jog.pad.fold_button.click()
    assert block.touch_jog.pad.folded
    block.touch_jog.pad.fold_button.click()
    assert not block.jog.pad.folded
