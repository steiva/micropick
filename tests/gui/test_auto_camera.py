"""The opener: once per label, never twice at a time, never after a failure.

Offscreen, with a mock session. The point of each rule is a page that is
looked at repeatedly: without them, an unplugged camera is a multi-second
blocking open every time the operator glances at the page.
"""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.auto_camera import CameraOpener               # noqa: E402
from micropick.gui.session import Session, SessionError          # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()                       # the mock scene needs the gantry pose
    yield s
    s.shutdown()


def settle(app, until, timeout=20.0):
    end = time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_it_opens_the_camera_once(app, session):
    opener = CameraOpener(session)
    opened = []
    opener.opened.connect(opened.append)

    assert opener.ensure("over") is False          # not open yet: asked for
    settle(app, lambda: opened)
    assert session.camera("over") is not None
    assert opened == ["over"]

    # Already open: nothing to do, and it says so rather than opening again.
    assert opener.ensure("over") is True
    settle(app, lambda: len(opened) > 1, timeout=0.5)
    assert opened == ["over"]
    session.close_camera("over")


def test_a_failure_is_not_retried_until_it_is_forgotten(app, session,
                                                        monkeypatch):
    opener = CameraOpener(session)
    failures = []
    opener.failed.connect(lambda label, reason: failures.append(label))
    attempts = []

    real = session.open_camera

    def refuse(label):
        attempts.append(label)
        raise SessionError("no such device")

    monkeypatch.setattr(session, "open_camera", refuse)
    opener.ensure("over")
    settle(app, lambda: failures)
    assert failures == ["over"]
    assert "no such device" in opener.refusal("over")

    # Looking at the page again must not mean another blocking open.
    opener.ensure("over")
    settle(app, lambda: len(attempts) > 1, timeout=0.5)
    assert attempts == ["over"]

    # Until the reason is fixed and the caller says so.
    monkeypatch.setattr(session, "open_camera", real)
    opener.forget("over")
    opener.ensure("over")
    settle(app, lambda: session.camera("over") is not None)
    assert session.camera("over") is not None
    session.close_camera("over")


def test_nothing_happens_without_a_profile_or_a_label(app, tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    bare = Session(Options(mock=True))
    opener = CameraOpener(bare)
    assert opener.ensure("over") is False          # no profile
    assert opener.ensure(None) is False
    assert opener.ensure("") is False


def test_the_upper_camera_is_the_one_the_profile_names(app, session):
    # The mock profile names none, so the fallback is the label that does
    # not say "under" - and it says so by being the fallback.
    assert session.upper_camera_label == "over"
    assert session.lower_camera_label == "under"

    session.profile.meta.camera_label = "under"
    assert session.upper_camera_label == "under"
    assert session.lower_camera_label == "over"
    session.profile.meta.camera_label = None
