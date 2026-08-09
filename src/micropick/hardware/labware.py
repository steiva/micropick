"""Talking to the robot about labware.

Reading a definition is pure and lives in `config.labware`; this module is the
robot-facing side: uploading a definition into a run and loading it into a slot.
The readers are re-exported here so existing callers keep working.

The OT-2 only knows the labware Opentrons ships with. Anything else, including
the extended tip racks used here, has to be uploaded into each run before it can
be loaded. Names and namespaces are read out of the definitions rather than
repeated in the calling code, so they cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import paths
from ..config.labware import (LabwareDefinition, LabwareError,
                              local_definitions, read_definition,
                              resolve_definition)

__all__ = ["LabwareDefinition", "LabwareError", "list_definitions",
           "load_definition", "read_definition", "resolve_definition",
           "upload_definition", "ensure_definitions", "load_labware"]

DEFAULT_NAMESPACE = "custom_beta"


# Custom definitions in labware/, keyed by load name. Kept as the historical
# name; the reader now lives in config.labware.
def list_definitions(directory: Path | None = None) -> dict[str, LabwareDefinition]:
    """Every custom definition in the labware directory, keyed by load name."""
    return local_definitions(directory)


def load_definition(load_name: str,
                    directory: Path | None = None) -> LabwareDefinition:
    """A custom definition from labware/ by load name."""
    known = local_definitions(directory)
    if load_name not in known:
        raise LabwareError(
            f"no definition for {load_name!r} in "
            f"{directory or paths.labware_dir()}; "
            f"available: {', '.join(sorted(known)) or 'none'}"
        )
    return known[load_name]


# ---------------------------------------------------------------------------
# talking to the robot
# ---------------------------------------------------------------------------
 
def upload_definition(api, definition: LabwareDefinition, *,
                      timeout: float = 30.0, session=None) -> dict:
    """Register a definition with the current run.
 
    Uploads are per run, so this has to happen again after create_run. The
    response status is checked: the previous version ignored it, and a rejected
    definition then looked exactly like a bad slot number at load time.
    """
    import requests
    post = (session or requests).post
 
    if not getattr(api, "run_id", None):
        raise LabwareError("no active run; call create_run() first")
 
    url = f"{api.get_url('runs')}/{api.run_id}/labware_definitions"
    response = post(url, headers=api.HEADERS,
                    params={"waitUntilComplete": True},
                    data=json.dumps({"data": definition.data}),
                    timeout=timeout)
    if not 200 <= response.status_code < 300:
        raise LabwareError(
            f"the robot rejected {definition.load_name!r} "
            f"({response.status_code}): {response.text[:300]}"
        )
    return response.json() if response.content else {}
 
 
def ensure_definitions(api, load_names=None, *, directory: Path | None = None,
                       verbose: bool = True,
                       session=None) -> dict[str, LabwareDefinition]:
    """Upload the named definitions, or every one found, into this run.
 
    Uploading is idempotent from the robot's point of view, so this is safe to
    call at the top of a session without tracking what has already been sent.
    """
    known = list_definitions(directory)
    if not known:
        raise LabwareError(f"no definitions found in "
                           f"{directory or paths.labware_dir()}")
    wanted = list(known) if load_names is None else list(load_names)
 
    uploaded = {}
    for name in wanted:
        if name not in known:
            raise LabwareError(
                f"no definition for {name!r}; "
                f"available: {', '.join(sorted(known))}"
            )
        definition = known[name]
        upload_definition(api, definition, session=session)
        uploaded[name] = definition
        if verbose:
            print(f"uploaded {definition}")
    return uploaded
 
 
def load_labware(api, load_name: str, slot: int, *,
                 namespace: str | None = None, version: int | None = None,
                 directory: Path | None = None, verbose: bool = False):
    """Load labware into a slot, taking namespace and version from the file.
 
    Passing them by hand is how they drift: the definition is the only place
    that actually knows.
    """
    if namespace is None or version is None:
        try:
            definition = load_definition(load_name, directory)
            namespace = namespace or definition.namespace
            version = version if version is not None else definition.version
        except LabwareError:
            namespace = namespace or "opentrons"      # a stock definition
    kwargs = {"namespace": namespace, "verbose": verbose}
    try:
        return api.load_labware(load_name, slot, **kwargs)
    except TypeError:
        return api.load_labware(load_name, slot, namespace=namespace)
 
