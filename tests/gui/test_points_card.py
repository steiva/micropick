"""Manual pick's one Points card (#96): the Memo and Saved sections, on a
mock session."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                  # noqa: E402

from micropick.gui.app import Options                       # noqa: E402
from micropick.gui.pages.manual import ManualPage           # noqa: E402
from micropick.gui.session import Session                   # noqa: E402


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


@pytest.fixture
def page(app, session):
    p = ManualPage(session)
    yield p
    p.deleteLater()


def _names(table):
    return [table.item(r, 0).text() for r in range(table.rowCount())]


# -- the card -----------------------------------------------------------------

def test_one_card_holds_both_sections_and_the_moves_to_the_chosen_point(page):
    card = page.points_card
    assert card.title.text() == "Points"
    assert card.is_open()
    card.set_open(False)
    assert card.is_open()                               # never folds
    assert page.memo_section.toggle.text() == "Memo"
    assert page.saved_section.toggle.text() == "Saved"
    for section, table in ((page.memo_section, page.memo_table),
                           (page.saved_section, page.saved_table)):
        assert card.isAncestorOf(section)
        assert section.isAncestorOf(table)
    for widget in (page.camera_to_button, page.tip_to_button, page.memo_z):
        assert card.isAncestorOf(widget)
        assert not page.memo_section.isAncestorOf(widget)
        assert not page.saved_section.isAncestorOf(widget)


def test_the_moves_stay_when_both_sections_are_folded(page):
    page.memo_section.set_collapsed(True)
    page.saved_section.set_collapsed(True)
    assert not page.camera_to_button.isHidden()


def test_each_section_keeps_its_table_as_it_was(page, session):
    session.add_memo_point(100.0, 100.0, 12.0)
    session.remember("trough", (150.0, 150.0, 30.0))
    assert page.memo_table.save_button("M1") is not None
    assert page.memo_table.delete_button("M1") is not None
    assert page.saved_table.is_saved("trough")
    assert page.saved_table.delete_button("trough") is not None
