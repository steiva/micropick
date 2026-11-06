"""The redesign's shared components (REDESIGN §3), each in its states."""

import math
import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer         # noqa: E402
from PySide6.QtGui import QMouseEvent                                   # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                     # noqa: E402

from micropick.gui import theme                                         # noqa: E402
from micropick.gui.widgets.banner import Banner                         # noqa: E402
from micropick.gui.widgets.camera_view import CameraView                # noqa: E402
from micropick.gui.widgets.controls import Segmented, Switch            # noqa: E402
from micropick.gui.widgets.fold_card import FoldCard, FoldGroup         # noqa: E402
from micropick.gui.widgets.footer import Footer                         # noqa: E402
from micropick.gui.widgets.jog_pad import STEPS, JogPad                 # noqa: E402
from micropick.gui.widgets.mini_deck import (DeckTile,                  # noqa: E402
                                             MiniDeckDropdown, Tile)
from micropick.gui.widgets.overlays import (ConfirmDialog,              # noqa: E402
                                            LockOverlay, SheetWindow)
from micropick.gui.widgets.plate_grid import PlateGrid, PlateTools      # noqa: E402
from micropick.gui.widgets.step_bar import StepBar                      # noqa: E402
from micropick.gui.widgets.tip_gauge import TipGauge                    # noqa: E402
from micropick.gui.widgets.top_bar import TopBar                        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _in_the_theme(themed):
    """Every test here sees the program's theme (conftest's `themed`)."""


def _plate(rows=8, columns=12):
    letters = "ABCDEFGHIJKLMNOP"[:rows]
    return [[f"{r}{c}" for r in letters] for c in range(1, columns + 1)]


def _click(widget, pos):
    for kind in (QMouseEvent.Type.MouseButtonPress,
                 QMouseEvent.Type.MouseButtonRelease):
        event = QMouseEvent(kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)


def test_the_stylesheet_names_no_token_it_does_not_have(app):
    import re
    sheet = re.sub(r"/\*.*?\*/", "", theme.stylesheet(), flags=re.DOTALL)
    assert not re.findall(r"@[a-z][a-z0-9-]*(?=[;\s)])", sheet)
    assert theme.COLORS["accent"] in sheet
    assert theme.accent() == theme.COLORS["accent"]


def test_the_home_crumb_is_the_same_on_home_and_further_in(app):
    from PySide6.QtWidgets import QPushButton
    bar = TopBar()
    clicks = []
    bar.home_clicked.connect(lambda: clicks.append(1))

    def home_crumb():
        buttons = [b for b in bar.findChildren(QPushButton)
                   if b.objectName() == "crumb" and not b.isHidden()]
        return buttons[0]

    bar.set_path([])
    here = home_crumb()
    assert here.text() == "Home" and not here.icon().isNull()
    width = here.sizeHint().width()
    here.click()
    assert clicks == []                       # Home on Home goes nowhere
    bar.set_path(["Auto pick"])
    further = home_crumb()
    assert further.text() == "Home" and not further.icon().isNull()
    # The same button, so the same gap between the house and "Home".
    assert further.sizeHint().width() == width
    assert further.iconSize() == here.iconSize()
    further.click()
    assert clicks == [1]


def test_top_bar_path_status_stop_and_toggles(app):
    bar = TopBar()
    homes = []
    bar.home_clicked.connect(lambda: homes.append(1))
    bar.set_path(["Robot setup", "Upper camera"])
    assert bar.path == ["Robot setup", "Upper camera"]
    assert not bar.status.isVisibleTo(bar)
    bar.set_status("Paused", "paused")
    assert bar.status.isVisibleTo(bar) and "Paused" in bar.status.text()
    bar.set_status(None)
    assert not bar.status.isVisibleTo(bar)
    assert not bar.stop.isVisibleTo(bar)
    bar.set_stop_visible(True)
    assert bar.stop.isVisibleTo(bar)
    locks = []
    bar.lock_toggled.connect(locks.append)
    bar.lock.click()
    assert locks == [True] and bar.lock.isChecked()
    bar.set_lock(False, allowed=False, reason="Picking has the robot")
    assert not bar.lock.isEnabled()
    assert "Picking" in bar.lock.toolTip()
    bar.set_parking(True)
    assert bar.park.text() == "Parking…"
    bar.set_parking(False)
    assert bar.park.text() == "Park"


