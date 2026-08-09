"""Destinations, plans and progress for a picking run.

A `Destination` is the set of places a run can deliver to: a labware plate in a
deck slot, or a bare list of deck coordinates. A `Routine` holds the plan (how
many objects each target wants), the progress made so far, and the order in
which targets are visited.

A plate destination is built from a labware **definition**, not from a plate
size the operator types in. The well names and the fill order come straight from
the definition's `wells` and `ordering`, so there is no independent "format"
input that can disagree with what is physically loaded. The old code took a
number, looked its shape up in a preset table and generated well names itself;
declaring 384 with a 96 loaded then worked until the first well past the real
plate. Removing the input removes the mismatch.

This module is free of hardware, windows and vision. Its I/O is the progress
file, written atomically after every recorded attempt so a run interrupted to
adjust a parameter resumes even across a kernel restart, and it reads labware
definitions through `config.labware` (offline, no robot). See DESIGN sections 2
and 7.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Union

import pandas as pd

from ..config.labware import LabwareDefinition, resolve_definition

__all__ = ["Destination", "Routine", "RoutineError", "STRATEGIES",
           "empty_plate_table", "plan_from_table"]

# OT-2 addressable slots. Slot 12 is the fixed trash, never a destination.
MAX_SLOT = 11

STRATEGIES = ("in_order", "by_row", "by_column", "least_filled")

Target = Union[str, tuple]

_NAME = re.compile(r"^([A-Za-z]+)(\d+)$")


class RoutineError(ValueError):
    """A destination, plan, progress file or record was rejected.

    A plain, specific message at the point of the mistake, rather than an
    IndexError or KeyError from somewhere deeper that has to be traced back.
    """


# ---------------------------------------------------------------------------
# turning an ordering into a planning grid
# ---------------------------------------------------------------------------

def _row_of(name: str) -> str:
    m = _NAME.match(name)
    return m.group(1) if m else name


def _col_of(name: str, fallback: int) -> object:
    m = _NAME.match(name)
    return int(m.group(2)) if m else fallback


def _grid(ordering: list[list[str]]):
    """From a column-major ordering, derive the labels and the cell<->well maps
    for a planning table shaped like the real plate.

    Row and column labels come from the well names (A, B, ... and 1, 2, ...),
    with a positional fallback for names that are not letter+number, so custom
    plates work too.
    """
    n_cols = len(ordering)
    n_rows = len(ordering[0]) if ordering else 0
    row_labels = [_row_of(ordering[0][r]) for r in range(n_rows)]
    col_labels = [_col_of(ordering[c][0], c + 1) for c in range(n_cols)]
    cell_to_well: dict[tuple, str] = {}
    well_to_cell: dict[str, tuple] = {}
    for c in range(n_cols):
        for r in range(len(ordering[c])):
            well = ordering[c][r]
            cell_to_well[(row_labels[r], col_labels[c])] = well
            well_to_cell[well] = (row_labels[r], col_labels[c])
    return row_labels, col_labels, cell_to_well, well_to_cell


# ---------------------------------------------------------------------------
# destination
# ---------------------------------------------------------------------------

class Destination:
    """The set of places a run can deliver to.

    Built through `from_definition`/`from_labware` or `coordinates`, never a bare
    constructor, so a plate always carries the identity and well list of a real
    definition and a coordinate set is validated up front.
    """

    def __init__(self, kind: str, targets: list[Target], *,
                 load_name: str | None = None, version: int | None = None,
                 namespace: str | None = None, slot: int | None = None,
                 ordering: list[list[str]] | None = None):
        self.kind = kind
        self.targets = targets
        self.load_name = load_name
        self.version = version
        self.namespace = namespace
        self.slot = slot
        self.ordering = ordering
        self._target_set = set(targets)

    # -- construction -------------------------------------------------------

    @staticmethod
    def _check_slot(slot: int) -> int:
        if not isinstance(slot, int) or isinstance(slot, bool):
            raise RoutineError(f"slot must be an int, got {slot!r}")
        if not 1 <= slot <= MAX_SLOT:
            raise RoutineError(f"slot must be 1..{MAX_SLOT}, got {slot}")
        return slot

    @classmethod
    def from_definition(cls, definition: LabwareDefinition,
                        slot: int) -> "Destination":
        """A plate destination whose wells and fill order come from a definition."""
        cls._check_slot(slot)
        if not definition.ordering:
            raise RoutineError(
                f"labware {definition.load_name!r} has no ordering")
        return cls("plate", list(definition.wells),
                   load_name=definition.load_name, version=definition.version,
                   namespace=definition.namespace, slot=slot,
                   ordering=[list(col) for col in definition.ordering])

    @classmethod
    def from_labware(cls, load_name: str, slot: int, *, version: int | None = None,
                     directory=None) -> "Destination":
        """Resolve a definition by load name (labware/ then stock) and build."""
        definition = resolve_definition(load_name, version, directory)
        return cls.from_definition(definition, slot)

    @classmethod
    def coordinates(cls, points: Iterable[Iterable[float]]) -> "Destination":
        targets: list[Target] = []
        for point in points:
            coord = tuple(float(v) for v in point)
            if len(coord) not in (2, 3):
                raise RoutineError(
                    f"coordinate {point!r} must be (x, y) or (x, y, z)")
            targets.append(coord)
        if not targets:
            raise RoutineError("a coordinate destination needs at least one point")
        if len(set(targets)) != len(targets):
            raise RoutineError("coordinate destination has duplicate points")
        return cls("coordinates", targets)

    # -- queries ------------------------------------------------------------

    @property
    def is_plate(self) -> bool:
        return self.kind == "plate"

    def contains(self, target: Target) -> bool:
        return target in self._target_set

    def index(self, target: Target) -> int:
        return self.targets.index(target)

    def wells_by_row(self) -> list[str]:
        """Well names row-major (A1, A2, ... B1, ...) from the ordering."""
        n_rows = max((len(col) for col in self.ordering), default=0)
        out = []
        for r in range(n_rows):
            for col in self.ordering:
                if r < len(col):
                    out.append(col[r])
        return out

    def wells_by_column(self) -> list[str]:
        """Well names column-major, as the definition lists them."""
        return [w for col in self.ordering for w in col]

    def __len__(self) -> int:
        return len(self.targets)

    def __repr__(self) -> str:
        if self.is_plate:
            return (f"Destination.plate({self.load_name!r} v{self.version}, "
                    f"slot {self.slot}, {len(self.targets)} wells)")
        return f"Destination.coordinates(<{len(self.targets)} points>)"

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        if self.is_plate:
            # Only the identity is stored; the definition is re-resolved on load,
            # so the file stays small and stays tied to the definition source.
            return {"kind": "plate", "load_name": self.load_name,
                    "version": self.version, "namespace": self.namespace,
                    "slot": self.slot}
        return {"kind": "coordinates",
                "targets": [list(t) for t in self.targets]}

    @classmethod
    def from_dict(cls, data: dict) -> "Destination":
        kind = data.get("kind")
        if kind == "plate":
            definition = resolve_definition(data["load_name"], data.get("version"))
            return cls.from_definition(definition, data["slot"])
        if kind == "coordinates":
            return cls.coordinates(data["targets"])
        raise RoutineError(f"unknown destination kind {kind!r}")


# ---------------------------------------------------------------------------
# progress bookkeeping
# ---------------------------------------------------------------------------

class _Progress:
    """Per-target counts of delivered and missed objects, plus the attempt
    history that lets a restart pick up exactly where it stopped."""

    __slots__ = ("delivered", "missed", "history")

    def __init__(self, delivered: int = 0, missed: int = 0,
                 history: list | None = None):
        self.delivered = delivered
        self.missed = missed
        self.history = history if history is not None else []


# ---------------------------------------------------------------------------
# routine
# ---------------------------------------------------------------------------

class Routine:
    """The plan, the progress and the order for one run.

    `plan` maps a subset of the destination's targets to how many objects each
    wants. Only targets that appear in the plan are visited. Progress is written
    to disk after every `record` when a `path` is bound.
    """

    def __init__(self, destination: Destination, plan: dict[Target, int], *,
                 strategy: str = "in_order", path: str | Path | None = None):
        self.destination = destination
        self.strategy = self._check_strategy(strategy, destination)
        self.plan = self._check_plan(plan, destination)
        self._progress: dict[Target, _Progress] = {t: _Progress() for t in self.plan}
        self._current: Target | None = None
        self.path = Path(path) if path is not None else None

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _check_strategy(strategy: str, destination: Destination) -> str:
        if strategy not in STRATEGIES:
            known = ", ".join(STRATEGIES)
            raise RoutineError(f"unknown strategy {strategy!r}; known: {known}")
        if strategy in ("by_row", "by_column") and not destination.is_plate:
            raise RoutineError(
                f"strategy {strategy!r} needs a plate destination; a coordinate "
                f"destination has no rows or columns, use 'in_order' or "
                f"'least_filled'")
        return strategy

    @staticmethod
    def _check_plan(plan: dict[Target, int],
                    destination: Destination) -> dict[Target, int]:
        if not plan:
            raise RoutineError("plan is empty; nothing to fill")
        checked: dict[Target, int] = {}
        for target, count in plan.items():
            if not destination.contains(target):
                raise RoutineError(
                    f"target {target!r} is not in the destination")
            if isinstance(count, bool) or not isinstance(count, int):
                raise RoutineError(
                    f"count for {target!r} must be an int, got {count!r}")
            if count < 0:
                raise RoutineError(
                    f"count for {target!r} is negative ({count})")
            checked[target] = count
        return checked

    # -- ordering -----------------------------------------------------------

    def _ordered(self) -> list[Target]:
        planned = set(self.plan)
        if self.strategy == "in_order":
            return list(self.plan)
        if self.strategy == "by_row":
            return [w for w in self.destination.wells_by_row() if w in planned]
        if self.strategy == "by_column":
            return [w for w in self.destination.wells_by_column() if w in planned]
        # least_filled: recomputed from actual delivered counts every call, so
        # the target furthest behind is filled next. Ties keep destination order.
        return sorted(self.plan,
                      key=lambda t: (self._progress[t].delivered,
                                     self.destination.index(t)))

    # -- traversal ----------------------------------------------------------

    def next(self) -> Target | None:
        """The next target still short of its plan under the current strategy,
        or None when every target is satisfied."""
        for target in self._ordered():
            if self._progress[target].delivered < self.plan[target]:
                self._current = target
                return target
        self._current = None
        return None

    @property
    def current(self) -> Target | None:
        return self._current

    def remaining(self, target: Target | None = None) -> int:
        target = self._resolve(target)
        return max(0, self.plan[target] - self._progress[target].delivered)

    def is_done(self) -> bool:
        return all(p.delivered >= self.plan[t] for t, p in self._progress.items())

    # -- recording ----------------------------------------------------------

    def record(self, delivered: int = 0, missed: int = 0, *,
               target: Target | None = None) -> None:
        """Record the outcome of one attempt in objects, not attempts.

        A batch pickup can end with some delivered and some missed at once, so
        both are counted. Persists to disk afterwards when a path is bound.
        """
        target = self._resolve(target)
        for name, value in (("delivered", delivered), ("missed", missed)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RoutineError(f"{name} must be an int, got {value!r}")
            if value < 0:
                raise RoutineError(f"{name} is negative ({value})")

        progress = self._progress[target]
        progress.delivered += delivered
        progress.missed += missed
        progress.history.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "delivered": delivered,
            "missed": missed,
        })
        if self.path is not None:
            self.save()

    def _resolve(self, target: Target | None) -> Target:
        if target is None:
            if self._current is None:
                raise RoutineError(
                    "no current target; call next() first or pass target=")
            return self._current
        if target not in self._progress:
            raise RoutineError(f"target {target!r} is not in the plan")
        return target

    # -- views --------------------------------------------------------------

    def progress_table(self) -> pd.DataFrame:
        """Delivered counts in the same shape the plan came in: a grid shaped
        like the real plate for a plate, a flat table for coordinates."""
        if self.destination.is_plate:
            row_labels, col_labels, _, well_to_cell = _grid(self.destination.ordering)
            frame = pd.DataFrame(0, index=row_labels, columns=col_labels, dtype=int)
            for target, progress in self._progress.items():
                row, col = well_to_cell[target]
                frame.at[row, col] = progress.delivered
            return frame
        return pd.DataFrame(
            [{"x": t[0], "y": t[1], "z": t[2] if len(t) == 3 else None,
              "target": self.plan[t],
              "delivered": self._progress[t].delivered,
              "missed": self._progress[t].missed}
             for t in self.destination.targets if t in self._progress])

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "destination": self.destination.to_dict(),
            "strategy": self.strategy,
            "progress": [
                {"target": self._encode_target(t),
                 "count": self.plan[t],
                 "delivered": p.delivered,
                 "missed": p.missed,
                 "history": p.history}
                for t, p in self._progress.items()
            ],
        }

    @staticmethod
    def _encode_target(target: Target):
        # A list for coordinates so they survive as numbers, a plain label for a
        # plate. The reader rebuilds the tuple; the target is never a dict key.
        return list(target) if isinstance(target, tuple) else target

    @staticmethod
    def _decode_target(value):
        return tuple(float(v) for v in value) if isinstance(value, list) else value

    def save(self) -> None:
        if self.path is None:
            raise RoutineError("routine has no path to save to")
        _write_atomic(self.path, json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Routine":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RoutineError(f"{path} is not valid JSON: {exc}") from exc

        destination = Destination.from_dict(data["destination"])
        plan = {cls._decode_target(entry["target"]): entry["count"]
                for entry in data["progress"]}
        routine = cls(destination, plan,
                      strategy=data.get("strategy", "in_order"), path=path)
        for entry in data["progress"]:
            target = cls._decode_target(entry["target"])
            routine._progress[target] = _Progress(
                delivered=entry["delivered"],
                missed=entry["missed"],
                history=list(entry.get("history", [])),
            )
        return routine

    def __repr__(self) -> str:
        done = sum(p.delivered for p in self._progress.values())
        want = sum(self.plan.values())
        return (f"<Routine {self.destination!r} strategy={self.strategy!r} "
                f"{done}/{want} objects>")


# ---------------------------------------------------------------------------
# building a plan from a plate-shaped table
# ---------------------------------------------------------------------------

def empty_plate_table(destination: Destination) -> pd.DataFrame:
    """A zeroed grid shaped like the destination plate, for the operator to fill.

    Row and column labels come from the definition's ordering, so the table
    matches the real plate, custom plates included, with no generated labels.
    """
    if not destination.is_plate:
        raise RoutineError("empty_plate_table needs a plate destination")
    row_labels, col_labels, _, _ = _grid(destination.ordering)
    return pd.DataFrame(0, index=row_labels, columns=col_labels, dtype=int)


def plan_from_table(table: pd.DataFrame,
                    destination: Destination) -> dict[str, int]:
    """Read a filled plate table into a validated plan, skipping the zeros.

    The table's cells are mapped to well names through the destination's grid, so
    a cell that is not a well of this plate, or a non-integer or negative count,
    is caught here rather than mid-routine.
    """
    if not destination.is_plate:
        raise RoutineError("plan_from_table needs a plate destination")
    _, _, cell_to_well, _ = _grid(destination.ordering)

    plan: dict[str, int] = {}
    for row in table.index:
        for col in table.columns:
            value = table.at[row, col]
            count = int(value)
            if count != value:
                raise RoutineError(
                    f"count at {row}{col} is not a whole number ({value!r})")
            if count < 0:
                raise RoutineError(f"count at {row}{col} is negative ({count})")
            if count == 0:
                continue
            well = cell_to_well.get((row, col))
            if well is None:
                raise RoutineError(
                    f"cell {row}{col} is not a well of {destination.load_name!r}")
            plan[well] = count
    if not plan:
        raise RoutineError("table is all zeros; nothing to fill")
    return plan


# ---------------------------------------------------------------------------
# atomic write
# ---------------------------------------------------------------------------

def _write_atomic(path: Path, payload: str) -> None:
    """Write through a temporary file and rename over the target, so a crash
    mid-write leaves the previous progress intact rather than a truncated file
    that would be loaded on the next run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
