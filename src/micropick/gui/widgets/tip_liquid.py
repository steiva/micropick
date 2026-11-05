"""Liquid in tip: the Picking page's card for liquid in the tip by hand.

A little liquid in the tip before a picking run makes the pickups work
better: the run draws and gives back only each cuboid's own volume
(`PickingConfig.vol`), so what is drawn here stays in the tip as a
cushion for the whole run. It was a step of Auto pick of its own; it is
done with the dish in place, just before Start, so it is on the Picking
page, a card folded to its title and what the tip holds.

The settings as one line with **Change** - the volume (`tip_liquid_vol`,
20 µl) and the height above the dish bottom (`tip_liquid_above_bottom`,
1.5 mm); the speed is the picking settings' flow rate. Change opens the
two boxes with **Save** (Enter in a box) and **Cancel**: nothing typed is
saved before Save, so a slip of the keyboard is seen first. Then **From**:

- **Dish center** (the default, never remembered): the middle of the
  working circle, as the Dish bottom step finds it (`dish_center`), at the
  height above the dish bottom.
- **A well**: a plate on the deck chosen on the mini deck, a well on its
  mini map; the tip goes to the well's bottom as the robot has it, in x
  and y at the plate's measured well center if it has one
  (`well_center.xy`), the drawing's else.

**Aspirate 20 µl** goes there, aspirates at the flow rate and raises the
tip to the travel height; pressed again it adds to what is in the tip,
which "The tip holds 40 µl." says. **Dispense 40 µl** gives all of it
back there: liquid drawn by mistake, or the tip rinsed - draw, give back.
Each is one short job through the page's jog panel (`JogPanel.run_job`).

During a run they work only while it is paused or waits with the tip
empty of a cuboid, under the run's lease lent to the panel, as Camera to
dish center does: the page says so through `run_hold`.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...workflows import well_center
from ..labware_kinds import facts, kind
from ..pages.dish_bottom import dish_center
from ..session import Session
from ..theme.factory import (caption, compact, double_spin_box,
                             laid_out_at_once, muted_label,
                             primary_button, reserve, secondary_button)
from .controls import Segmented
from .jog_panel import JogPanel
from .mini_deck import DeckTile, MiniDeckDropdown
from .picking_fields import BOUNDS, STEPS
from .plate_grid import PlateGrid

__all__ = ["TipLiquidBox", "FROM", "RunHold"]

log = logging.getLogger(__name__)

# From: the segmented choices.
FROM = ("Dish center", "A well")
DISH, WELL = 0, 1


class RunHold:
    """What a run lets the card do, from the page that runs it: `why()`
    is None with no run, "" while the card may use the robot under the
    run's lease, else why not; `borrow()` lends that lease to the panel
    for one job, `give_back()` ends a loan that started nothing."""

    def __init__(self, why: Callable[[], str | None],
                 borrow: Callable[[], None], give_back: Callable[[], None]):
        self.why, self.borrow, self.give_back = why, borrow, give_back


def _number_box(parent: QWidget, name: str, suffix: str):
    """A picking setting's box, within its bounds and steps."""
    box = double_spin_box(parent)
    box.setRange(*BOUNDS[name])
    box.setDecimals(1)
    box.setSingleStep(STEPS[name])
    box.setSuffix(suffix)
    box.setMinimumWidth(110)
    return box


