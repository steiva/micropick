"""A plate's measured well center: measuring it, and what it adds to a well.

The robot's idea of a well comes from the labware definition and the slot;
the plate on the deck sits a fraction of a millimetre off it. So the tip is
taken to one well's top as the robot has it (`go_to_top`), the operator
jogs it onto the real center level with the rim, and the difference is the
plate's `config.schema.WellCentre`, saved per slot and plate type by
`Session.set_well_centre`. Manual control's Well card and Liquid handling
measure it the same way, with this module; every page that goes into that
plate's wells uses it, wherever it was measured.

No Qt: `go_to_top` blocks on the robot and runs in a worker; the rest is
arithmetic and runs anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..hardware.protocols import xyz
from .motion import Motion

__all__ = ["go_to_top", "Nominal", "Measurement", "deposit_offset",
           "for_plan", "definition_depth", "run_using"]


@dataclass(frozen=True)
class Nominal:
    """Where the robot put the tip for `well`'s top center, on the plate of
    `load_name` in `slot`: what a measurement is taken against."""

    slot: str
    load_name: str
    well: str
    pose: tuple[float, float, float]


def go_to_top(motion: Motion, labware_id: str, well: str, *,
              log=None) -> tuple[float, float, float]:
    """The tip to the top center of `well` as the robot has it; returns the
    pose it reports there, which a measurement is taken against."""
    motion.to_well(labware_id, well, "top", (0.0, 0.0, 0.0), log=log)
    return xyz(motion.robot)


def deposit_offset(cfg, centre) -> tuple[float, float]:
    """The x and y offset of a deposit into a well, from the robot's well
    center: the picking settings' well offset (`cfg.well_offset_x`, `_y`)
    plus the measured center's x and y. Never its z: a deposit keeps the
    robot's own heights. `centre` is a `WellCentre`, its offset, or None
    for a plate not measured."""
    offset = getattr(centre, "offset", centre)
    cx, cy = (0.0, 0.0) if offset is None else (float(offset[0]),
                                                float(offset[1]))
    return float(cfg.well_offset_x) + cx, float(cfg.well_offset_y) + cy


def for_plan(profile, routine, run_state):
    """The measured well center of the plate a plate plan delivers to - the
    plate of the plan's slot, of the type the robot session holds there -
    or None. Wherever it was measured; a picking run adds its x and y to
    every deposit (`deposit_offset`)."""
    if profile is None or routine is None or run_state is None:
        return None
    slot = routine.destination.slot
    entry = run_state.labware.get(str(slot))
    if entry is None:
        return None
    return profile.deck.well_centre(slot, entry.load_name)


def definition_depth(definition, well: str) -> float | None:
    """The labware definition's depth of `well`: what a plate's well depth
    starts from at its first measurement, not what it is."""
    try:
        return float(definition.data["wells"][well]["depth"])
    except (KeyError, TypeError, ValueError):
        return None


def run_using(lease, slot, load_name: str) -> str | None:
    """The run going into this plate now, by name, or None. Its measured
    center is not forgotten or changed under it: the run read it at Start,
    and the profile and the run in progress must not disagree."""
    if lease is None or not lease.uses_plate(slot, load_name):
        return None
    return lease.holder


class Measurement:
    """One measurement in hand, on one page: from the robot's well top
    (`started`, with the pose `go_to_top` returned) to the pose the tip was
    jogged to (`offset`). Kept on the GUI thread; it belongs to the plate it
    was started on and is shown only while that plate is the one chosen."""

    def __init__(self):
        self._nominal: Nominal | None = None

    def started(self, slot, load_name: str, well: str, pose) -> Nominal:
        self._nominal = Nominal(str(slot), load_name, well,
                                tuple(float(v) for v in pose))
        return self._nominal

    def for_plate(self, slot, load_name: str) -> Nominal | None:
        """The measurement in hand, if it was started on this plate."""
        nominal = self._nominal
        if nominal is None or (nominal.slot, nominal.load_name) != (
                str(slot), load_name):
            return None
        return nominal

    def offset(self, pose) -> list[float]:
        """What to save as the plate's well center: `pose` less the robot's
        well top. Raises with no measurement in hand."""
        if self._nominal is None:
            raise ValueError("no measurement in hand: Go to well top first")
        return [float(p) - float(a) for p, a in zip(pose, self._nominal.pose)]

    def forget(self) -> None:
        """Saved, or the deck or the robot changed: measuring again starts
        from Go to well top."""
        self._nominal = None
