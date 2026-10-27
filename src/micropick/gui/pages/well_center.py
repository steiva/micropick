"""The Well center step (REDESIGN §6.7): Liquid's third.

For each plate on the deck, the real well center and the bottom
(`config.schema.WellCenter`, one per slot and plate type, saved by
`Session.set_well_center`): the robot's idea of a well is the plate's
drawing, and a step near the bottom needs the real one.

On the left, **no camera** (the upper one does not see the tip at a
well): where the tip is, Z in big numbers as on the Dish bottom step, and
the jog pad under it - the face of a jog panel that is never shown, so it
binds no keys and its Keys toggle is hidden; the operator watches the
robot. On the right "Well center and depth", the plates on the
deck - each "Measured today", "Not measured: using the drawing" (skipped,
a ✓ in yellow) or "Not measured yet" (`Session.well_center_status`) - and,
for the one selected, four steps:

1. **Tip to a well:** a well, then Move the tip - to its top center as
   the robot has it (`workflows.well_center.go_to_top`), what the
   measurement is taken against (`Measurement`).
2. **Center the tip:** the arrows until it is in the middle; It is
   centered.
3. **Lower to the top of the well:** until the tip end is level with the
   top of the plate; It is level.
4. **Well depth:** the drawing's for that well unless the plate's was
   set; **Save** stores where the tip is less the robot's well top, and
   that depth. The bottom is the top less the depth.

**Skip this plate, use the drawing** marks it skipped for this robot
session (`Session.skip_plate`). The step is done when every plate is
measured or skipped (`tasks.step_state`).

A plate a run goes into keeps its center: Save is gray while one does
(`well_center.run_using`). Robot commands go through the jog panel's
`run_job`.
"""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ...workflows import well_center
from ..session import MEASURED, SKIPPED, Session, SessionError
from ..theme import COLORS, SIZES, SPACING, mono_font
from ..theme.factory import (caption, double_spin_box, heading, muted_label,
                             primary_button, quiet_button, scroll_column,
                             select)
from ..theme.icons import pixmap
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.jog_panel import JogPanel
from ..widgets.step_card import StepCard, wrapped
from ..widgets.toast import show_toast
from ..workers import describe_error

__all__ = ["PlateRow", "WellCenterPage", "TITLE", "status_text"]

log = logging.getLogger(__name__)

TITLE = "Well center"

PANEL_WIDTH = 440
MARK_PX = 24

INTRO = ("Measure every plate you will use. A plate you skip still works, "
         "with the depth from its drawing.")
NOT_MEASURED_YET = "Not measured yet"
USING_THE_DRAWING = "Not measured: using the drawing"

# Where the operator is with the selected plate: the tip to a well,
# centering it, lowering it, the depth and Save.
TO_WELL, CENTER, LOWER, SAVE = 0, 1, 2, 3


def status_text(status: str, center=None) -> str:
    """A plate's line in the list: "Measured today", "Measured 3 days
    ago", "Not measured: using the drawing", "Not measured yet"."""
    if status == MEASURED:
        when = center.measured_at if center is not None else None
        if when is None:
            return "Measured"
        # Saved in local time, naive (`Session.set_well_center`): by the
        # calendar here, not by 24 hours.
        if when.tzinfo is not None:
            when = when.astimezone()
        days = (date.today() - when.date()).days
        if days <= 0:
            return "Measured today"
        return ("Measured yesterday" if days == 1
                else f"Measured {days} days ago")
    return USING_THE_DRAWING if status == SKIPPED else NOT_MEASURED_YET


