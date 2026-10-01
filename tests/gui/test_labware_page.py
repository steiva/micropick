"""Robot & Deck: modules placed from a catalogue of types."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.labware import LabwarePage             # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


def test_a_type_fills_the_height_and_places_on_the_clicked_slot(app, session):
    page = LabwarePage(session)
    assert page.module_type.count() >= 2
    page.module_type.setCurrentText("Picking platform")
    assert page.module_height.value() == pytest.approx(64.2)
    assert not page.add_module_button.isEnabled()          # no slot yet
    page._slot_clicked("5")
    page.module_height.setValue(63.9)                      # still editable
    page._add_module()
    module = session.profile.deck.module_for(5)
    assert module.name == "Picking platform"
    assert module.height_mm == pytest.approx(63.9)
    # Placing again replaces; removing takes it out.
    page.module_type.setCurrentText("Calibration module")
    page._add_module()
    assert session.profile.deck.module_for(5).name == "Calibration module"
    assert len(session.profile.deck.modules) == 1
    page._remove_module()
    assert session.profile.deck.module_for(5) is None
    page.deleteLater()


def test_placing_on_one_slot_of_an_old_multi_slot_module_splits_it(app, session):
    from micropick.config.schema import DeckModule
    session.set_deck_modules([DeckModule(slots=[5, 8, 9], offset=[0, 0, 64.2],
                                         name="platform")])
    page = LabwarePage(session)
    page._slot_clicked("8")
    assert page.module_height.value() == pytest.approx(64.2)
    page.module_type.setCurrentText("Calibration module")
    page._add_module()
    deck = session.profile.deck
    assert deck.module_for(5).slots == [5, 9]
    assert deck.module_for(8).name == "Calibration module"
    page.deleteLater()

