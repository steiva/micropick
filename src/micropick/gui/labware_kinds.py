"""What a deck tile says of a labware definition (REDESIGN §6.1).

Its **kind** - "plate", "tips" or "dish" - is the color of its icon
(`widgets.mini_deck.KIND_COLORS`); anything else (an adapter, a lid) has
none and is drawn neutral. Its **facts** are one line under the name:
"384 wells · 112 µl · flat", "96 tips · 300 µl". The well volume is always
there: two Corning 384 plates differ only by it.

Read from the definition as the robot has it, so a custom one in
`labware/` says the same as a stock one.
"""

from __future__ import annotations

import logging

from ..config.labware import (LabwareDefinition, LabwareError,
                              local_definitions, shared_definitions)

__all__ = ["CHIPS", "catalogue", "facts", "in_chip", "kind"]

log = logging.getLogger(__name__)

# The catalogue's tabs, in order, Frequent the default (#41): the
# profile's recently used and the custom definitions. No Dishes: the robot
# knows no dishes.
CHIPS = ("Frequent", "All", "Plates", "Tips")

_PLATES = ("wellPlate", "reservoir")
_BOTTOMS = {"flat": "flat", "v": "V-bottom", "u": "U-bottom"}


def _is_dish(definition: LabwareDefinition) -> bool:
    words = " ".join((definition.load_name, definition.display_name,
                      definition.category)).lower()
    return "dish" in words or "petri" in words


def kind(definition: LabwareDefinition) -> str | None:
    if definition.is_tiprack:
        return "tips"
    if _is_dish(definition):
        return "dish"
    if definition.category in _PLATES:
        return "plate"
    return None


def in_chip(definition: LabwareDefinition, chip: str) -> bool:
    """Whether `definition` is listed under the tab All, Plates or Tips."""
    if chip == "All":
        return True
    return {"Plates": "plate", "Tips": "tips"}.get(chip) == kind(definition)


def _volume(ul: float) -> str:
    if ul >= 1000:
        return f"{ul / 1000:g} ml"
    return f"{ul:g} µl"


def facts(definition: LabwareDefinition) -> str:
    wells = definition.data.get("wells") or {}
    first = wells.get(definition.wells[0]) if definition.wells else None
    volume = (first or {}).get("totalLiquidVolume")
    count = definition.well_count
    out = [f"{count} tips" if definition.is_tiprack else
           f"{count} well" if count == 1 else f"{count} wells"]
    if volume:
        out.append(_volume(float(volume)))
    bottoms = {(g.get("metadata") or {}).get("wellBottomShape")
               for g in definition.data.get("groups") or []}
    if len(bottoms) == 1 and (bottom := bottoms.pop()) in _BOTTOMS:
        out.append(_BOTTOMS[bottom])
    return " · ".join(out)


def catalogue() -> tuple[dict[str, LabwareDefinition],
                         dict[str, LabwareDefinition]]:
    """(the custom definitions, every definition), by load name: custom
    first, a stock one of the same name left out. A list that cannot be
    read is logged and taken as empty."""
    try:
        custom = local_definitions()
    except LabwareError as exc:
        log.error("custom labware: %s", exc)
        custom = {}
    try:
        stock = shared_definitions()
    except LabwareError as exc:
        log.error("stock labware: %s", exc)
        stock = {}
    known = {**custom, **{n: d for n, d in stock.items() if n not in custom}}
    return custom, known
