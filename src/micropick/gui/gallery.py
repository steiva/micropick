"""Every component of the redesign in its states, on one screen.

    python -m micropick.gui.gallery [--mock]

For looking at the theme and the widgets (REDESIGN §9, phase 1) without a
robot, a profile or a page: nothing here is connected to a session.
`--mock` is accepted, and changes nothing, so the command reads like the
application's own.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                               QScrollArea, QVBoxLayout, QWidget)

from .theme import COLORS, SPACING
from .theme.factory import (caption, card, danger_button, danger_text_button,
                            heading, muted_label, primary_button,
                            quiet_button, secondary_button, select)
from .widgets.banner import Banner
from .widgets.controls import Segmented, Switch
from .widgets.fold_card import FoldCard, FoldGroup
from .widgets.footer import Footer
from .widgets.jog_pad import JogPad
from .widgets.mini_deck import DeckTile, MiniDeckDropdown
from .widgets.overlays import ConfirmDialog, LockOverlay, SheetWindow
from .widgets.plate_grid import PlateGrid, PlateTools
from .widgets.step_bar import StepBar
from .widgets.tip_gauge import TipGauge
from .widgets.toast import show_toast
from .widgets.top_bar import TopBar

__all__ = ["Gallery", "main"]


def _plate(rows: int, columns: int) -> list[list[str]]:
    letters = "ABCDEFGHIJKLMNOP"[:rows]
    return [[f"{r}{c}" for r in letters] for c in range(1, columns + 1)]


def _deck() -> list[DeckTile]:
    return [
        DeckTile(5, "Corning 384 well plate", "plate", "112 µl",
                 "Measured", "ok"),
        DeckTile(6, "Corning 384 well plate", "plate", "80 µl",
                 "Not measured", "warn"),
        DeckTile(8, "Petri dish", "dish", valid=False),
        DeckTile(10, "VWR tips, 200 µl", "tips", valid=False),
        DeckTile(12, kind="trash", valid=False),
    ]


class Gallery(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("micropick: components")
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.top = TopBar(central)
        self.top.set_path(["Auto pick"])
        self.top.set_light(True)
        self.top.set_status("Picking")
        self.top.set_stop_visible(True)
        self.steps = StepBar(["Deck", "Tip", "Plan", "Dish bottom", "Picking"],
                             central)
        self.steps.set_state(2, [True, True, False, False, False])
        self.steps.clicked.connect(
            lambda i: self.steps.set_state(i, [True, True, False, False,
                                               False]))
        outer.addWidget(self.top)
        outer.addWidget(self.steps)

        scroll = QScrollArea(central)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(24, 24, 24, 24)
        grid.setSpacing(SPACING * 2)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.footer = Footer(central)
        self.footer.set_navigation(next_enabled=False)
        outer.addWidget(self.footer)
        self.setCentralWidget(central)

        grid.addWidget(self._buttons(), 0, 0)
        grid.addWidget(self._inputs(), 0, 1)
        grid.addWidget(self._banners(), 1, 0)
        grid.addWidget(self._folds(), 1, 1)
        grid.addWidget(self._plate(), 2, 0, 1, 2)
        grid.addWidget(self._camera_overlays(), 3, 0)
        grid.addWidget(self._covers(), 3, 1)
        grid.addWidget(self._footers(), 4, 0, 1, 2)

        self.lock = LockOverlay(central)
        self.lock.unlock_clicked.connect(lambda: self._set_lock(False))
        self.top.lock_toggled.connect(self._set_lock)
        self.top.park_clicked.connect(self._park)
        self.top.stop_clicked.connect(lambda: show_toast(self, "Stopping."))

    # -- sections ----------------------------------------------------------

    def _section(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        frame = card()
        layout = frame.layout()
        layout.addWidget(heading(title, 3))
        return frame, layout

    def _buttons(self) -> QWidget:
        frame, layout = self._section("Buttons")
        row = QHBoxLayout()
        for button in (primary_button("Start picking"),
                       secondary_button("Back"), quiet_button("Save anyway"),
                       danger_text_button("Reset"), danger_button("Stop run")):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        off = primary_button("Use this plan")
        off.setEnabled(False)
        paused = secondary_button("Resume")
        paused.setProperty("tone", "warn")
        for button in (off, paused):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(caption("Cuboids per well"))
        layout.addWidget(muted_label("A description in the quieter ink."))
        return frame

    def _inputs(self) -> QWidget:
        frame, layout = self._section("Choices")
        segmented = Segmented(["In a well", "At a point", "By hand"])
        layout.addWidget(segmented)
        layout.addWidget(Switch("Snap to found cuboids", checked=True))
        layout.addWidget(Switch("Aspirate as soon as I click"))
        box = select()
        box.addItems(["VWR tips, 200 µl · slot 10", "Opentrons 20 µl · slot 11"])
        layout.addWidget(box)
        deck = MiniDeckDropdown()
        deck.set_tiles(_deck())
        deck.set_slot(5)
        deck.slot_chosen.connect(lambda s: show_toast(self, f"Slot {s}."))
        layout.addWidget(deck)
        return frame

    def _banners(self) -> QWidget:
        frame, layout = self._section("Banners and toast")
        info = Banner("The robot remembers what was on it last time. Check "
                      "that it matches the real deck.", "info")
        info.add_action("Got it")
        warn = Banner("Put a tip on first.", "warn")
        warn.add_action("Go to Tip")
        ok = Banner("Camera calibrated and saved.", "ok")
        for banner in (info, warn, ok):
            layout.addWidget(banner)
        toast = secondary_button("Show a toast")
        toast.clicked.connect(lambda: show_toast(self, "Tip A3 is on."))
        layout.addWidget(toast)
        return frame

    def _folds(self) -> QWidget:
        frame, layout = self._section("Fold cards (one open)")
        sizes = FoldCard("Cuboid sizes", summary="250–500 µm", open=True)
        sizes.body.layout().addWidget(muted_label(
            "Drag the white lines to change which sizes are picked."))
        plate = FoldCard("Plate · slot 5", summary="12 of 24 wells")
        mini = PlateGrid(mini=True)
        mini.set_ordering(_plate(16, 24))
        wells = [w for c in _plate(16, 24) for w in c]
        fills = {w: COLORS["planned"] for w in wells[:24]}
        fills.update({w: COLORS["plate"] for w in wells[:12]})
        mini.set_fills(fills)
        mini.set_current(wells[12])
        mini.setMinimumHeight(150)
        plate.body.layout().addWidget(mini)
        self._folds_group = FoldGroup([sizes, plate])
        layout.addWidget(sizes)
        layout.addWidget(plate)
        return frame

    def _plate(self) -> QWidget:
        frame, layout = self._section("Plate grid")
        grid = PlateGrid()
        grid.set_ordering(_plate(16, 24))
        grid.setMinimumHeight(360)
        tools = PlateTools(grid)
        row = QHBoxLayout()
        count = QLabel("No wells chosen yet.")
        grid.selection_changed.connect(
            lambda names: count.setText(f"{len(names)} wells selected"))
        row.addWidget(count)
        row.addStretch(1)
        row.addWidget(tools)
        layout.addLayout(row)
        layout.addWidget(grid)
        return frame

    def _camera_overlays(self) -> QWidget:
        frame, layout = self._section("Over the picture")
        stage = QWidget()
        stage.setMinimumHeight(330)
        stage.setObjectName("stage")
        stage.setStyleSheet("QWidget#stage { background: #050607; "
                            "border-radius: 10px; }")
        gauge = TipGauge(parent=stage)
        gauge.set_volume(15)
        gauge.set_position((245.1, 132.4, 68.9))
        gauge.move(16, 16)
        pad = JogPad(parent=stage)
        pad.jog.connect(lambda axis, sign: show_toast(
            self, f"Jog {axis} {'+' if sign > 0 else '−'}{pad.step:g} mm"))
        pad.adjustSize()
        stage.resizeEvent = lambda e: pad.move(
            stage.width() - pad.width() - 16, stage.height() - pad.height() - 16)
        layout.addWidget(stage)
        return frame

    def _covers(self) -> QWidget:
        frame, layout = self._section("Sheets and dialogs")
        sheet = secondary_button("Open a sheet")
        sheet.clicked.connect(self._sheet)
        confirm = secondary_button("Reset the setup…")
        confirm.clicked.connect(self._confirm)
        lock = secondary_button("Lock the robot")
        lock.clicked.connect(lambda: self._set_lock(True))
        for button in (sheet, confirm, lock):
            layout.addWidget(button)
        layout.addStretch(1)
        return frame

    def _footers(self) -> QWidget:
        frame, layout = self._section("Run status in the footer")
        states = [("ready", "Ready", "23 cuboids found."),
                  ("busy", "Picking", "Well A13 of 24 · taking a cuboid"),
                  ("warn", "Paused", "Paused after well A12."),
                  ("warn", "Waiting for you",
                   ("No cuboids found after 2 shakes. Add cuboids, or "
                    "finish by hand.")),
                  ("ok", "Done", "24 wells filled."),
                  ("off", "Not ready", "Put a tip on first.")]
        for tone, tag, sentence in states:
            footer = Footer()
            footer.set_status(tag, sentence, tone=tone)
            pause = secondary_button("Resume" if tag == "Paused" else "Pause")
            if tag == "Paused":
                pause.setProperty("tone", "warn")
            stop = danger_button("Stop run")
            start = primary_button("Start picking")
            footer.set_buttons([pause, stop] if tone in ("busy", "warn")
                               else [footer.back, start])
            layout.addWidget(footer)
        return frame

    # -- actions -------------------------------------------------------------

    def _sheet(self) -> None:
        sheet = SheetWindow(self.findChild(QScrollArea), "Upper camera")
        sheet.body.addWidget(heading("Which camera is the upper camera?", 3))
        sheet.body.addWidget(muted_label(
            "The picture on the left is live. Pick the camera whose picture "
            "matches."))
        sheet.body.addStretch(1)
        self.top.set_path(["Robot setup", "Upper camera"])
        sheet.done.connect(lambda: show_toast(self, "Saved."))
        for signal in (sheet.done, sheet.canceled):
            signal.connect(lambda: self.top.set_path(["Auto pick"]))
        sheet.open()

    def _confirm(self) -> None:
        reset = ConfirmDialog.confirm(
            self, "Reset the setup?",
            text="Cameras, calibration, platforms and positions are cleared.",
            safe="Keep setup", destructive="Reset")
        show_toast(self, "Setup reset." if reset else "Setup kept.")

    def _set_lock(self, on: bool) -> None:
        self.top.set_lock(on)
        self.lock.set_locked(on)

    def _park(self) -> None:
        from PySide6.QtCore import QTimer
        self.top.set_parking(True)
        QTimer.singleShot(1500, lambda: self.top.set_parking(False))


def main(argv: list[str] | None = None) -> int:
    from .app import create_app
    argv = list(sys.argv if argv is None else argv)
    app = create_app([a for a in argv if a != "--mock"])
    window = Gallery()
    window.resize(1600, 1000)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
