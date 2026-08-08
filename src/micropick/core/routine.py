"""Destinations, plans and progress for a picking run.

A `Destination` is the set of places a run can deliver to: a standard well plate
in a deck slot, or a bare list of deck coordinates. A `Routine` holds the plan
(how many objects each target wants), the progress made so far and the order in
which targets are visited.

This module is deliberately free of hardware, windows and vision. Its one piece
of I/O is the progress file: a `Routine` writes itself to disk after every
recorded attempt, so a run interrupted to adjust a parameter resumes where it
stopped even across a kernel restart. Previously progress lived only in the
object and a restart lost an overnight run. That file is not a profile or robot
file; nothing else here touches the disk. See DESIGN sections 2 and 7.

What the old `legacy/core.py` got wrong, and this fixes:

- Progress was in memory only. Here it is persisted atomically after each record.
- Attempts were a boolean success flag. A batch pickup can deliver some objects
  and miss others in the same attempt, which a bool cannot describe, so
  `record` counts objects: `record(delivered=, missed=)`.
- `spread_out` sorted by the *planned* count, a fixed order that spread nothing.
  `least_filled` here recomputes the order from the *actual* fill every call.
- Row labels were a slice of a 26-character string, which ran into punctuation
  past Z and mislabelled the 1536 plate. Labels here are base-26 (A..Z, AA..).
- Coordinate targets were liable to be stringified through a dict key. Here they
  round-trip as tuples of floats.
- The plan was never checked against the destination, so a bad well or a
  negative count surfaced as a deep exception later. Here both are refused at
  construction with a plain message.
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

__all__ = ["Destination", "Routine", "RoutineError", "STRATEGIES",
           "empty_plate_table", "plan_from_table"]

# rows x cols for the standard SBS plates we handle.
WELL_PLATE_PRESETS = {
    6: (2, 3),
    24: (4, 6),
    48: (6, 8),
    96: (8, 12),
    384: (16, 24),
    1536: (32, 48),
}

# OT-2 addressable slots. Slot 12 is the fixed trash, never a destination.
MAX_SLOT = 11

STRATEGIES = ("in_order", "by_row", "by_column", "least_filled")

Target = Union[str, tuple]

_LABEL = re.compile(r"^([A-Z]+)(\d+)$")


class RoutineError(ValueError):
    """A destination, plan, progress file or record was rejected.

    A plain, specific message at the point of the mistake, rather than an
    IndexError or KeyError from somewhere deeper that has to be traced back.
    """


# ---------------------------------------------------------------------------
# well labels
# ---------------------------------------------------------------------------

def _row_label(index: int) -> str:
    """Excel-style base-26 row label: 0->A, 25->Z, 26->AA, 31->AF.

    The old code sliced a fixed 26-character string, so row 27 of the 1536 plate
    became "[" instead of "AA".
    """
    label = ""
    n = index
    while True:
        label = chr(ord("A") + n % 26) + label
        n = n // 26 - 1
        if n < 0:
            return label


def _row_labels(rows: int) -> list[str]:
    return [_row_label(i) for i in range(rows)]


def _split_label(label: str) -> tuple[str, int]:
    """"AF48" -> ("AF", 48). Used by the geometric orderings and the table view."""
    match = _LABEL.match(label)
    if not match:
        raise RoutineError(f"{label!r} is not a well label like 'A1' or 'AF48'")
    return match.group(1), int(match.group(2))


def _row_rank(row: str) -> int:
    """Inverse of _row_label, so labels sort in plate order rather than
    lexically ('B' before 'AA')."""
    rank = 0
    for ch in row:
        rank = rank * 26 + (ord(ch) - ord("A") + 1)
    return rank - 1


# ---------------------------------------------------------------------------
# destination
# ---------------------------------------------------------------------------

class Destination:
    """The set of places a run can deliver to.

    Built through `plate` or `coordinates` rather than a bare constructor, so an
    unknown plate size or a malformed coordinate is refused here, with a
    message, instead of producing an empty or half-formed destination that fails
    later.
    """

    def __init__(self, kind: str, targets: list[Target], *,
                 size: int | None = None, slot: int | None = None):
        self.kind = kind
        self.targets = targets
        self.size = size
        self.slot = slot
        self._target_set = set(targets)

    # -- construction -------------------------------------------------------

    @classmethod
    def plate(cls, size: int, slot: int) -> "Destination":
        if size not in WELL_PLATE_PRESETS:
            known = ", ".join(str(s) for s in sorted(WELL_PLATE_PRESETS))
            raise RoutineError(f"unknown plate size {size!r}; known sizes: {known}")
        if not isinstance(slot, int) or isinstance(slot, bool):
            raise RoutineError(f"slot must be an int, got {slot!r}")
        if not 1 <= slot <= MAX_SLOT:
            raise RoutineError(f"slot must be 1..{MAX_SLOT}, got {slot}")
        rows, cols = WELL_PLATE_PRESETS[size]
        labels = [f"{row}{col}"
                  for row in _row_labels(rows)
                  for col in range(1, cols + 1)]
        return cls("plate", labels, size=size, slot=slot)

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

    @property
    def layout(self) -> tuple[int, int]:
        return WELL_PLATE_PRESETS[self.size]

    def contains(self, target: Target) -> bool:
        return target in self._target_set

    def index(self, target: Target) -> int:
        return self.targets.index(target)

    def __len__(self) -> int:
        return len(self.targets)

    def __repr__(self) -> str:
        if self.is_plate:
            return f"Destination.plate(size={self.size}, slot={self.slot})"
        return f"Destination.coordinates(<{len(self.targets)} points>)"

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        if self.is_plate:
            return {"kind": "plate", "size": self.size, "slot": self.slot}
        # Coordinates are stored as JSON arrays and rebuilt as tuples on load,
        # never used as dict keys, so they never become strings.
        return {"kind": "coordinates",
                "targets": [list(t) for t in self.targets]}

    @classmethod
    def from_dict(cls, data: dict) -> "Destination":
        kind = data.get("kind")
        if kind == "plate":
            return cls.plate(data["size"], data["slot"])
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
        targets = list(self.plan)
        if self.strategy == "in_order":
            return targets
        if self.strategy == "by_row":
            return sorted(targets, key=lambda t: (_row_rank(_split_label(t)[0]),
                                                  _split_label(t)[1]))
        if self.strategy == "by_column":
            return sorted(targets, key=lambda t: (_split_label(t)[1],
                                                  _row_rank(_split_label(t)[0])))
        # least_filled: recomputed from actual delivered counts every call, so
        # the target furthest behind is filled next. Ties keep destination order.
        return sorted(targets,
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
        """Delivered counts in the same shape the plan came in: a rows x cols
        grid for a plate, a flat table for coordinates. For a notebook to show.
        """
        if self.destination.is_plate:
            rows, cols = self.destination.layout
            frame = pd.DataFrame(0, index=_row_labels(rows),
                                 columns=list(range(1, cols + 1)), dtype=int)
            for target, progress in self._progress.items():
                row, col = _split_label(target)
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

def empty_plate_table(size: int) -> pd.DataFrame:
    """A zeroed grid with correct row labels for the operator to fill in.

    Unlike the old `create_well_plan`, the row index is base-26, so the 1536
    plate is labelled A..Z, AA..AF rather than running into punctuation past Z.
    """
    if size not in WELL_PLATE_PRESETS:
        known = ", ".join(str(s) for s in sorted(WELL_PLATE_PRESETS))
        raise RoutineError(f"unknown plate size {size!r}; known sizes: {known}")
    rows, cols = WELL_PLATE_PRESETS[size]
    return pd.DataFrame(0, index=_row_labels(rows),
                        columns=list(range(1, cols + 1)), dtype=int)


def plan_from_table(table: pd.DataFrame, size: int) -> dict[str, int]:
    """Read a filled plate table into a validated plan, skipping the zeros.

    `size` gives the plate the labels are checked against, so a table whose
    shape does not match the named plate is caught here rather than when the
    routine is built.
    """
    if size not in WELL_PLATE_PRESETS:
        known = ", ".join(str(s) for s in sorted(WELL_PLATE_PRESETS))
        raise RoutineError(f"unknown plate size {size!r}; known sizes: {known}")
    rows, cols = WELL_PLATE_PRESETS[size]
    if table.shape != (rows, cols):
        raise RoutineError(
            f"table is {table.shape[0]}x{table.shape[1]}, but a {size}-well "
            f"plate is {rows}x{cols}")

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
            if count > 0:
                plan[f"{row}{col}"] = count
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
