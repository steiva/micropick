"""The Tip liquid step: Auto pick's fourth, between Dish bottom and Plan.

A little liquid in the tip before a picking run makes the pickups work
better: the run draws and gives back only each cuboid's own volume
(`PickingConfig.vol`), so what is drawn here stays in the tip as a
cushion for the whole run. Optional: Next is never held, and the step is
done while the tip holds something (`Session.in_tip`), so it is undone
by itself when the tip is dropped or changed.

**No camera**, one card: the settings as one line with **Change** - the
volume (`tip_liquid_vol`, 20 µl) and the height above the dish bottom
(`tip_liquid_above_bottom`, 1.5 mm), saved into the picking settings as
they are typed; the speed is the picking settings' flow rate - then
**Aspirate from**:

- **Dish center** (the default, never remembered): the middle of the
  working circle, as the Dish bottom step finds it (`dish_center`), at the
  height above the dish bottom.
- **A well**: a plate on the deck chosen on the mini deck, a well on its
  mini map; the tip goes to the well's bottom as the robot has it, in x
  and y at the plate's measured well center if it has one
  (`well_center.xy`), the drawing's else.

**Aspirate 20 µl** goes there, aspirates at the flow rate and raises the
tip to the travel height, one short job through the page's jog panel
(`JogPanel.run_job`), never shown. Pressed again it adds to what is in the
tip, which "The tip holds 40 µl." says.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...workflows import well_center
from ..labware_kinds import facts, kind
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (caption, card, compact, double_spin_box,
                             heading, muted_label, primary_button,
                             scroll_column, secondary_button)
from ..widgets.controls import Segmented
from ..widgets.jog_panel import JogPanel
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.picking_fields import BOUNDS, STEPS
from ..widgets.plate_grid import PlateGrid
from .dish_bottom import dish_center

__all__ = ["TipLiquidPage", "FROM"]

TITLE = "Tip liquid"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# Aspirate from: the segmented choices.
FROM = ("Dish center", "A well")
DISH, WELL = 0, 1


def _number_box(parent: QWidget, name: str, suffix: str):
    """A picking setting's box, within its bounds and steps."""
    box = double_spin_box(parent)
    box.setRange(*BOUNDS[name])
    box.setDecimals(1)
    box.setSingleStep(STEPS[name])
    box.setSuffix(suffix)
    box.setMinimumWidth(110)
    return box


class TipLiquidPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        # (slot, labware id, definition) of the plate chosen, and its well.
        self._plate = None
        self._well: str | None = None
        self._plates: dict = {}

        # Its keys on itself, hidden: they never fire.
        self.jog = JogPanel(session, machine_controls=False, parent=self)
        self.jog.hide()
        self.jog.position_changed.connect(self._show_said)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.addStretch(1)
        layout.addWidget(scroll_column(self._panel(), PANEL_WIDTH))
        layout.addStretch(1)

        session.robot_state_changed.connect(lambda _s: self._reload_plates())
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.tip_changed.connect(lambda _t: self.refresh())
        session.in_tip_changed.connect(lambda _ul: self.refresh())
        session.profile_changed.connect(lambda _p: self._show_settings())
        self._show_settings()
        self._reload_plates()

    # -- construction --------------------------------------------------------

    def _panel(self) -> QWidget:
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(heading("Fill the tip", 1, panel))

        box = card(panel)
        body = box.layout()
        row = QHBoxLayout()
        self.settings_line = muted_label("", box)
        self.settings_line.setWordWrap(True)
        self.change_button = compact(secondary_button("Change", box))
        self.change_button.setCheckable(True)
        self.change_button.toggled.connect(
            lambda on: self.change_box.setVisible(on))
        row.addWidget(self.settings_line, 1)
        row.addWidget(self.change_button)
        body.addLayout(row)

        self.change_box = QWidget(box)
        lines = QVBoxLayout(self.change_box)
        lines.setContentsMargins(0, 0, 0, 0)
        self.volume = _number_box(self.change_box, "tip_liquid_vol", " µl")
        self.above_bottom = _number_box(self.change_box,
                                        "tip_liquid_above_bottom", " mm")
        self.above_bottom.setToolTip("Above the dish bottom.")
        for label, number in (("Volume", self.volume),
                              ("Height", self.above_bottom)):
            line = QHBoxLayout()
            name = QLabel(label, self.change_box)
            name.setMinimumWidth(90)
            line.addWidget(name)
            line.addWidget(number)
            line.addStretch(1)
            lines.addLayout(line)
            number.editingFinished.connect(self._save_settings)
        self.change_box.setVisible(False)
        body.addWidget(self.change_box)

        body.addWidget(caption("Aspirate from", box))
        self.source = Segmented(list(FROM), current=DISH, parent=box)
        self.source.changed.connect(lambda _i: self._source_changed())
        body.addWidget(self.source)

        self.well_box = QWidget(box)
        wells = QVBoxLayout(self.well_box)
        wells.setContentsMargins(0, 0, 0, 0)
        self.plate_dropdown = MiniDeckDropdown(self.well_box,
                                               placeholder="Choose the plate")
        self.plate_dropdown.slot_chosen.connect(self._plate_chosen)
        wells.addWidget(self.plate_dropdown)
        self.plate_map = PlateGrid(mini=True, parent=self.well_box)
        self.plate_map.well_clicked.connect(self._well_chosen)
        wells.addWidget(self.plate_map)
        self.well_line = muted_label("Click a well.", self.well_box)
        wells.addWidget(self.well_line)
        self.well_box.setVisible(False)
        body.addWidget(self.well_box)

        self.holds = muted_label("", box)
        self.holds.setVisible(False)
        body.addWidget(self.holds)
        self.aspirate_button = primary_button("", box)
        self.aspirate_button.clicked.connect(self._aspirate)
        body.addWidget(self.aspirate_button)
        column.addWidget(box)

        # What the robot answered: a move not possible, a job refused.
        self.said = muted_label("", panel)
        self.said.setWordWrap(True)
        self.said.setAlignment(Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self.said)
        column.addStretch(1)
        return panel

    # -- the settings --------------------------------------------------------

    def _show_settings(self) -> None:
        """The boxes and the line from the picking settings."""
        profile = self.session.profile
        if profile is not None:
            picking = profile.picking
            for number, value in ((self.volume, picking.tip_liquid_vol),
                                  (self.above_bottom,
                                   picking.tip_liquid_above_bottom)):
                number.blockSignals(True)
                number.setValue(float(value))
                number.blockSignals(False)
        self.refresh()

    def _save_settings(self) -> None:
        """The boxes into the picking settings, when they differ."""
        profile = self.session.profile
        if profile is None:
            return
        new = {"tip_liquid_vol": round(self.volume.value(), 1),
               "tip_liquid_above_bottom": round(self.above_bottom.value(), 1)}
        old = {name: getattr(profile.picking, name) for name in new}
        if new == old:
            return
        profile.picking = profile.picking.model_copy(update=new)
        profile.save_picking()
        log.info("picking settings: %s -> %s (Tip liquid)", old, new)
        self.refresh()

    def _settings_text(self) -> str:
        profile = self.session.profile
        if profile is None:
            return "No profile loaded."
        picking = profile.picking
        parts = [f"{picking.tip_liquid_vol:g} µl"]
        if self.source.current() == DISH:
            parts.append(f"{picking.tip_liquid_above_bottom:g} mm above the "
                         f"dish bottom")
        parts.append(f"{picking.flow_rate:g} µl/s")
        return " · ".join(parts)

    # -- the source ----------------------------------------------------------

    def _source_changed(self) -> None:
        self.well_box.setVisible(self.source.current() == WELL)
        self.refresh()

    def _reload_plates(self) -> None:
        """The plates on the deck on the mini deck; the one chosen kept
        while it is there."""
        plates = {int(slot): (int(slot), entry.labware_id, definition)
                  for slot, entry, definition in self.session.plates()
                  if slot.isdigit()}
        self._plates = plates
        self.plate_dropdown.set_tiles([
            DeckTile(slot, name=definition.display_name,
                     kind=kind(definition), facts=facts(definition))
            for slot, (_slot, _id, definition) in plates.items()])
        chosen = self._plate[0] if self._plate is not None else None
        if chosen not in plates:
            self._set_plate(None)
        else:
            self._set_plate(plates[chosen])
        self.refresh()

    def _plate_chosen(self, slot: int) -> None:
        self._set_plate(self._plates.get(slot))
        self.refresh()

    def _set_plate(self, plate) -> None:
        if plate is None or self._plate is None or plate[2] != self._plate[2]:
            self._well = None
        self._plate = plate
        self.plate_dropdown.set_slot(None if plate is None else plate[0])
        self.plate_map.set_ordering(plate[2].ordering if plate else [])
        self.plate_map.set_current(self._well)
        self.plate_map.setVisible(plate is not None)

    def _well_chosen(self, well: str) -> None:
        self._well = well
        self.plate_map.set_current(well)
        self.refresh()

    # -- what the page shows -------------------------------------------------

    def _show_said(self, lines) -> None:
        """Every answer of the panel's, a job's end included: the button
        follows it (the robot's lease is given back before)."""
        self.said.setText("\n".join(lines[1:]))
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        session, profile = self.session, self.session.profile
        self.settings_line.setText(self._settings_text())
        volume = profile.picking.tip_liquid_vol if profile else 0.0
        self.aspirate_button.setText(f"Aspirate {volume:g} µl")
        held = session.in_tip if session.tip.attached is True else 0.0
        self.holds.setVisible(held > 0)
        self.holds.setText(f"The tip holds {held:g} µl.")
        if self._plate is not None:
            self.well_line.setText(
                f"Well {self._well}, slot {self._plate[0]}." if self._well
                else "Click a well.")
        self.well_line.setVisible(self._plate is not None)
        why = self._why_not()
        self.aspirate_button.setEnabled(not why)
        self.aspirate_button.setToolTip(why)

    def _why_not(self) -> str:
        """Why Aspirate cannot be pressed now, or ""."""
        session = self.session
        if session.robot is None:
            return "The robot is not connected."
        if session.robot_lease.busy or self.jog.busy:
            return "Robot busy."
        if session.tip.attached is not True:
            return "No tip is on."
        if self.source.current() == DISH:
            return dish_center(session.profile)[1]
        if self._plate is None:
            return "Choose the plate."
        if self._well is None:
            return "Choose the well."
        return ""

    # -- aspirate ------------------------------------------------------------

    def _aspirate(self) -> None:
        if self._why_not():
            return
        session = self.session
        picking = session.profile.picking
        volume, rate = float(picking.tip_liquid_vol), float(picking.flow_rate)
        motion = session.motion
        if self.source.current() == DISH:
            xy, _why = dish_center(session.profile)
            z = float(picking.dish_bottom) + float(
                picking.tip_liquid_above_bottom)
            refused = motion.refused((*xy, z))
            if refused:
                self.jog.tell(f"Not possible: {refused}.")
                return

            def go(log_):
                motion.tip_to(xy, z, log=log_)
            where = "the dish center"
        else:
            slot, labware_id, definition = self._plate
            well = self._well
            center = session.profile.deck.well_center(slot,
                                                      definition.load_name)
            dx, dy = well_center.xy(center)

            def go(log_):
                motion.to_well(labware_id, well, "bottom", (dx, dy, 0.0),
                               log=log_)
            where = f"{well} of slot {slot}"

        def job(log_):
            go(log_)
            log_(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            motion.raise_tip(log=log_)

        def then(_result):
            return f"aspirated {volume:g} µl from {where}"

        if not self.jog.run_job(job, what="tip liquid", then=then):
            self.jog.tell(session.robot_lease.refusal)
        self.refresh()