def test_a_bar_button_keeps_its_width_while_busy(app):
    bar = TopBar()
    bar.resize(1400, 52)
    bar.show()
    app.processEvents()
    places = [bar.light.geometry(), bar.lock.geometry(), bar.park.geometry(),
              bar.retract.geometry()]
    bar.set_parking(True)
    bar.set_retracting(True)
    app.processEvents()
    assert bar.park.icon().isNull() and bar.retract.icon().isNull()
    assert places == [bar.light.geometry(), bar.lock.geometry(),
                      bar.park.geometry(), bar.retract.geometry()]
    bar.set_parking(False)
    bar.set_retracting(False)
    assert not bar.park.icon().isNull() and not bar.retract.icon().isNull()
    bar.close()


def test_step_bar_marks_done_and_current_and_reports_a_click(app):
    bar = StepBar(["Deck", "Tip", "Plan"])
    bar.set_state(1, [True, False, False])
    assert [c.done for c in bar.chips] == [True, False, False]
    assert [c.current for c in bar.chips] == [False, True, False]
    clicked = []
    bar.clicked.connect(clicked.append)
    bar.chips[2].click()
    assert clicked == [2]


def _chip(optional, *, done=False, current=False):
    """The second step of a bar, as drawn at its own size."""
    bar = StepBar()
    bar.set_steps(["Deck", "Well center"], optional=[False, optional])
    bar.set_state(1 if current else 0, [False, done])
    chip = bar.chips[1]
    chip.resize(chip.sizeHint())
    return bar, chip


def _around_the_number(chip):
    """The pixels on a circle just outside the round mark."""
    image = chip.grab().toImage()
    x = 8 + chip.MARK / 2
    y = chip.height() / 2
    radius = chip.MARK / 2 + 3
    return [image.pixelColor(round(x + radius * math.cos(math.radians(a))),
                             round(y + radius * math.sin(math.radians(a))))
            for a in range(0, 360, 3)]


@pytest.mark.parametrize("current", [False, True])
def test_a_marked_step_not_done_has_a_dashed_ring(app, current):
    _bar, plain = _chip(False, current=current)
    _bar2, marked = _chip(True, current=current)
    changed = [a != b for a, b in zip(_around_the_number(plain),
                                      _around_the_number(marked))]
    # Dashes, not a line: some of the circle is drawn, some is left.
    assert 0.2 < sum(changed) / len(changed) < 0.9


def test_the_done_mark_replaces_the_ring(app):
    _bar, plain = _chip(False, done=True)
    _bar2, marked = _chip(True, done=True)
    assert plain.grab().toImage() == marked.grab().toImage()


def test_a_step_shown_and_done_has_no_ring(app):
    _bar, plain = _chip(False, done=True, current=True)
    _bar2, marked = _chip(True, done=True, current=True)
    assert plain.grab().toImage() == marked.grab().toImage()


def test_the_ring_takes_no_width(app):
    _bar, plain = _chip(False)
    _bar2, marked = _chip(True)
    assert plain.sizeHint() == marked.sizeHint()


def test_footer_navigation_and_run_status(app):
    footer = Footer()
    footer.set_navigation(next_enabled=False)
    assert not footer.next.isEnabled()
    assert footer.back.isVisibleTo(footer)
    footer.set_status("Picking", "Well A13 of 24 · taking a cuboid",
                      tone="busy")
    assert footer.tag.text() == "PICKING"
    assert "A13" in footer.sentence.text()
    footer.clear_status()
    assert not footer.tag.isVisibleTo(footer)


def test_fold_group_keeps_one_card_open(app):
    a = FoldCard("Cuboid sizes", summary="250–500 µm", open=True)
    b = FoldCard("Plate · slot 5", summary="12 of 24 wells")
    FoldGroup([a, b])
    assert a.is_open() and not b.is_open()
    b.header.click()
    assert b.is_open() and not a.is_open()
    b.header.click()
    assert not a.is_open() and not b.is_open()
    assert b.summary.text() == "12 of 24 wells"