class TipLiquidBox(QWidget):
    def __init__(self, session: Session, jog: JogPanel, *,
                 run_hold: RunHold | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.jog = jog
        self.run_hold = run_hold
        # (slot, labware id, definition) of the plate chosen, and its well.
        self._plate = None
        self._well: str | None = None
        self._plates: dict = {}
        # Every job's end: the buttons follow it (the robot's lease is
        # given back before).
        jog.position_changed.connect(lambda _lines: self.refresh())
        self._build()

        session.robot_state_changed.connect(lambda _s: self._reload_plates())
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.tip_changed.connect(lambda _t: self.refresh())
        session.in_tip_changed.connect(lambda _ul: self.refresh())
        session.profile_changed.connect(lambda _p: self._show_settings())
        self._show_settings()
        self._reload_plates()

    # -- construction --------------------------------------------------------

    def _build(self) -> None:
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.settings_line = muted_label("", self)
        self.settings_line.setWordWrap(True)
        self.change_button = compact(secondary_button("Change", self))
        self.change_button.clicked.connect(self._open_change)
        row.addWidget(self.settings_line, 1)
        row.addWidget(self.change_button)
        body.addLayout(row)

        self.change_box = QWidget(self)
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
            number.lineEdit().returnPressed.connect(self.save_change)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = compact(secondary_button("Cancel",
                                                      self.change_box))
        self.cancel_button.clicked.connect(self.cancel_change)
        self.save_button = compact(primary_button("Save", self.change_box))
        self.save_button.clicked.connect(self.save_change)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        lines.addLayout(buttons)
        self.change_box.setVisible(False)
        body.addWidget(self.change_box)

        body.addWidget(caption("From", self))
        self.source = Segmented(list(FROM), current=DISH, parent=self)
        self.source.changed.connect(lambda _i: self._source_changed())
        body.addWidget(self.source)

        self.well_box = QWidget(self)
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

        self.holds = muted_label("", self)
        self.holds.setVisible(False)
        reserve(self.holds)
        body.addWidget(self.holds)
        buttons = QHBoxLayout()
        self.aspirate_button = primary_button("", self)
        self.aspirate_button.clicked.connect(self._aspirate)
        self.dispense_button = secondary_button("", self)
        self.dispense_button.clicked.connect(self._dispense)
        buttons.addWidget(self.aspirate_button, 1)
        buttons.addWidget(self.dispense_button, 1)
        body.addLayout(buttons)

        # What the robot answered: a move not possible, a job refused.

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

    def _open_change(self) -> None:
        self._show_settings()
        self.change_box.setVisible(True)
        self.change_button.setEnabled(False)

    def _close_change(self) -> None:
        self.change_box.setVisible(False)
        self.change_button.setEnabled(True)

    def cancel_change(self) -> None:
        """What was typed is dropped."""
        self._show_settings()
        self._close_change()

    @property
    def changing(self) -> bool:
        """The boxes are open with a value that differs from what is
        saved."""
        profile = self.session.profile
        if profile is None or self.change_box.isHidden():
            return False
        return self._typed() != {
            name: getattr(profile.picking, name) for name in self._typed()}

    def _typed(self) -> dict[str, float]:
        return {"tip_liquid_vol": round(self.volume.value(), 1),
                "tip_liquid_above_bottom": round(self.above_bottom.value(), 1)}

    def save_change(self) -> None:
        """The boxes into the picking settings, when they differ."""
        profile = self.session.profile
        if profile is None:
            return
        new = self._typed()
        old = {name: getattr(profile.picking, name) for name in new}
        if new != old:
            profile.picking = profile.picking.model_copy(update=new)
            profile.save_picking()
            log.info("picking settings: %s -> %s (Liquid in tip)", old, new)
        self._close_change()
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
        with laid_out_at_once(self):
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

    # -- what the card shows -------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    @property
    def held(self) -> float:
        """µl in the tip now: none without a tip."""
        session = self.session
        return session.in_tip if session.tip.attached is True else 0.0

    def summary(self) -> str:
        """For the card's title row: "20 µl in the tip", "Tip empty"."""
        held = self.held
        return f"{held:g} µl in the tip" if held > 0 else "Tip empty"

    def refresh(self) -> None:
        profile = self.session.profile
        self.settings_line.setText(self._settings_text())
        volume = profile.picking.tip_liquid_vol if profile else 0.0
        self.aspirate_button.setText(f"Aspirate {volume:g} µl")
        held = self.held
        self.dispense_button.setText(f"Dispense {held:g} µl" if held > 0
                                     else "Dispense")
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
        why = why or ("" if held > 0 else "The tip is empty.")
        self.dispense_button.setEnabled(not why)
        self.dispense_button.setToolTip(why)

    def _why_not(self) -> str:
        """Why the robot cannot go to the source now, or ""."""
        session = self.session
        if session.robot is None:
            return "The robot is not connected."
        run = self.run_hold.why() if self.run_hold is not None else None
        if run:
            return run
        if self.jog.busy or (run is None and session.robot_lease.busy):
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

    # -- aspirate, dispense --------------------------------------------------

    def _to_source(self):
        """(go(log), where), or None when the move is refused (said)."""
        session = self.session
        picking = session.profile.picking
        motion = session.motion
        if self.source.current() == DISH:
            xy, _why = dish_center(session.profile)
            z = float(picking.dish_bottom) + float(
                picking.tip_liquid_above_bottom)
            refused = motion.refused((*xy, z))
            if refused:
                self.jog.refuse(f"Not possible: {refused}.")
                return None

            def go(log_):
                motion.tip_to(xy, z, log=log_)
            return go, "the dish center"
        slot, labware_id, definition = self._plate
        well = self._well
        center = session.profile.deck.well_center(slot, definition.load_name)
        dx, dy = well_center.xy(center)

        def go(log_):
            motion.to_well(labware_id, well, "bottom", (dx, dy, 0.0),
                           log=log_)
        return go, f"{well} of slot {slot}"

    def _run(self, act, what: str) -> None:
        """To the source, `act(log)`, the tip up; under the run's lease
        while one holds the robot."""
        if self._why_not():
            return
        target = self._to_source()
        if target is None:
            return
        go, where = target
        motion = self.session.motion

        def job(log_):
            go(log_)
            said = act(log_)
            motion.raise_tip(log=log_)
            return said

        def then(said):
            return f"{said} {where}."

        if self.run_hold is not None and self.run_hold.why() == "":
            self.run_hold.borrow()
        if not self.jog.run_job(job, what=what, then=then):
            if self.run_hold is not None:
                self.run_hold.give_back()
            self.jog.refuse(self.session.robot_lease.refusal)
        self.refresh()

    def _aspirate(self) -> None:
        session = self.session
        picking = session.profile.picking
        volume, rate = float(picking.tip_liquid_vol), float(picking.flow_rate)

        def act(log_):
            log_(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            return f"Aspirated {volume:g} µl from"

        self._run(act, "tip liquid")

    def _dispense(self) -> None:
        session = self.session
        if self.held <= 0:
            return
        rate = float(session.profile.picking.flow_rate)

        def act(log_):
            log_(f"dispense all at {rate:g} µl/s")
            volume = session.dispense(rate)
            return f"Dispensed {volume:g} µl into"

        self._run(act, "tip liquid")
