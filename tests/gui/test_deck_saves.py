"""Saving the platforms or the module types keeps the rest of deck.json."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                     # noqa: E402

from micropick.config.schema import DeckModule, ModuleType     # noqa: E402
from micropick.core.liquid import Location                     # noqa: E402
from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.session import Session                      # noqa: E402

TRASH = Location(kind="point", point="Waste")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options())
    s.load_last_profile()
    s.set_default_trash(TRASH)
    yield s
    s.shutdown()


def test_saving_the_platforms_keeps_the_default_trash(session):
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2],
                                         name="Picking platform")])
    assert session.default_trash == TRASH


def test_saving_the_module_types_keeps_the_default_trash(session):
    session.set_module_types([ModuleType(name="Riser", height_mm=30.0)])
    assert session.default_trash == TRASH