def test_segmented_and_switch(app):
    seg = Segmented(["In a well", "At a point", "By hand"])
    chosen = []
    seg.changed.connect(chosen.append)
    seg.buttons[2].click()
    assert seg.current() == 2 and chosen == [2]
    seg.set_current(0)
    assert seg.current() == 0 and chosen == [2]
    switch = Switch("Snap to found cuboids")
    switch.click()
    assert switch.isChecked()


def test_banner_carries_its_actions(app):
    banner = Banner("Put a tip on first.", "warn")
    go = banner.add_action("Go to Tip")
    assert banner.tone == "warn" and go.text() == "Go to Tip"
    banner.clear_actions()
    banner.set_tone("ok")
    assert banner.property("tone") == "ok"


def test_plate_grid_toggles_wells_rows_and_columns_with_undo(app):
    grid = PlateGrid()
    grid.set_ordering(_plate())
    grid.resize(26 + 12 * 30, 26 + 8 * 30)
    grid.toggle(["A1"])
    assert grid.selection == {"A1"}
    grid.toggle(["A1"])
    assert grid.selection == set()
    column = _plate()[0]
    grid.toggle(column)
    assert grid.selection == set(column)
    grid.toggle(["A1", "A2"])          # not all chosen: both put in
    assert {"A1", "A2"} <= grid.selection
    grid.undo()
    assert grid.selection == set(column)
    grid.redo()
    assert "A2" in grid.selection
    grid.clear()
    assert grid.selection == set() and grid.can_undo()


def test_plate_grid_click_on_a_header_takes_its_column(app):
    grid = PlateGrid()
    grid.set_ordering(_plate())
    grid.resize(22 + 12 * 30, 22 + 8 * 30)
    cell, left, top = grid._layout()
    _click(grid, QPoint(int(left + cell * 2.5), int(top - 8)))   # column 3
    assert grid.selection == {f"{r}3" for r in "ABCDEFGH"}
    _click(grid, QPoint(int(left + cell * 2.5), int(top + cell * 0.5)))
    assert "A3" not in grid.selection


def test_plate_tools_follow_the_history(app):
    grid = PlateGrid()
    grid.set_ordering(_plate())
    tools = PlateTools(grid)
    assert not tools.undo.isEnabled() and not tools.clear.isEnabled()
    tools.select_all.click()
    assert len(grid.selection) == 96 and tools.undo.isEnabled()
    tools.undo.click()
    assert grid.selection == set() and tools.redo.isEnabled()


def test_mini_plate_reports_a_click_and_chooses_nothing(app):
    grid = PlateGrid(mini=True)
    grid.set_ordering(_plate())
    grid.resize(12 * 10, 8 * 10)
    clicked = []
    grid.well_clicked.connect(clicked.append)
    cell, left, top = grid._layout()
    _click(grid, QPoint(int(left + cell * 0.5), int(top + cell * 0.5)))
    assert clicked == ["A1"] and grid.selection == set()


def test_jog_pad_steps_and_keys(app):
    pad = JogPad(step=1.0)
    assert len(STEPS) == 10 and pad.step == 1
    sizes = []
    pad.step_changed.connect(sizes.append)
    pad.bigger.click()
    assert pad.step == 2 and sizes == [2]
    pad.set_step(10)
    assert not pad.bigger.isEnabled()
    shown = [b.property("mm") for b in pad.options]
    assert 10 in shown
    jogs = []
    pad.jog.connect(lambda axis, sign: jogs.append((axis, sign)))
    pad.buttons["x-"].click()
    pad.buttons["z+"].click()
    assert jogs == [("x", -1), ("z", 1)]
    assert all(not b.keycap for b in pad.buttons.values())
    pad.keys.setChecked(True)
    assert pad.buttons["z+"].keycap == "PgUp"
    pad.set_bindings({"z+": "Home"})
    assert pad.buttons["z+"].keycap == "Home"
    pad.set_enabled_reason(False, "Robot busy.")
    assert not pad.buttons["y+"].isEnabled()


def test_tip_gauge_draws_the_volume(app):
    gauge = TipGauge(capacity_ul=200)
    gauge.set_volume(50)
    assert gauge.volume.text() == "50 µl"
    assert gauge.drawing.fraction == pytest.approx(0.25)
    gauge.set_position((1.0, 2.0, 3.0))
    assert "Z 3.0" in gauge.position.text()


