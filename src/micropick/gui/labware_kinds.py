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
                              local_definitions, resolve_definition,
                              shared_definitions)
from .widgets.mini_deck import DECK_ROWS, DeckTile

__all__ = ["CHIPS", "catalogue", "deck_tiles", "facts", "in_chip", "kind",
           "well_depth", "well_volume"]

TRASH_SLOT = 12

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


def well_volume(definition: LabwareDefinition) -> float | None:
    """What its first well holds in µl, if the definition says."""
    wells = definition.data.get("wells") or {}
    first = wells.get(definition.wells[0]) if definition.wells else None
    volume = (first or {}).get("totalLiquidVolume")
    return float(volume) if volume else None


def well_depth(definition: LabwareDefinition) -> float | None:
    """How deep its first well is in mm, if the definition says."""
    wells = definition.data.get("wells") or {}
    first = wells.get(definition.wells[0]) if definition.wells else None
    depth = (first or {}).get("depth")
    return float(depth) if depth else None


def facts(definition: LabwareDefinition) -> str:
    volume = well_volume(definition)
    count = definition.well_count
    out = [f"{count} tips" if definition.is_tiprack else
           f"{count} well" if count == 1 else f"{count} wells"]
    if volume:
        out.append(_volume(volume))
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


def deck_tiles(session) -> list[DeckTile]:
    """Every slot, with what the run holds there, else its platform: a deck
    where any slot can be chosen."""
    state = session.run_state
    held = (state.labware if state is not None
            and session.robot is not None else {})
    profile = session.profile
    tiles = []
    for row in DECK_ROWS:
        for slot in row:
            module = (profile.deck.module_for(slot) if profile is not None
                      else None)
            platform = (module.name or "Platform") if module is not None else ""
            entry = held.get(str(slot))
            if entry is None:
                tiles.append(DeckTile(
                    slot, platform=platform,
                    kind="trash" if slot == TRASH_SLOT else None))
                continue
            try:
                definition = resolve_definition(entry.load_name)
            except LabwareError:
                tiles.append(DeckTile(slot, name=entry.load_name,
                                      platform=platform))
                continue
            tiles.append(DeckTile(slot, name=definition.display_name,
                                  kind=kind(definition),
                                  facts=facts(definition), platform=platform))
    return tiles