class PlateRow(QFrame):
    """A plate on the deck: a mark, its name and its status; the whole row
    is clicked, the selected one highlighted."""

    clicked = Signal()

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("plateRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status = None
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(12)
        self.mark = QLabel(self)
        self.mark.setFixedSize(MARK_PX, MARK_PX)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.mark)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.name = QLabel(name, self)
        self.name.setObjectName("setupLabel")
        self.name.setWordWrap(True)
        self.value = QLabel(self)
        words.addWidget(self.name)
        words.addWidget(self.value)
        row.addLayout(words, 1)

    def show_status(self, status: str, text: str) -> None:
        self.status = status
        self.value.setText(text)
        if status == MEASURED:
            self.mark.setPixmap(pixmap("check", COLORS["ok"], 14))
            self.mark.setStyleSheet(f"background: {COLORS['ok-bg']}; "
                                    f"border-radius: {MARK_PX // 2}px;")
            self.value.setStyleSheet(f"color: {COLORS['text-2']};")
        elif status == SKIPPED:
            self.mark.setPixmap(pixmap("check", COLORS["warn"], 14))
            self.mark.setStyleSheet(f"background: {COLORS['warn-bg']}; "
                                    f"border-radius: {MARK_PX // 2}px;")
            self.value.setStyleSheet(f"color: {COLORS['warn-text']};")
        else:
            self.mark.setPixmap(pixmap("ring", COLORS["faint"], 18))
            self.mark.setStyleSheet("")
            self.value.setStyleSheet(f"color: {COLORS['text-2']};")

    def set_selected(self, selected: bool) -> None:
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class WellCenterPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._measurement = well_center.Measurement()
        self._at = TO_WELL
        # (slot, load name) of the plate selected, and the rows shown.
        self._selected: tuple[str, str] | None = None
        self._rows: dict[tuple[str, str], PlateRow] = {}
        # The plate the depth field was last filled for, so a typed value
        # is not written over by the next refresh.
        self._depth_for = None

        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False, parent=self)
        self.jog.hide()
        self.jog.pose_changed.connect(self._show_pose)
        self.jog.position_changed.connect(self._show_said)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        layout.addWidget(self._tip_column(), 1)
        layout.addWidget(scroll_column(self._panel(), PANEL_WIDTH))

        session.labware_changed.connect(lambda _s: self._list_plates())
        session.robot_state_changed.connect(self._on_robot)
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.profile_changed.connect(lambda _p: self.refresh())
        session.confirmations_changed.connect(self.refresh)
        self._list_plates()

    # -- construction --------------------------------------------------------

    def _tip_column(self) -> QWidget:
        column = QWidget(self)
        lines = QVBoxLayout(column)
        lines.setSpacing(SPACING)
        lines.addStretch(1)
        self.xy_readout = muted_label("X —   Y —", column)
        self.xy_readout.setFont(mono_font(SIZES["body"]))
        self.z_readout = QLabel("Z —", column)
        self.z_readout.setFont(mono_font(SIZES["readout"], 500))
        for widget in (caption("Tip", column), self.xy_readout,
                       self.z_readout, muted_label("mm", column)):
            lines.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        lines.addSpacing(SPACING * 3)

        # The hidden panel's own pad: it moves, steps and grays as the
        # panel says. Its keys are not bound while the panel is hidden.
        self.pad = self.jog.pad
        self.pad.keys.hide()
        lines.addWidget(self.pad, 0, Qt.AlignmentFlag.AlignHCenter)

        # What the robot answered: a step refused, a move not possible.
        self.said = muted_label("", column)
        self.said.setWordWrap(True)
        self.said.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lines.addWidget(self.said)
        lines.addStretch(1)
        return column

    def _panel(self) -> QWidget:
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(heading("Well center and depth", 1, panel))
        column.addWidget(wrapped(INTRO))

        self.plate_list = QVBoxLayout()
        self.plate_list.setSpacing(2)
        column.addLayout(self.plate_list)
        self.no_plates = muted_label("No plate on the deck.", panel)
        column.addWidget(self.no_plates)

        self.well_step = StepCard(panel, 1, "Tip to a well")
        row = QHBoxLayout()
        self.well_choice = select(panel)
        self.well_choice.currentTextChanged.connect(self._well_chosen)
        row.addWidget(self.well_choice, 1)
        self.move_button = primary_button("Move the tip", panel)
        self.move_button.clicked.connect(self._go_to_well_top)
        row.addWidget(self.move_button)
        self.well_step.add_layout(row)
        column.addWidget(self.well_step.box)

        self.center_step = StepCard(panel, 2, "Center the tip")
        self.center_step.add(wrapped(
            "Use the arrows until the tip is in the middle of the well."))
        self.centered_button = primary_button("It is centered", panel)
        self.centered_button.clicked.connect(lambda: self._to(LOWER))
        self.center_step.add(self.centered_button)
        column.addWidget(self.center_step.box)

        self.lower_step = StepCard(panel, 3, "Lower to the top of the well")
        self.lower_step.add(wrapped(
            "Lower until the tip end is level with the top of the plate."))
        self.level_button = primary_button("It is level", panel)
        self.level_button.clicked.connect(lambda: self._to(SAVE))
        self.lower_step.add(self.level_button)
        column.addWidget(self.lower_step.box)

        self.depth_step = StepCard(panel, 4, "Well depth")
        row = QHBoxLayout()
        self.well_depth = double_spin_box(panel)
        self.well_depth.setRange(0.1, 200.0)
        self.well_depth.setDecimals(2)
        self.well_depth.setSingleStep(0.1)
        self.well_depth.setSuffix(" mm")
        row.addWidget(self.well_depth, 1)
        self.save_button = primary_button("Save", panel)
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        self.depth_step.add_layout(row)
        self.depth_source = caption("", panel)
        self.depth_step.add(self.depth_source)
        column.addWidget(self.depth_step.box)

        self.skip_button = quiet_button("Skip this plate, use the drawing",
                                        panel)
        self.skip_button.clicked.connect(self._skip)
        column.addWidget(self.skip_button)
        column.addStretch(1)
        return panel

    # -- the plates ----------------------------------------------------------

    def _plates(self):
        return self.session.plates()

    def _list_plates(self) -> None:
        """The rows again, after a change to the deck; the selection is
        kept while its plate is still there, else the first not done."""
        while self.plate_list.count():
            widget = self.plate_list.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = {}
        for slot, entry, definition in self._plates():
            key = (slot, entry.load_name)
            row = PlateRow(f"Slot {slot} · {definition.display_name}", self)
            row.clicked.connect(lambda k=key: self.select_plate(*k))
            self.plate_list.addWidget(row)
            self._rows[key] = row
        if self._selected not in self._rows:
            self._selected = None
            self.select_plate(*self._first_to_do())
        else:
            self.refresh()

    def _first_to_do(self) -> tuple:
        """The first plate not measured or skipped; else the first; else
        (None, None)."""
        keys = list(self._rows)
        todo = [k for k in keys
                if self.session.well_center_status(*k) not in (MEASURED,
                                                               SKIPPED)]
        return (todo or keys or [(None, None)])[0]

    def select_plate(self, slot, load_name) -> None:
        key = None if slot is None else (str(slot), load_name)
        if key == self._selected:
            return
        self._selected = key
        # The steps start again unless the measurement in hand is this
        # plate's.
        self._at = (CENTER if key is not None
                    and self._measurement.for_plate(*key) is not None
                    else TO_WELL)
        self._depth_for = None
        self._fill_wells()
        self.refresh()

    def _definition(self):
        return next((d for s, e, d in self._plates()
                     if (s, e.load_name) == self._selected), None)

    def _labware_id(self) -> str | None:
        return next((e.labware_id for s, e, _d in self._plates()
                     if (s, e.load_name) == self._selected), None)

    def _center(self):
        profile = self.session.profile
        if profile is None or self._selected is None:
            return None
        return profile.deck.well_center(*self._selected)

    def _fill_wells(self) -> None:
        definition = self._definition()
        wells = ([w for column in definition.ordering for w in column]
                 if definition is not None else [])
        center = self._center()
        self.well_choice.blockSignals(True)
        self.well_choice.clear()
        self.well_choice.addItems(wells)
        if center is not None and center.well in wells:
            self.well_choice.setCurrentText(center.well)
        self.well_choice.blockSignals(False)

    def _well_chosen(self, _well: str) -> None:
        # Another well: the depth follows its drawing, and the tip is not
        # over it.
        self._depth_for = None
        if self._at != TO_WELL:
            self._at = TO_WELL
            self._measurement.forget()
        self.refresh()

    # -- the steps -----------------------------------------------------------

    def _to(self, at: int) -> None:
        self._at = at
        self.refresh()

    def _go_to_well_top(self) -> None:
        well, labware_id = self.well_choice.currentText(), self._labware_id()
        if (self._selected is None or not well or labware_id is None
                or self.jog.busy):
            return
        slot, load_name = self._selected
        motion = self.session.motion

        def job(log_):
            return well_center.go_to_top(motion, labware_id, well, log=log_)

        def reached(pose):
            # On the GUI thread, once the job has ended.
            self._measurement.started(slot, load_name, well, pose)
            if self._selected == (slot, load_name):
                self._at = CENTER
            self.refresh()
            return ""

        log.info("well center: to the top of %s in slot %s", well, slot)
        if not self.jog.run_job(job, what="tip to a well", then=reached):
            self.jog.tell(self.session.robot_lease.refusal)

    def _definition_depth(self) -> float | None:
        definition = self._definition()
        well = self.well_choice.currentText()
        return (well_center.definition_depth(definition, well)
                if definition is not None and well else None)

    def _save(self) -> None:
        """Where the tip is, less the robot's well top, and the depth."""
        if self._selected is None or self._at != SAVE:
            return
        nominal = self._measurement.for_plate(*self._selected)
        pose = self.jog.pose
        if (nominal is None or pose is None or self.jog.busy
                or self._run_using() is not None):
            return
        depth = round(float(self.well_depth.value()), 3)
        offset = self._measurement.offset(pose)
        try:
            self.session.set_well_center(nominal.slot, nominal.load_name,
                                         offset, nominal.well,
                                         depth_mm=depth)
            # A depth set before is kept by a new measurement; the one in
            # the field is what the operator saves.
            self.session.set_well_depth(nominal.slot, nominal.load_name,
                                        depth)
        except SessionError as exc:
            self.jog.tell(describe_error(exc))
            return
        self._measurement.forget()
        self._at = TO_WELL
        self._depth_for = None
        show_toast(self, "Well center saved.")
        self.select_plate(*self._first_to_do())
        self.refresh()

    def _skip(self) -> None:
        if self._selected is None:
            return
        try:
            self.session.skip_plate(*self._selected)
        except SessionError as exc:
            self.jog.tell(describe_error(exc))
            return
        show_toast(self, "Using the drawing.")
        self.select_plate(*self._first_to_do())

    def _run_using(self) -> str | None:
        if self._selected is None:
            return None
        return well_center.run_using(self.session.robot_lease,
                                     *self._selected)

    # -- what the page shows -------------------------------------------------

    def _on_robot(self, _state) -> None:
        # Another connection or robot session: a well top reached in the
        # last one is not this one's to finish.
        self._measurement.forget()
        self._at = TO_WELL
        self._list_plates()

    def _show_pose(self, pose) -> None:
        if pose is not None:
            self.xy_readout.setText(f"X {pose[0]:.2f}   Y {pose[1]:.2f}")
            self.z_readout.setText(f"Z {pose[2]:.2f}")
        elif self.session.robot is None:
            self.xy_readout.setText("X —   Y —")
            self.z_readout.setText("Z —")
        self.refresh()

    def _show_said(self, lines) -> None:
        """Every answer of the panel's, a job's end included: the buttons
        follow it (the robot's lease is given back before)."""
        self.said.setText("\n".join(lines[1:]))
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The robot may have moved, and the deck changed, elsewhere.
        self.jog.refresh_position()
        self._list_plates()

    def refresh(self) -> None:
        session = self.session
        for key, row in self._rows.items():
            status = session.well_center_status(*key)
            center = (session.profile.deck.well_center(*key)
                      if session.profile is not None else None)
            row.show_status(status, status_text(status, center))
            row.set_selected(key == self._selected)
        self.no_plates.setVisible(not self._rows)

        chosen = self._selected is not None
        movable = (chosen and session.robot is not None
                   and not session.robot_lease.busy and not self.jog.busy)
        running = self._run_using()
        self.well_choice.setEnabled(chosen)
        self.move_button.setEnabled(movable and bool(
            self.well_choice.currentText()))
        self.centered_button.setEnabled(chosen and self._at == CENTER)
        self.level_button.setEnabled(chosen and self._at == LOWER)
        self.well_depth.setEnabled(chosen and self._at == SAVE)
        self.save_button.setEnabled(
            movable and self._at == SAVE and self.jog.pose is not None
            and running is None)
        self.save_button.setToolTip(
            "" if running is None else well_center.in_use_tip(running))
        status = (session.well_center_status(*self._selected) if chosen
                  else None)
        self.skip_button.setEnabled(chosen and status != MEASURED
                                    and status != SKIPPED)
        for number, step in enumerate((self.well_step, self.center_step,
                                       self.lower_step, self.depth_step)):
            step.set_state(OK if number < self._at else
                           TODO if number == self._at and chosen else
                           BLOCKED)
        self._show_depth()

    def _show_depth(self) -> None:
        """The plate's depth, else the drawing's for the chosen well. At
        Save, what was typed stays."""
        key = (self._selected, self.well_choice.currentText())
        if (self._at == SAVE and key == self._depth_for
                or self.well_depth.hasFocus()):
            return
        self._depth_for = key
        center, drawing = self._center(), self._definition_depth()
        if center is not None and center.depth_mm is not None:
            depth, source = center.depth_mm, "Set for this plate."
        elif drawing is not None:
            depth, source = drawing, (f"{drawing:.2f} mm, from the plate's "
                                      f"drawing.")
        else:
            depth, source = None, ""
        if depth is not None:
            self.well_depth.blockSignals(True)
            self.well_depth.setValue(depth)
            self.well_depth.blockSignals(False)
        self.depth_source.setText(source)
