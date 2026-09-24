"""The tip on the pipette: what the robot says about it, and the trash.

A tip is the first reason the robot crashes into something. It adds fifty-odd
millimetres to the pipette, and a move planned without it drives that length
into whatever is below. So the one question that matters — is there a tip on
right now — is not answered from a note this software keeps about what it
asked for. It is read from the robot: the run's command log is the robot's own
record, and the last tip command that succeeded in it says what is on the
pipette, whoever issued it — this application, a notebook, or the Opentrons
app. An OT-2 has no tip sensor; `/instruments` reports none. The log is the
best answer there is, and it is the robot's, not ours.

The fixed trash on current robot software (7.1 and later) is not labware in
a run created over HTTP and cannot be loaded into one — slot 12 "is not
provided by the deck configuration". It is an addressable area, `fixedTrash`,
and a tip goes into it with `moveToAddressableAreaForDropTip` followed by
`dropTipInPlace`. `ot2_api` has no method for the first, so it is posted here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .protocols import require_ok

__all__ = ["TipState", "tip_state", "drop_tip_in_trash", "FIXED_TRASH_AREA",
           "TIP_COMMANDS"]

FIXED_TRASH_AREA = "fixedTrash"

# The commands that change what is on the pipette. Anything else in the log,
# a move, a home, a labware load, leaves the tip as it was.
TIP_COMMANDS = ("pickUpTip", "dropTip", "dropTipInPlace")


@dataclass(frozen=True)
class TipState:
    """What the robot's record says is on the pipette.

    `attached` is None when the record could not be read; a caller that
    treats None as "no tip" is making the mistake this module exists to
    prevent. `labware_id` and `well` are where a tip was taken from, when it
    was one this run picked up.
    """

    attached: bool | None
    labware_id: str | None = None
    well: str | None = None

    @classmethod
    def unknown(cls) -> "TipState":
        return cls(attached=None)


def tip_state(api, run_id: str, *, page: int = 100, timeout: float = 10.0,
              session=None) -> TipState:
    """Walk the run's command log backwards to the last tip command.

    Without a cursor the robot serves the most recent page, so the common
    case — a tip command within the last hundred entries — is one request.
    A run with thousands of moves and its only pickUpTip at the start costs
    a request per hundred commands; the answer is worth it.

    Only commands that succeeded count: a pickUpTip the robot declined left
    the pipette as it was.
    """
    import requests
    get = (session or requests).get

    url = f"{api.get_url('runs')}/{run_id}/commands"
    cursor: int | None = None
    while True:
        params = {"pageLength": page}
        if cursor is not None:
            params["cursor"] = cursor
        response = get(url, headers=api.HEADERS, params=params, timeout=timeout)
        if not 200 <= response.status_code < 300:
            return TipState.unknown()
        body = response.json()
        for command in reversed(body.get("data", [])):
            if command.get("status") != "succeeded":
                continue
            kind = command.get("commandType")
            if kind == "pickUpTip":
                p = command.get("params", {})
                return TipState(True, p.get("labwareId"), p.get("wellName"))
            if kind in TIP_COMMANDS:
                return TipState(False)
        start = int(body.get("meta", {}).get("cursor", 0))
        if start <= 0:
            # The whole log, and no tip command in it: the run never had one.
            return TipState(False)
        cursor = max(0, start - page)


def drop_tip_in_trash(api, *, pipette_id: str | None = None) -> None:
    """Move over the fixed trash and let go. Moves the gantry.

    Both answers are checked: the robot answers 201 to a command it then
    declines, and a drop it declined leaves the tip on.
    """
    pipette = pipette_id or getattr(api, "pipette_id", None)
    if not pipette:
        raise RuntimeError("no pipette loaded in the run, so nothing can be "
                           "moved to the trash")
    command = {"data": {"commandType": "moveToAddressableAreaForDropTip",
                        "params": {"pipetteId": pipette,
                                   "addressableAreaName": FIXED_TRASH_AREA,
                                   "offset": {"x": 0, "y": 0, "z": 0},
                                   "alternateDropLocation": False},
                        "intent": "setup"}}
    response = api.post("commands", headers=api.HEADERS,
                        params={"waitUntilComplete": True},
                        data=json.dumps(command))
    require_ok(response, "move to the fixed trash")
    require_ok(api.drop_tip_in_place(verbose=False), "drop tip in the trash")
