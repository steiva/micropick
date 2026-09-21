"""hardware.tips: the tip from the command log, and the trash as an area.

A fake API that serves a paged command log the way the robot does - the last
page when no cursor is given, `meta.cursor` as the page's start - so the
backward walk is exercised across page boundaries without a robot.
"""
from __future__ import annotations

import json

import pytest

from micropick.hardware import tips
from micropick.hardware.protocols import MoveFailed


class _Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Log:
    """GET on /runs/<id>/commands, paged like the robot pages it."""

    def __init__(self, commands, status=200):
        self.commands = commands
        self.status = status
        self.requests = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.requests.append(dict(params))
        n = len(self.commands)
        page = int(params["pageLength"])
        start = params.get("cursor")
        if start is None:
            start = max(0, n - page)
        data = self.commands[start:start + page]
        return _Response({"data": data, "meta": {"cursor": start,
                                                 "totalLength": n}},
                         self.status)


class _Api:
    HEADERS = {"opentrons-version": "3"}
    pipette_id = "pip-1"

    def __init__(self):
        self.posted = []
        self.dropped = 0
        self._move_status = "succeeded"

    def get_url(self, key):
        return "http://robot/runs"

    def post(self, endpoint, headers, params=None, data=None):
        self.posted.append(json.loads(data)["data"])
        return _Response({"data": {"status": self._move_status,
                                   "error": {"detail": "blocked"}}}, 201)

    def drop_tip_in_place(self, verbose=False):
        self.dropped += 1
        return _Response({"data": {"status": "succeeded"}}, 201)


def cmd(kind, status="succeeded", **params):
    return {"commandType": kind, "status": status, "params": params}


def moves(n):
    return [cmd("moveRelative") for _ in range(n)]


def test_last_pick_up_wins():
    log = _Log(moves(3) + [cmd("pickUpTip", labwareId="rack", wellName="C4")]
               + moves(2))
    state = tips.tip_state(_Api(), "run", session=log)
    assert state == tips.TipState(True, "rack", "C4")
    assert len(log.requests) == 1


def test_a_drop_after_the_pick_up_means_no_tip():
    for drop in (cmd("dropTipInPlace"), cmd("dropTip", labwareId="rack",
                                            wellName="C4")):
        log = _Log([cmd("pickUpTip", labwareId="rack", wellName="C4"), drop]
                   + moves(4))
        assert tips.tip_state(_Api(), "run", session=log).attached is False


def test_a_declined_command_does_not_count():
    log = _Log([cmd("pickUpTip", labwareId="rack", wellName="A1"),
                cmd("dropTipInPlace", status="failed")] + moves(2))
    assert tips.tip_state(_Api(), "run", session=log).attached is True


def test_walks_back_across_pages():
    log = _Log([cmd("pickUpTip", labwareId="rack", wellName="H12")] + moves(250))
    state = tips.tip_state(_Api(), "run", page=100, session=log)
    assert state == tips.TipState(True, "rack", "H12")
    assert [r.get("cursor") for r in log.requests] == [None, 51, 0]


def test_a_run_with_no_tip_command_has_no_tip():
    assert tips.tip_state(_Api(), "run", session=_Log(moves(7))).attached is False
    assert tips.tip_state(_Api(), "run", session=_Log([])).attached is False


def test_an_unreadable_log_is_unknown_not_none():
    state = tips.tip_state(_Api(), "run", session=_Log(moves(3), status=500))
    assert state.attached is None
    assert state == tips.TipState.unknown()


def test_drop_in_trash_moves_to_the_area_then_drops():
    api = _Api()
    tips.drop_tip_in_trash(api)
    (move,) = api.posted
    assert move["commandType"] == "moveToAddressableAreaForDropTip"
    assert move["params"]["addressableAreaName"] == "fixedTrash"
    assert move["params"]["pipetteId"] == "pip-1"
    assert api.dropped == 1


def test_drop_in_trash_stops_when_the_move_is_declined():
    api = _Api()
    api._move_status = "failed"
    with pytest.raises(MoveFailed, match="blocked"):
        tips.drop_tip_in_trash(api)
    assert api.dropped == 0                      # no blind drop somewhere else


def test_drop_in_trash_needs_a_pipette():
    api = _Api()
    api.pipette_id = None
    with pytest.raises(RuntimeError, match="no pipette"):
        tips.drop_tip_in_trash(api)
