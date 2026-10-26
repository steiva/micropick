"""Profiles are invisible: at start the last one used is loaded, and with
none a profile `default` is made silently (REDESIGN §12.8)."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                     # noqa: E402

from micropick.config import store                              # noqa: E402
from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.session import Session                      # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options())
    yield s
    s.shutdown()


def test_with_no_profile_a_default_one_is_made_and_loaded(session):
    profile = session.load_last_profile()
    assert profile.name == "default"
    assert session.profile is profile
    assert store.list_profiles() == ["default"]


def test_the_profile_loaded_last_is_loaded_again(app, session):
    store.create_profile("bench")
    store.create_profile("spare")
    session.load_profile("spare")
    session.load_profile("bench")
    later = Session(Options())
    try:
        assert later.load_last_profile().name == "bench"
    finally:
        later.shutdown()


def test_without_a_record_one_of_the_existing_profiles_is_loaded(session):
    store.create_profile("bench")
    assert session.load_last_profile().name == "bench"
    assert store.list_profiles() == ["bench"]
