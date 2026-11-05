"""Manual pick's one Points card (#96): its list (`test_point_list`), and
Undo and Redo of the last 3 moves, on a mock session."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                               # noqa: E402
from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog    # noqa: E402

from micropick.gui.app import Options                       # noqa: E402
from micropick.gui.pages.manual import ManualPage           # noqa: E402
from micropick.gui.session import Session, SessionError     # noqa: E402
from micropick.gui.widgets.overlays import ConfirmDialog    # noqa: E402


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


# -- the card -----------------------------------------------------------------

def test_beside_the_work_the_card_never_folds_and_holds_the_moves(
        app, page, session):
    page.resize(1920, 860)
    page.show()
    app.processEvents()
    card = page.points_card
    assert card.title.text() == "Points"
    assert card.is_open()
    card.set_open(False)
    assert card.is_open()                               # never folds
    session.add_memo_point(100.0, 100.0, 12.0)
    page.points_list.choose("M1")
    for widget in (page.camera_to_button, page.tip_to_button, page.memo_z,
                   page.delete_point_button, page.undo_button,
                   page.redo_button):
        assert card.isAncestorOf(widget)


# -- Undo and Redo ------------------------------------------------------------

def _points(session):
    return [(p.name, p.x, p.y, p.z, p.saved) for p in session.points]


def _add(page, session):
    session.add_memo_point(30.0, 40.0, 5.0)


def _delete_memo(page, session):
    page.points_list.choose("M1")
    page.delete_point_button.click()


def _delete_saved(page, session):
    page.points_list.choose("trough")
    page.delete_point_button.click()


def _type_in(page, name, key, text):
    row = page.points_list.row(name)
    row.edit(key)
    box = row.editor
    if key == "name":
        box.setText(text)
    else:
        box.setValue(float(text))
    QTest.keyClick(box, Qt.Key.Key_Return)


def _type_memo_z(page, session):
    _type_in(page, "M1", "z", "7.5")


def _type_saved_z(page, session):
    _type_in(page, "trough", "z", "7.5")


def _z_from_memo_tip(page, session):
    page.points_list.choose("M1")
    page.jog.pose = (1.0, 2.0, 44.5)
    page._refresh_memo_buttons()
    page.memo_z.click()


def _z_from_saved_tip(page, session):
    page.points_list.choose("trough")
    page.jog.pose = (1.0, 2.0, 44.5)
    page._refresh_memo_buttons()
    page.memo_z.click()


def _rename(page, session):
    _type_in(page, "trough", "name", "bench")


def _save(page, session):
    page.points_list.row("M1").star.click()


def _take_the_star_off(page, session):
    page.points_list.row("trough").star.click()


MOVES = [_add, _delete_memo, _delete_saved, _type_memo_z, _type_saved_z,
         _z_from_memo_tip, _z_from_saved_tip, _rename, _save,
         _take_the_star_off]


@pytest.fixture
def two_points(page, session, monkeypatch):
    """M1 and the saved point trough, with nothing to undo yet, and every
    question answered yes."""
    session.add_memo_point(100.0, 100.0, 12.0)
    session.remember("trough", (150.0, 150.0, 30.0))
    session.memo_points_changed.emit()
    session.point_history.clear()
    page._refresh_memo_buttons()
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *a, **k: ("kept", True))
    monkeypatch.setattr(ConfirmDialog, "confirm",
                        classmethod(lambda cls, host, q, **k: True))


@pytest.mark.parametrize("move", MOVES, ids=lambda m: m.__name__.strip("_"))
def test_a_move_is_undone_and_redone(page, session, two_points, move):
    before = _points(session)
    assert not page.undo_button.isEnabled()
    move(page, session)
    after = _points(session)
    assert after != before
    assert page.undo_button.isEnabled()
    assert not page.redo_button.isEnabled()

    page.undo_button.click()
    assert _points(session) == before
    assert not page.undo_button.isEnabled()
    assert page.redo_button.isEnabled()

    page.redo_button.click()
    assert _points(session) == after
    assert page.undo_button.isEnabled()
    assert not page.redo_button.isEnabled()


def test_a_fourth_move_back_is_not_available(page, session):
    for _ in range(4):
        session.add_memo_point(10.0, 10.0, None)
    assert [p.name for p in session.memo_points] == ["M1", "M2", "M3", "M4"]
    for _ in range(3):
        assert page.undo_button.isEnabled()
        page.undo_button.click()
    assert [p.name for p in session.memo_points] == ["M1"]
    assert not page.undo_button.isEnabled()
    for _ in range(3):
        page.redo_button.click()
    assert [p.name for p in session.memo_points] == ["M1", "M2", "M3", "M4"]
    assert not page.redo_button.isEnabled()


def test_a_new_move_after_an_undo_ends_the_redo(page, session):
    session.add_memo_point(10.0, 10.0, None)
    page.undo_button.click()
    assert page.redo_button.isEnabled()
    session.add_memo_point(20.0, 20.0, None)
    assert not page.redo_button.isEnabled()
    assert [p.x for p in session.memo_points] == [20.0]


def test_undoing_the_delete_of_a_saved_point_puts_it_back_in_the_profile(
        page, session, two_points):
    session.remember("sink", (10.0, 20.0, 3.0))
    session.remember("observe", (1.0, 2.0, 80.0))
    session.memo_points_changed.emit()
    order = list(session.saved_points())
    page.points_list.choose("trough")
    page.delete_point_button.click()
    assert "trough" not in session.profile.positions
    page.undo_button.click()
    assert list(session.saved_points()) == order        # back in its place
    session.load_profile("mock")
    assert session.saved_points()["trough"] == (150.0, 150.0, 30.0)
    assert session.profile.positions["observe"] == (1.0, 2.0, 80.0)


def test_undoing_a_save_takes_it_out_of_the_profile_and_redo_stores_it(
        page, session, two_points):
    page.points_list.row("M1").star.click()
    page.undo_button.click()
    assert page.points_list.names == ["M1", "trough"]
    assert page.points_list.row("M1").star.text() == "☆"
    assert "kept" not in session.profile.positions
    page.redo_button.click()
    assert page.points_list.names == ["trough", "kept"]
    session.load_profile("mock")
    assert session.saved_points()["kept"] == (100.0, 100.0, 12.0)


def test_a_point_at_the_travel_height_comes_back_with_no_z(page, session):
    session.add_memo_point(10.0, 20.0, None)
    session.save_memo_point("M1", "kept")
    assert session.saved_points()["kept"][2] is not None
    page.undo_button.click()
    assert session.point("M1").z is None


def test_a_refused_move_is_no_move(page, session, two_points):
    session.remember("sink", (10.0, 20.0, 3.0))
    session.memo_points_changed.emit()
    session.point_history.clear()
    _type_in(page, "trough", "name", "sink")            # in use
    with pytest.raises(SessionError):
        session.save_memo_point("M1", "shake")
    with pytest.raises(SessionError):
        session.change_point("trough", z=None)
    assert session.delete_memo_point("M7") is False
    assert session.delete_saved_point("nothing") is False
    session.change_point("M1", z=12.0)                  # what it was
    assert not page.undo_button.isEnabled()


def test_undo_reaches_at_a_point_and_the_chosen_point_stays(page, session,
                                                            two_points):
    page.points_list.choose("M1")
    session.add_memo_point(30.0, 40.0, 5.0)
    assert page.source_points.names == ["M1", "M2", "trough"]
    page.undo_button.click()
    assert page.source_points.names == ["M1", "trough"]
    assert page.target_points.names == ["M1", "trough"]
    assert page.chosen_point() == "M1"


def test_loading_a_profile_ends_the_history(page, session):
    session.add_memo_point(10.0, 20.0, None)
    session.load_profile("mock")
    assert not page.undo_button.isEnabled()


def test_undo_has_no_key(page):
    from PySide6.QtGui import QKeySequence, QShortcut
    undo = QKeySequence(QKeySequence.StandardKey.Undo)
    assert all(s.key() != undo for s in page.findChildren(QShortcut))
