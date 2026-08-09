"""Reading labware definitions, offline and without a robot.

A definition is the single source of truth for a plate's well names and its fill
order, so the picking plan is built from one rather than from a plate size the
operator types in. Definitions come from two places, checked in this order:

1. ``labware/`` in the project, holding the custom plates this rig uses.
2. the ``opentrons-shared-data`` package, holding the stock Opentrons
   definitions, at ``data/labware/definitions/2/<load_name>/<version>.json``.

Both are on disk; nothing here talks to the robot. The robot-facing side
(uploading a definition into a run, loading it into a slot) stays in
``hardware/labware.py`` and reuses the readers here, so there is one parser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths

__all__ = ["LabwareDefinition", "LabwareError", "read_definition",
           "local_definitions", "shared_definition", "resolve_definition"]


class LabwareError(RuntimeError):
    pass


@dataclass
class LabwareDefinition:
    """One labware definition: identity, well names and fill order.

    ``wells`` is the flattened ``ordering`` (column-major, as the file gives it),
    so it is the canonical list a destination iterates. ``ordering`` is kept as
    the list of columns for the row/column orderings.
    """

    load_name: str
    namespace: str
    version: int
    display_name: str
    ordering: list[list[str]]
    data: dict
    source: str                       # "local" or "shared"
    path: Path | None = None
    wells: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.wells:
            self.wells = [w for column in self.ordering for w in column]

    @property
    def well_count(self) -> int:
        return len(self.wells)

    @property
    def uri(self) -> str:
        return f"{self.namespace}/{self.load_name}/{self.version}"

    def __str__(self) -> str:
        return (f"{self.load_name} v{self.version} ({self.namespace}, "
                f"{self.source}), {self.well_count} wells")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _parse(data: dict, source: str, path: Path | None) -> LabwareDefinition:
    where = str(path) if path is not None else f"{source} definition"
    try:
        load_name = data["parameters"]["loadName"]
    except (KeyError, TypeError):
        raise LabwareError(
            f"{where} has no parameters.loadName, so it is not an Opentrons "
            f"labware definition") from None
    for key in ("wells", "ordering"):
        if key not in data:
            raise LabwareError(f"{where} is missing '{key}'")
    return LabwareDefinition(
        load_name=load_name,
        namespace=data.get("namespace", "custom_beta"),
        version=int(data.get("version", 1)),
        display_name=data.get("metadata", {}).get("displayName", load_name),
        ordering=[list(col) for col in data["ordering"]],
        data=data,
        source=source,
        path=path,
    )


def read_definition(path: Path) -> LabwareDefinition:
    """Read one definition file from disk (a local, custom plate)."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as fh:
            return _parse(json.load(fh), "local", path)
    except json.JSONDecodeError as exc:
        raise LabwareError(f"could not read {path.name}: {exc}") from exc


def local_definitions(directory: Path | None = None
                      ) -> dict[str, LabwareDefinition]:
    """Every custom definition in the labware directory, keyed by load name."""
    directory = Path(directory) if directory else paths.labware_dir()
    if not directory.is_dir():
        return {}
    out: dict[str, LabwareDefinition] = {}
    for path in sorted(directory.glob("*.json")):
        definition = read_definition(path)
        if definition.load_name in out:
            raise LabwareError(
                f"two files define {definition.load_name!r}: "
                f"{out[definition.load_name].path.name} and {path.name}")
        out[definition.load_name] = definition
    return out


# ---------------------------------------------------------------------------
# stock definitions from opentrons-shared-data
# ---------------------------------------------------------------------------

def _shared_root():
    try:
        from importlib.resources import files
        return files("opentrons_shared_data") / "data" / "labware" / "definitions" / "2"
    except ModuleNotFoundError as exc:
        raise LabwareError(
            "opentrons-shared-data is not installed, so stock labware "
            "definitions are unavailable; install it or put the definition in "
            "labware/") from exc


def shared_definition(load_name: str,
                      version: int | None = None) -> LabwareDefinition | None:
    """A stock definition from opentrons-shared-data, or None if unknown.

    With no version the newest on disk is taken.
    """
    directory = _shared_root() / load_name
    if not directory.is_dir():
        return None
    versions = sorted(int(p.stem) for p in directory.glob("*.json"))
    if not versions:
        return None
    want = versions[-1] if version is None else version
    if want not in versions:
        raise LabwareError(
            f"stock labware {load_name!r} has no version {want}; "
            f"available: {', '.join(map(str, versions))}")
    data = json.loads((directory / f"{want}.json").read_text(encoding="utf-8"))
    return _parse(data, "shared", None)


# ---------------------------------------------------------------------------
# the resolver
# ---------------------------------------------------------------------------

def resolve_definition(load_name: str, version: int | None = None,
                       directory: Path | None = None) -> LabwareDefinition:
    """Find a definition by load name: custom labware/ first, then stock.

    Raises with the list of available custom definitions if it is in neither, so
    a typo does not surface later as a load failure on the robot.
    """
    local = local_definitions(directory)
    if load_name in local:
        definition = local[load_name]
        if version is not None and definition.version != version:
            raise LabwareError(
                f"{load_name!r} in labware/ is version {definition.version}, "
                f"not {version}")
        return definition

    stock = shared_definition(load_name, version)
    if stock is not None:
        return stock

    known = ", ".join(sorted(local)) or "none"
    raise LabwareError(
        f"no labware definition for {load_name!r}. Custom definitions in "
        f"labware/: {known}. Stock Opentrons load names are also accepted "
        f"(from opentrons-shared-data).")