def test_mini_deck_lights_only_valid_slots(app):
    deck = MiniDeckDropdown()
    deck.set_tiles([DeckTile(5, "Corning 384", "plate", "112 µl"),
                    DeckTile(8, "Petri dish", "dish", valid=False)])
    deck.set_slot(5)
    assert deck._slot.text() == "Slot 5" and "112" in deck._facts.text()
    chosen = []
    deck.slot_chosen.connect(chosen.append)
    deck.open_popover()
    tiles = {t.tile.slot: t for t in deck._popover.findChildren(Tile)}
    assert len(tiles) == 12
    assert tiles[5].isEnabled() and not tiles[8].isEnabled()
    assert not tiles[1].isEnabled()
    tiles[5].click()
    assert chosen == [5]


def test_the_deck_looks_the_same_opened_from_the_picture(app):
    """A button over the picture is restyled, and its height was the tiles'."""
    plain = MiniDeckDropdown()
    plain.open_popover()
    tile = plain._popover.findChildren(Tile)[0]
    view = CameraView()
    over = MiniDeckDropdown()
    view.add_top_row_widget(over)
    over.open_popover()
    tiles = over._popover.findChildren(Tile)
    assert {t.size() for t in tiles} == {tile.size()}
    assert tile.height() == 74
    plain._popover.close()
    over._popover.close()
    view.deleteLater()


def test_a_press_on_its_button_is_not_given_back_to_it(app):
    """A press outside a popup goes on to what is under it, and the button
    would open it again; `offscreen` replays nothing, so this says the popup
    was told not to."""
    host = QWidget()
    host.resize(600, 700)
    deck = MiniDeckDropdown(host)
    deck.setGeometry(10, 10, 300, 40)
    host.show()
    deck.open_popover()
    popup = deck._popover
    replay = Qt.WidgetAttribute.WA_NoMouseReplay

    def press(widget):
        at = QPointF(widget.mapToGlobal(widget.rect().center()))
        app.sendEvent(popup, QMouseEvent(
            QEvent.Type.MouseButtonPress, popup.mapFromGlobal(at), at,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))

    other = QWidget(host)
    other.setGeometry(10, 600, 100, 40)
    press(other)
    assert not popup.testAttribute(replay)
    deck.open_popover()
    popup = deck._popover
    press(deck)
    assert popup.testAttribute(replay)
    host.deleteLater()


def test_sheet_done_and_cancel(app):
    host = QWidget()
    host.resize(1200, 800)
    sheet = SheetWindow(host, "Upper camera")
    outcomes = []
    sheet.done.connect(lambda: outcomes.append("done"))
    sheet.canceled.connect(lambda: outcomes.append("cancel"))
    sheet.open()
    assert sheet.isVisibleTo(host) and sheet.geometry() == host.rect()
    sheet.done_button.click()
    assert outcomes == ["done"] and not sheet.isVisibleTo(host)
    sheet.open()
    sheet.cancel_button.click()
    assert outcomes == ["done", "cancel"]


def test_confirm_waits_and_the_safe_answer_is_primary(app):
    host = QWidget()
    host.resize(800, 600)
    dialog = ConfirmDialog(host, "Reset the setup?", safe="Keep setup",
                           destructive="Reset")
    assert dialog.safe.objectName() == "primary"
    assert dialog.destructive.objectName() == "dangerText"
    QTimer.singleShot(0, dialog.destructive.click)
    assert dialog.ask() is True


def test_lock_overlay_covers_and_unlocks(app):
    host = QWidget()
    host.resize(800, 600)
    lock = LockOverlay(host)
    lock.set_locked(True)
    assert lock.isVisibleTo(host)
    asked = []
    lock.unlock_clicked.connect(lambda: asked.append(1))
    lock.unlock.click()
    assert asked == [1]
    lock.set_locked(False)
    assert not lock.isVisibleTo(host)


def test_gallery_builds(app):
    from micropick.gui.gallery import Gallery
    window = Gallery()
    window.resize(1440, 900)
    assert window.top.path == ["Auto pick"]
