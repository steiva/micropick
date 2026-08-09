"""Routine identity vs the plate actually loaded, on a MockRobot.

Covers the stateful part: a match passes, an empty slot or a different
definition is refused, and a resumed routine with progress must be acknowledged
before a session will run it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from micropick.config.schema import Calibration, PickingConfig, PipetteOffset, ProfileMeta
from micropick.config.store import Profile
from micropick.core.calibration.pixel_map import PixelMap
from micropick.config.schema import PixelMap as PixelMapConfig
from micropick.core.routine import Destination, Routine
from micropick.hardware.labware import loaded_labware
from micropick.hardware.mock import MockRobot, open_mock_camera
from micropick.workflows.picking import PickingError, PickingSession

# A real custom plate in labware/, so the definition re-resolves on load.
PLATE = "wide_bore_200ul"


def _pixel_map():
    s = 500.0
    return PixelMap(PixelMapConfig(
        degree=1, cu=450, cv=350, s=s,
        coef=[[0.0, 0.05 * s], [0.05 * s, 0.0]], zero=[0.0, 0.0], ref=[450, 350],
        bounds=[0.0, 0.0, 900.0, 700.0], image_size=[900, 700], sweep_z=0.0))


def _profile():
    return Profile(
        meta=ProfileMeta(name="test"),
        calibration=Calibration(pipette_offset=PipetteOffset(dx=0.0, dy=0.0)),
        picking=PickingConfig(destination_slot=5), cameras={},
        positions={"observe": (150.0, 150.0, 80.0), "shake": (1.0, 2.0, 3.0)},
        path=Path("/tmp/x"))


def _session(routine, robot):
    cam = open_mock_camera(width=900, height=700, fps=120.0)
    return PickingSession(robot, cam, _pixel_map(), _profile(), routine,
                          object(), labware_id="lw1"), cam


# ---------------------------------------------------------------------------
# loaded_labware through the mock's run model
# ---------------------------------------------------------------------------

def test_loaded_labware_reflects_load_and_move():
    robot = MockRobot()
    lid = robot.load_labware(PLATE, 5, namespace="custom_beta", version=1)
    assert loaded_labware(robot)["5"].load_name == PLATE
    robot.move_labware(lid, "offDeck")
    assert "5" not in loaded_labware(robot)          # emptied slot disappears


# ---------------------------------------------------------------------------
# session start checks the slot against the routine
# ---------------------------------------------------------------------------

def _plate_routine(**kw):
    dest = Destination.from_labware(PLATE, slot=5)
    return Routine(dest, {"A1": 1}, name="plate A", **kw)


def test_session_starts_when_slot_matches():
    robot = MockRobot()
    robot.load_labware(PLATE, 5, namespace="custom_beta", version=1)
    session, cam = _session(_plate_routine(), robot)
    cam.close()                                       # constructed without error


def test_session_refuses_empty_slot():
    robot = MockRobot()                               # nothing loaded
    with pytest.raises(PickingError):
        _session(_plate_routine(), robot)


def test_session_refuses_wrong_definition():
    robot = MockRobot()
    robot.load_labware("vwr_96_tiprack_200ul_xl", 5,
                       namespace="custom_beta", version=1)
    with pytest.raises(PickingError):
        _session(_plate_routine(), robot)


def test_unconfirmed_resume_refused_then_allowed(tmp_path):
    robot = MockRobot()
    robot.load_labware(PLATE, 5, namespace="custom_beta", version=1)
    path = tmp_path / "run.json"
    r = _plate_routine(path=path)
    r.next()
    r.record(delivered=1, target="A1")               # progress on disk

    back = Routine.load(path)
    assert back.needs_confirmation
    with pytest.raises(PickingError):
        _session(back, robot)                         # not acknowledged
    back.confirm_resume()
    session, cam = _session(back, robot)              # now it starts
    cam.close()
