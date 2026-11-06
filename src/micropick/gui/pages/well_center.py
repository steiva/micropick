"""The Well center step (REDESIGN §6.7): Liquid's and Manual pick's
third (#63), Auto pick's fifth (#64), the same page in each.

For each plate on the deck, the real well center and the bottom
(`config.schema.WellCenter`, one per slot and plate type, saved by
`Session.set_well_center`): the robot's idea of a well is the plate's
drawing, and a step near the bottom needs the real one.

On the left, **no camera** (the upper one does not see the tip at a
well): where the tip is, Z in big numbers as on the Dish bottom step, and
the jog pad under it - the face of a jog panel that is never shown, whose
keys go with the pad on screen, as on every page (#57); the operator
watches the robot. On the right "Well center and depth", a
`MiniDeckDropdown` (#130) with every plate on the deck lit, in every task
(`tasks.well_center_plates`), each tile saying Measured or Not measured;
one line for the selected plate - "Measured today", "Not measured: using
the drawing" (`Session.well_center_status`) - then Last time, and last, so
that nothing stands under what folds, one card, no title (the selector
stands over it), no chip (its row says it), holding a path
(`widgets.path_steps`, #127), none with no plate on the deck:

- **Tip to a well:** a well on the plate's map (`WellPicker`), then Move
  the tip - to its top center as the robot has it (`workflows.well_center.go_to_top`), what the
  measurement is taken against (`Measurement`). Done: "Tip is over A1",
  with **Redo**, the one way back: the map again and the measurement
  forgotten, nothing moved. So it is the way to another well.
- **Center the tip:** the arrows until it is in the middle; It is
  centered. Done: "Tip is centered".
- **Lower to the top of the well:** until the tip end is level with the
  top of the plate; It is level. Done: "Tip is level with the top".
- **Well depth:** the drawing's for that well unless the plate's was
  set; **Save** stores where the tip is less the robot's well top, and
  that depth, and goes on to the next plate: never done. The bottom is the
  top less the depth.

A plate opens on the first item, or on Center the tip while a measurement
begun on it is in hand; a new robot session forgets that. The pad works
on every item.

A click on the selector chooses the plate for the robot session
(`Session.chosen_plate`), until that plate is moved or taken off. The page
opens on it, else in Auto pick on the plan's plate, else on the first on
the deck (`_opening`). The plate Save goes on to is shown, not chosen.

The step is optional in every task: a plate not measured uses its drawing.
In Manual pick a measured plate also shows "Last time: Z 66.40 mm · 3
days ago" (`well_center.measured_bottom`) with **Use it**, as Dish bottom
does: Use it, or Save, confirms that plate's bottom for this robot session
(`Session.plate_bottom_confirmed`). A measurement made on another module
height is not the plate's (`DeckConfig.well_center`): not measured.
The step is done while there are plates and every one is measured
(`tasks.step_state`).

A plate a run goes into keeps its center: Save is gray while one does
(`well_center.run_using`). Robot commands go through the jog panel's
`run_job`.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QSpacerItem,
                               QVBoxLayout, QWidget)

from ...workflows import well_center
from .. import labware_kinds, tasks
from ..session import MEASURED, Session, SessionError
from ..theme import SIZES, SPACING, mono_font
from ..theme.factory import (caption, card, double_spin_box, heading,
                             muted_label, primary_button, reserve,
                             scroll_column, secondary_button)
from ..widgets.jog_pad import StepChip
from ..widgets.jog_panel import JogPanel
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.path_steps import PathItem, PathSteps, path_body
from ..widgets.step_card import wrapped
from ..widgets.toast import show_toast
from ..widgets.well_picker import WellPicker
from ..workers import describe_error

__all__ = ["WellCenterPage", "TITLE", "status_text"]

log = logging.getLogger(__name__)

TITLE = "Well center"

PANEL_WIDTH = 440
MAP_PX = 340

INTRO = "A plate not measured uses its drawing."
NO_PLATE = "No plate on the deck."
USING_THE_DRAWING = "Not measured: using the drawing"

# Where the operator is with the selected plate, the path's items: the tip
# to a well, centering it, lowering it, the depth and Save.
TO_WELL, CENTER, LOWER, SAVE = "well", "center", "lower", "depth"


def measured_ago(center) -> str:
    """"today", "yesterday", "3 days ago"; "" with no date."""
    when = center.measured_at if center is not None else None
    if when is None:
        return ""
    # Saved in local time, naive (`Session.set_well_center`): by the
    # calendar here, not by 24 hours.
    if when.tzinfo is not None:
        when = when.astimezone()
    days = (date.today() - when.date()).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def status_text(status: str, center=None) -> str:
    """A plate's line under the selector: "Measured today", "Measured 3
    days ago", "Not measured: using the drawing"."""
    if status == MEASURED:
        return f"Measured {measured_ago(center)}".rstrip()
    return USING_THE_DRAWING


class WellCenterPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._task = "liquid"
        self._measurement = well_center.Measurement()
        self._at = TO_WELL
        # (slot, load name) of the plate selected, and its labware id: a
        # plate taken off and put back has the same key and is another.
        self._selected: tuple[str, str] | None = None
        self._selected_id: str | None = None
        # The plate the depth field was last filled for, so a typed value
        # is not written over by the next refresh.
        self._depth_for = None

        self.jog = JogPanel(session, shortcut_host=self,
                            parent=self)
        self.jog.hide()
        self.jog.pose_changed.connect(self._show_pose)
        # Every job's end: the buttons follow it (the robot's lease is
        # given back before).
        self.jog.position_changed.connect(lambda _lines: self.refresh())

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
        # The hidden panel's own pad: it moves, steps and grays as the
        # panel says, and its keys are on while it is on screen.
        self.pad = self.jog.pad
        self.step_chip = StepChip(self.pad, column)
        lines.addWidget(self.step_chip, 0, Qt.AlignmentFlag.AlignHCenter)
        lines.addSpacing(SPACING * 3)

        lines.addWidget(self.pad, 0, Qt.AlignmentFlag.AlignHCenter)

        lines.addStretch(1)
        return column

    def _panel(self) -> QWidget:
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(heading("Well center and depth", 1, panel))
        self.intro = wrapped(INTRO)
        column.addWidget(self.intro)

        self.plate_choice = MiniDeckDropdown(panel, cut=True,
                                             placeholder="Choose a plate")
        self.plate_choice.slot_chosen.connect(self._plate_clicked)
        column.addWidget(self.plate_choice)
        self.plate_status = muted_label("", panel)
        column.addWidget(self.plate_status)

        self.last_card = card(panel)
        row = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(2)
        self.last_value = heading("", 3, panel)
        self.last_when = muted_label("", panel)
        words.addWidget(self.last_value)
        words.addWidget(self.last_when)
        row.addLayout(words, 1)
        self.use_button = secondary_button("Use it", panel)
        self.use_button.clicked.connect(self._use_last)
        row.addWidget(self.use_button)
        self.last_card.layout().addLayout(row)
        reserve(self.last_card)
        column.addWidget(self.last_card)

        column.addWidget(self._path_card(panel))
        # What the map leaves as it folds: the operator may have scrolled
        # down to Move the tip, and a column that shrinks at the job's end
        # brings everything in it down.
        self.map_room = QSpacerItem(0, 0)
        column.addItem(self.map_room)
        column.addStretch(1)
        return panel

    def _path_card(self, panel: QWidget) -> QWidget:
        """The selected plate's path, the last thing in the column: a body
        folding as a job ends moves nothing."""
        self.path_card = card(panel)

        self.wells = WellPicker("accent", MAP_PX, panel, plate_choice=False)
        self.wells.changed.connect(self._well_chosen)
        self.move_button = primary_button("Move the tip", panel)
        self.move_button.clicked.connect(self._go_to_well_top)
        self.centered_button = primary_button("It is centered", panel)
        self.centered_button.clicked.connect(lambda: self._to(LOWER))
        self.level_button = primary_button("It is level", panel)
        self.level_button.clicked.connect(lambda: self._to(SAVE))

        depth_row = QWidget(panel)
        depth_row.setObjectName("plain")
        row = QHBoxLayout(depth_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.well_depth = double_spin_box(panel)
        self.well_depth.setRange(0.1, 200.0)
        self.well_depth.setDecimals(2)
        self.well_depth.setSingleStep(0.1)
        self.well_depth.setSuffix(" mm")
        row.addWidget(self.well_depth, 1)
        self.save_button = primary_button("Save", panel)
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        self.depth_source = caption("", panel)

        # A body folds as its button is pressed: the keyboard stays the
        # robot's.
        for button in (self.move_button, self.centered_button,
                       self.level_button, self.save_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.path = PathSteps([
            PathItem(TO_WELL, "Tip to a well", "",
                     path_body("", self.wells, self.move_button),
                     link_text="Redo"),
            PathItem(CENTER, "Center the tip", "Tip is centered",
                     path_body("Use the arrows until the tip is in the middle "
                               "of the well.", self.centered_button)),
            PathItem(LOWER, "Lower to the top of the well",
                     "Tip is level with the top",
                     path_body("Lower until the tip end is level with the top "
                               "of the plate.", self.level_button)),
            PathItem(SAVE, "Well depth", "Well depth",
                     path_body("", depth_row, self.depth_source)),
        ], self.path_card)
        self.path.link_clicked.connect(lambda _key: self._redo())
        self.path_card.layout().addWidget(self.path)
        return self.path_card

    # -- the plates ----------------------------------------------------------

    def set_task(self, task: str) -> None:
        """The task the step is shown in: the plate it opens on."""
        self._task = task
        self._list_plates()
        self.select_plate(*self._opening())

    def _plates(self):
        return tasks.well_center_plates(self.session)

    def _keys(self) -> list[tuple[str, str]]:
        return [(slot, entry.load_name) for slot, entry, _d in self._plates()]

    def _list_plates(self) -> None:
        """After a change to the deck: the selection is kept while its
        plate is still there, else the page opens again."""
        if self._labware_id() != self._selected_id or self._selected is None:
            self._selected = None
            self.select_plate(*self._opening())
            if self._selected is None:
                self._fill_wells()
        # Also when nothing was selected and nothing is: the deck emptied.
        self.refresh()

    def _tiles(self) -> list[DeckTile]:
        """The whole deck, its plates lit and saying how they stand."""
        lit = {int(key[0]): key for key in self._keys()}
        tiles = []
        for tile in labware_kinds.deck_tiles(self.session):
            key = lit.get(tile.slot)
            if key is None:
                tiles.append(replace(tile, valid=False))
            elif self.session.well_center_status(*key) == MEASURED:
                tiles.append(replace(tile, note="Measured", note_tone="ok"))
            else:
                tiles.append(replace(tile, note="Not measured"))
        return tiles

    def _opening(self) -> tuple:
        """The plate chosen; else in Auto pick the plan's; else the first
        on the deck; else (None, None)."""
        keys = self._keys()
        chosen = self.session.chosen_plate
        if chosen is not None and (chosen.slot, chosen.load_name) in keys:
            return chosen.slot, chosen.load_name
        if self._task == "auto":
            plan = well_center.plan_plate(self.session.routine,
                                          self.session.run_state)
            if plan in keys:
                return plan
        return (keys or [(None, None)])[0]

    def _first_to_do(self) -> tuple:
        """The first plate not measured; else the first; else (None,
        None)."""
        keys = self._keys()
        todo = [k for k in keys
                if self.session.well_center_status(*k) != MEASURED]
        return (todo or keys or [(None, None)])[0]

    def _plate_clicked(self, slot: int) -> None:
        try:
            self.session.choose_plate(slot)
        except SessionError as exc:
            self.jog.refuse(describe_error(exc))
            self.refresh()
            return
        plate = self.session.chosen_plate
        self.select_plate(plate.slot, plate.load_name)

    def select_plate(self, slot, load_name) -> None:
        key = None if slot is None else (str(slot), load_name)
        if key == self._selected:
            return
        self._selected = key
        self._selected_id = self._labware_id()
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
        """The selected plate on the map, on the well of the measurement in
        hand, else the well last measured, else its first."""
        plate = next(((s, e, d) for s, e, d in self._plates()
                      if (s, e.load_name) == self._selected), None)
        center = self._center()
        self.wells.blockSignals(True)
        self.wells.set_plates([] if plate is None else [plate])
        if plate is not None:
            nominal = self._measurement.for_plate(*self._selected)
            self.wells.choose(plate[0], nominal.well if nominal is not None
                              else center.well if center is not None
                              and center.well in plate[2].wells
                              else plate[2].ordering[0][0])
        self.wells.blockSignals(False)

    def _well(self) -> str:
        chosen = self.wells.chosen()
        return "" if chosen is None else chosen[3]

    def _well_chosen(self) -> None:
        # Another well, on the first item only: the depth follows its
        # drawing.
        self._depth_for = None
        self.refresh()

    # -- the steps -----------------------------------------------------------

    def _to(self, at: str) -> None:
        self._at = at
        self.refresh()

    def _redo(self) -> None:
        """Back to the map, the measurement begun forgotten; nothing moves."""
        self._measurement.forget()
        self._at = TO_WELL
        self._depth_for = None
        self.refresh()

    def _go_to_well_top(self) -> None:
        well, labware_id = self._well(), self._labware_id()
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
                # A click on the map during the move: the tip is over this.
                self._fill_wells()
            else:
                # The one measurement in hand is now that plate's.
                self._at = TO_WELL
            self.refresh()
            return ""

        log.info("well center: to the top of %s in slot %s", well, slot)
        if not self.jog.run_job(job, what="tip to a well", then=reached):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _definition_depth(self) -> float | None:
        definition = self._definition()
        well = self._well()
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
            self.jog.refuse(describe_error(exc))
            return
        self._measurement.forget()
        self._at = TO_WELL
        self._depth_for = None
        show_toast(self, "Well center saved.")
        self.select_plate(*self._first_to_do())
        self.refresh()

    def _use_last(self) -> None:
        if self._selected is None:
            return
        try:
            self.session.use_plate_bottom(*self._selected)
        except SessionError as exc:
            self.jog.refuse(describe_error(exc))
            return
        show_toast(self, "Using the last measurement.")

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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The robot may have moved, and the deck changed, elsewhere.
        self.jog.refresh_position()
        self._list_plates()

    def refresh(self) -> None:
        session = self.session
        self.plate_choice.set_tiles(self._tiles())
        self.plate_choice.set_slot(None if self._selected is None
                                   else int(self._selected[0]))
        # First: a field grayed with the keyboard passes it on, folded not.
        self._show_path()
        chosen = self._selected is not None
        movable = (chosen and session.robot is not None
                   and not session.robot_lease.busy and not self.jog.busy)
        running = self._run_using()
        self.move_button.setEnabled(movable and bool(self._well()))
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
        self.plate_status.setText(status_text(status, self._center())
                                  if chosen else NO_PLATE)
        self._show_last()
        self._show_depth()

    def _show_path(self) -> None:
        """The selected plate's card; none with no plate."""
        chosen = self._selected is not None
        self.path_card.setVisible(chosen)
        room = (max(0, self.path.body_height(TO_WELL)
                    - self.path.body_height(self._at)) if chosen else 0)
        if room != self.map_room.sizeHint().height():
            self.map_room.changeSize(0, room, QSizePolicy.Policy.Fixed,
                                     QSizePolicy.Policy.Fixed)
            self.path_card.parentWidget().layout().invalidate()
        if not chosen:
            return
        self.path.set_done_title(TO_WELL, f"Tip is over {self._well()}")
        self.path.set_current(self._at)

    def _show_last(self) -> None:
        """The selected plate's measured bottom with Use it, in Manual pick
        only."""
        center, definition = self._center(), self._definition()
        shown = (self._task == "manual" and center is not None
                 and definition is not None)
        self.last_card.setVisible(shown)
        if not shown:
            return
        bottom = well_center.measured_bottom(
            center, definition,
            self.session.profile.deck.module_height(center.slot))
        self.last_value.setText(f"Last time: Z {bottom:.2f} mm")
        self.last_when.setText(measured_ago(center))
        self.use_button.setEnabled(
            not self.session.plate_bottom_confirmed(*self._selected))

    def _show_depth(self) -> None:
        """The plate's depth, else the drawing's for the chosen well. At
        Save, what was typed stays."""
        key = (self._selected, self._well())
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
