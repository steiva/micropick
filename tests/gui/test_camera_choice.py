"""Which attached camera is the upper and which the lower (#9): the session
lists the attached cameras (the mock ones under --mock), shows any of them
live before it is chosen, and writes the choice - swapping the two when
the other role's device is chosen, adding a camera to a profile without
one."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                     # noqa: E402

from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.session import Session, SessionError        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    yield s
    s.shutdown()


def _connect(session):
    session.probe_robot()
    session.new_run()


def _devices(session):
    return {role: session.camera_device(role) for role in ("upper", "lower")}


def test_the_mock_cameras_are_the_attached_ones(session):
    assert session.attached_cameras() == ["mock-over", "mock-under"]
    assert _devices(session) == {"upper": "mock-over", "lower": "mock-under"}


def test_choosing_the_other_roles_camera_swaps_them(session):
    swapped = session.choose_camera("upper", "mock-under")
    assert swapped == "mock-over"
    assert _devices(session) == {"upper": "mock-under", "lower": "mock-over"}
    # Written into the profile, not only held here.
    session.load_profile("mock")
    assert _devices(session) == {"upper": "mock-under", "lower": "mock-over"}


def test_choosing_the_same_camera_changes_nothing(session):
    assert session.choose_camera("lower", "mock-under") is None
    assert _devices(session) == {"upper": "mock-over", "lower": "mock-under"}


def test_a_profile_without_cameras_gets_one(session):
    session.reset_setup()
    assert _devices(session) == {"upper": None, "lower": None}
    session.choose_camera("lower", "mock-under")
    assert _devices(session) == {"upper": None, "lower": "mock-under"}
    session.choose_camera("upper", "mock-over")
    assert _devices(session) == {"upper": "mock-over", "lower": "mock-under"}


def test_a_camera_is_not_chosen_during_a_run(session):
    lease = session.robot_lease.claim("picking run", run=True)
    with pytest.raises(SessionError, match="Robot busy"):
        session.choose_camera("upper", "mock-under")
    lease.release()
    assert _devices(session) == {"upper": "mock-over", "lower": "mock-under"}


def test_a_profile_cameras_device_is_seen_through_that_camera(session):
    _connect(session)
    camera = session.preview_camera("lower", "mock-over")
    # One device, opened once: the upper camera as the pages have it.
    assert camera is session.camera("over")
    session.close_preview()
    assert session.camera("over") is camera and camera.is_alive()


def test_another_device_is_seen_through_a_camera_of_its_own(session):
    _connect(session)
    session.reset_setup()
    camera = session.preview_camera("lower", "mock-under")
    assert session.open_cameras == []
    assert camera.crop == 0.5 and camera.is_alive()
    end = time.monotonic() + 5
    while not camera.read()[0] and time.monotonic() < end:
        time.sleep(0.01)
    assert camera.read()[0]
    # The next preview lets it go.
    session.preview_camera("lower", "mock-over")
    assert not camera.is_alive()
    session.close_preview()


def test_a_saved_name_is_the_attached_camera_it_resolves_to(session):
    # Saved as a vendor's shorter name: opened as the attached one.
    session.assign_camera_devices({"under": "mock-und"})
    names = session.attached_cameras()
    assert session.camera_device("lower") == "mock-und"
    assert session.camera_device("lower", attached=names) == "mock-under"
