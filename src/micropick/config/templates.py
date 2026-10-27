"""Templates: plans saved under a name the operator gave, to start from.

A **template** (GLOSSARY) is a named file of a plan without a plate's
progress: a plate plan (`core.routine.PlanPreset`) under
`Outputs/templates/plate-plans/`, a liquid plan under
`Outputs/templates/liquid-plans/`. Files in the outputs folder, so a
colleague's can be copied in, or opened from anywhere ("Open from file…").

The name on screen is the one in the file (`PlanPreset.name`), never the
file's: a name can hold any character, a file name cannot. Save as makes
a new file, under a name not in use, and never writes over another; Save
changes writes over the open one (REDESIGN §6.3). There is no "are you
sure": what is overwritten is only ever the template that is open.

The plate plan presets saved before templates (`Outputs/plate_plan_presets`)
are moved here the first time templates are listed; a file there that is
not a preset is left where it is.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import paths
from ..core.routine import PlanPreset, RoutineError

__all__ = ["PLATE_PLANS", "LIQUID_PLANS", "Template", "templates_dir",
           "plate_templates", "save_plate_template", "name_in_use"]

log = logging.getLogger(__name__)

PLATE_PLANS = "plate-plans"
LIQUID_PLANS = "liquid-plans"

# Where Save plan as preset… put its files.
_OLD_PRESETS = "plate_plan_presets"


@dataclass(frozen=True)
class Template:
    name: str
    path: Path
    wells: int
    saved: datetime

    def facts(self) -> str:
        """The line under its name in the list: "24 wells · saved Oct 20"."""
        wells = f"{self.wells} well" if self.wells == 1 else f"{self.wells} wells"
        return f"{wells} · saved {self.saved:%b} {self.saved.day}"


def templates_dir(kind: str) -> Path:
    directory = paths.outputs_dir() / "templates" / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _move_old_presets(directory: Path) -> None:
    old = paths.outputs_dir(create=False) / _OLD_PRESETS
    if not old.is_dir():
        return
    for path in sorted(old.glob("*.json")):
        try:
            preset = PlanPreset.load(path)
        except (RoutineError, OSError, ValueError):
            continue
        if not preset.name:
            # The file name was its name: Save plan as preset… named it so.
            preset.name = path.stem
            preset.save(path)
        target = _free_path(directory, preset.name)
        shutil.move(str(path), target)
        log.info("plate plan preset %s moved to %s", path, target)


def _template(path: Path) -> Template | None:
    try:
        preset = PlanPreset.load(path)
    except (RoutineError, OSError, ValueError) as exc:
        log.warning("not a plate plan template: %s (%s)", path, exc)
        return None
    return Template(preset.name or path.stem, path, len(preset.plan),
                    datetime.fromtimestamp(path.stat().st_mtime))


def plate_templates() -> list[Template]:
    """The plate plan templates, the last saved first."""
    directory = templates_dir(PLATE_PLANS)
    _move_old_presets(directory)
    found = [t for t in map(_template, sorted(directory.glob("*.json")))
             if t is not None]
    return sorted(found, key=lambda t: t.saved, reverse=True)


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def name_in_use(name: str) -> bool:
    return any(_key(t.name) == _key(name) for t in plate_templates())


def _free_path(directory: Path, name: str) -> Path:
    """A file for `name` that does not exist yet."""
    stem = re.sub(r"[^\w\- ]+", "_", name).strip(" ._") or "template"
    path, n = directory / f"{stem}.json", 2
    while path.exists():
        path, n = directory / f"{stem} ({n}).json", n + 1
    return path


def save_plate_template(preset: PlanPreset, *,
                        over: Template | None = None) -> Template:
    """`preset` as a new template under `preset.name`, or written over the
    template `over` (its name kept)."""
    if over is not None:
        preset.name = over.name
        path = over.path
    else:
        path = _free_path(templates_dir(PLATE_PLANS), preset.name)
    preset.save(path)
    log.info("plate plan template %r saved: %s, %d wells, %s", preset.name,
             preset.load_name, len(preset.plan), path)
    return _template(path)
