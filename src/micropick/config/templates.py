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
not a preset is left where it is. So are the liquid programs saved with
Save as… (`Outputs/liquid`), all but the page's own `current.json`.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import paths
from ..core.liquid import Program, ProgramError
from ..core.routine import PlanPreset, RoutineError

__all__ = ["PLATE_PLANS", "LIQUID_PLANS", "Template", "templates_dir",
           "plate_templates", "save_plate_template", "name_in_use",
           "liquid_templates", "liquid_template", "save_liquid_template"]

log = logging.getLogger(__name__)

PLATE_PLANS = "plate-plans"
LIQUID_PLANS = "liquid-plans"

# Where Save plan as preset… put its files, and the liquid page's Save as….
_OLD_PRESETS = "plate_plan_presets"
_OLD_PROGRAMS = "liquid"
# The liquid page's program as it is edited: not a template.
_CURRENT = "current.json"


@dataclass(frozen=True)
class Template:
    name: str
    path: Path
    wells: int
    saved: datetime
    groups: int | None = None          # a liquid plan's

    def facts(self) -> str:
        """The line under its name in the list: "24 wells · saved Oct 20",
        a liquid plan's "2 groups · 24 wells · saved Oct 20"."""
        wells = f"{self.wells} well" if self.wells == 1 else f"{self.wells} wells"
        if self.groups is not None:
            groups = ("1 group" if self.groups == 1
                      else f"{self.groups} groups")
            wells = f"{groups} · {wells}"
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


def name_in_use(name: str, kind: str = PLATE_PLANS) -> bool:
    listed = plate_templates() if kind == PLATE_PLANS else liquid_templates()
    return any(_key(t.name) == _key(name) for t in listed)


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


# -- liquid plans --------------------------------------------------------------

def _move_old_programs(directory: Path) -> None:
    old = paths.outputs_dir(create=False) / _OLD_PROGRAMS
    if not old.is_dir():
        return
    for path in sorted(old.glob("*.json")):
        if path.name == _CURRENT:
            continue
        try:
            program = Program.load(path)
        except (ProgramError, OSError, ValueError):
            continue
        if not program.name:
            program.name = path.stem
            program.save(path)
        target = _free_path(directory, program.name)
        shutil.move(str(path), target)
        log.info("liquid program %s moved to %s", path, target)


def liquid_template(path: Path) -> Template:
    """The template in the file at `path`, as the list names it. Raises
    ProgramError or OSError for a file that is not one."""
    program = Program.load(path)
    return Template(program.name or path.stem, path,
                    sum(len(g.wells) for g in program.groups),
                    datetime.fromtimestamp(path.stat().st_mtime),
                    groups=len(program.groups))


def _liquid_template(path: Path) -> Template | None:
    try:
        return liquid_template(path)
    except (ProgramError, OSError, ValueError) as exc:
        log.warning("not a liquid plan template: %s (%s)", path, exc)
        return None


def liquid_templates() -> list[Template]:
    """The liquid plan templates, the last saved first."""
    directory = templates_dir(LIQUID_PLANS)
    _move_old_programs(directory)
    found = [t for t in map(_liquid_template, sorted(directory.glob("*.json")))
             if t is not None]
    return sorted(found, key=lambda t: t.saved, reverse=True)


def save_liquid_template(program: Program, *,
                         over: Template | None = None) -> Template:
    """`program` as a new template under `program.name`, or written over
    the template `over` (its name kept)."""
    program = program.model_copy(deep=True)
    if over is not None:
        program.name = over.name
        path = over.path
    else:
        path = _free_path(templates_dir(LIQUID_PLANS), program.name)
    program.save(path)
    log.info("liquid plan template %r saved: %d groups, %s", program.name,
             len(program.groups), path)
    return liquid_template(path)
