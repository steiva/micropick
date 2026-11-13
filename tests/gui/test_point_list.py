"""One points list on Manual pick (#112, MANUAL.md §2.5, §5, §10.11-15): rows
of our own, a star for what is saved, the actions under the chosen point."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt               # noqa: E402
from PySide6.QtGui import QKeyEvent                         # noqa: E402
from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication,   # noqa: E402
                               QInputDialog, QLabel, QLineEdit,
                               QTableWidget)

from micropick.gui.app import Options                       # noqa: E402
from micropick.gui.pages.manual import ManualPage           # noqa: E402
from micropick.gui.session import Session                   # noqa: E402
from micropick.gui.widgets.camera_view import CameraView    # noqa: E402
from micropick.gui.widgets.overlays import ConfirmDialog    # noqa: E402
from micropick.gui.widgets.point_list import BAD, PointList  # noqa: E402

INTERVAL_MS = 40


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    before = app.doubleClickInterval()
    app.setDoubleClickInterval(INTERVAL_MS)
    yield app
    app.setDoubleClickInterval(before)


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    yield s
    s.shutdown()


@pytest.fixture
def points(app, session):
    """A shown list with the actions, M1 and M2 over the saved point trough."""
    session.add_memo_point(100.0, 110.0, 12.0)
    session.add_memo_point(120.0, 130.0, None)
    session.remember("trough", (150.0, 160.0, 30.0))
    widget = PointList(session, actions=True)
    widget.resize(380, 300)
    widget.show()
    app.processEvents()
    yield widget
    widget.deleteLater()


@pytest.fixture
def page(app, session):
    p = ManualPage(session)
    yield p
    p.deleteLater()


def _settle():
    QTest.qWait(INTERVAL_MS * 3)


def _name_asked(monkeypatch, answer):
    asked = []

    def get_text(*args, **kwargs):
        asked.append(kwargs.get("text"))
        return answer

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    return asked


def _answer(monkeypatch, *answers):
    asked, left = [], iter(answers)

    def confirm(cls, host, question, **kwargs):
        asked.append((question, kwargs))
        return next(left)

    monkeypatch.setattr(ConfirmDialog, "confirm", classmethod(confirm))
    return asked


# -- the rows ------------------------------------------------------------------

def test_one_row_a_point_the_memo_points_then_the_saved(points):
    assert points.names == ["M1", "M2", "trough"]
    assert [points.row(n).star.text() for n in points.names] == ["☆", "☆", "★"]
    row = points.row("M1")
    assert [row.cells[k].text() for k in "xyz"] == ["100.00", "110.00", "12.00"]


def test_no_z_reads_travel(points):
    assert points.row("M2").cells["z"].text() == "travel"


def test_the_list_follows_the_session(points, session):
    session.delete_memo_point("M1")
    assert points.names == ["M2", "trough"]
    session.change_point("M2", x=121.5)
    assert points.row("M2").cells["x"].text() == "121.50"


def test_no_table_is_left_for_points_on_manual(page):
    assert page.findChildren(QTableWidget) == []


def test_a_long_name_is_cut_and_keeps_its_whole_self(app, points, session):
    long = "the point over the far corner of the deck, by the trash"
    session.rename_saved_point("trough", long)
    app.processEvents()
    cell = points.row(long).cells["name"]
    assert cell.text() != long and cell.text().endswith("…")
    assert cell.toolTip() == long


def test_a_point_under_a_modules_top_stays_red(points, session, monkeypatch):
    monkeypatch.setattr(session, "memo_problem",
                        lambda p: "inside" if p.name == "M1" else "")
    points.refresh()
    cell = points.row("M1").cells["z"]
    assert BAD in cell.styleSheet() and cell.toolTip() == "inside"
    assert BAD not in points.row("trough").cells["z"].styleSheet()


# -- the star ------------------------------------------------------------------

def test_the_empty_star_asks_a_name_its_own_by_default(
        points, session, monkeypatch):
    asked = _name_asked(monkeypatch, ("bench", True))
    told = []
    points.told.connect(told.append)
    points.row("M1").star.click()
    assert asked == ["M1"]
    assert session.profile.positions["bench"] == (100.0, 110.0, 12.0)
    assert points.row("bench").star.text() == "★"
    assert told == ["bench kept for later."]


def test_a_star_canceled_keeps_nothing(points, session, monkeypatch):
    _name_asked(monkeypatch, ("bench", False))
    points.row("M1").star.click()
    assert session.memo_point("M1") is not None


def test_a_name_in_use_is_refused(points, monkeypatch):
    _name_asked(monkeypatch, ("trough", True))
    said = []
    points.said.connect(said.append)
    points.row("M1").star.click()
    assert said == ['A point is already called "trough".']


def test_the_chosen_point_stays_chosen_under_its_new_name(points, monkeypatch):
    _name_asked(monkeypatch, ("bench", True))
    points.choose("M1")
    points.row("M1").star.click()
    assert points.chosen_name == "bench"


def test_the_full_star_asks_with_keep_primary_then_makes_it_a_memo_point(
        points, session, monkeypatch):
    asked = _answer(monkeypatch, False, True)
    points.row("trough").star.click()
    question, words = asked[0]
    assert question == "Remove trough from saved points?"
    assert words["safe"] == "Keep"
    assert "trough" in session.saved_points()
    points.row("trough").star.click()
    assert "trough" not in session.profile.positions
    assert session.memo_point("trough").z == 30.0
    assert points.row("trough").star.text() == "☆"


def test_taking_a_star_off_is_one_undo_step(points, session, monkeypatch):
    _answer(monkeypatch, True)
    points.row("trough").star.click()
    session.undo_points()
    assert points.row("trough").star.text() == "★"


def test_liquids_star_is_never_taken_off(app, session):
    session.remember("trough", (1.0, 2.0, 3.0))
    session.add_memo_point(4.0, 5.0, None)
    points = PointList(session, star_removable=False)
    assert not points.row("trough").star.isEnabled()
    assert points.row("M1").star.isEnabled()
    points.deleteLater()


# -- choosing a row ------------------------------------------------------------

def test_a_chosen_row_unfolds_its_actions_under_the_point(points):
    assert not points.actions.isVisible()
    points.choose("M2")
    row = points.row("M2")
    assert points.actions.isVisible()
    assert row.isAncestorOf(points.actions)
    assert points.actions.y() >= row.head.geometry().bottom()
    assert [b.text() for b in (points.camera_to, points.tip_to,
                               points.z_from_tip)] == [
        "Camera to", "Tip to", "Z from tip"]
    points.choose("trough")
    assert points.row("trough").isAncestorOf(points.actions)


def test_a_click_chooses_the_row_and_unfolds_it_at_once(points):
    chosen = []
    points.chosen.connect(chosen.append)
    QTest.mouseClick(points.row("M2").cells["x"], Qt.MouseButton.LeftButton)
    assert points.chosen_name == "M2" and chosen == ["M2"]
    assert points.actions.isVisible()
    assert points.row("M2").isAncestorOf(points.actions)


def test_a_click_on_the_chosen_row_lets_it_go(points):
    points.choose("M2")
    QTest.mouseClick(points.row("M2").cells["x"], Qt.MouseButton.LeftButton)
    _settle()
    assert points.chosen_name is None
    assert not points.actions.isVisible()


INERT = Qt.WidgetAttribute.WA_TransparentForMouseEvents


def test_the_actions_of_a_row_that_lifted_take_no_click(points):
    """Folding the row above lifts the one clicked, and the second press of a
    double click, a slow one too, came down on Tip to."""
    points.choose("M1")
    QTest.mouseClick(points.row("M2").cells["x"], Qt.MouseButton.LeftButton)
    assert points.row("M2").isAncestorOf(points.actions)
    assert points.actions.testAttribute(INERT)
    QTest.qWait(INTERVAL_MS + 15)
    assert points.actions.testAttribute(INERT)
    _settle()
    assert not points.actions.testAttribute(INERT)


def test_the_actions_of_a_row_that_stayed_take_clicks_at_once(points):
    points.choose("M2")
    QTest.mouseClick(points.row("M1").cells["x"], Qt.MouseButton.LeftButton)
    assert points.row("M1").isAncestorOf(points.actions)
    assert not points.actions.testAttribute(INERT)


def test_a_double_click_on_a_row_that_lifted_edits_its_cell(points):
    """The second press is on the row's own actions by then."""
    points.choose("M1")
    row = points.row("M2")
    cell = row.cells["y"]
    x = cell.mapTo(row, cell.rect().center()).x()
    QTest.mouseClick(cell, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    QTest.mouseDClick(row, Qt.MouseButton.LeftButton,
                      pos=QPoint(x, points.actions.geometry().center().y()))
    assert row.editor is not None and row._key == "y"
    row.close_editor(False)


def test_a_point_gone_is_chosen_no_more(points, session):
    chosen = []
    points.choose("M1")
    points.chosen.connect(chosen.append)
    session.delete_memo_point("M1")
    assert points.chosen_name is None and chosen == [None]
    assert not points.actions.isVisible()


def test_the_cross_takes_a_memo_point_at_once(points, session, monkeypatch):
    asked = _answer(monkeypatch)
    points.row("M1").delete.click()
    assert session.memo_point("M1") is None and asked == []


def test_the_cross_needs_no_row_chosen_and_chooses_none(points, session):
    points.choose("M2")
    QTest.mouseClick(points.row("M1").delete, Qt.MouseButton.LeftButton)
    assert session.memo_point("M1") is None
    assert points.chosen_name == "M2"


def test_the_cross_asks_for_a_saved_point(points, session, monkeypatch):
    asked = _answer(monkeypatch, False, True)
    points.row("trough").delete.click()
    assert asked[0][0] == "Delete point trough?"
    assert "trough" in session.saved_points()
    points.row("trough").delete.click()
    assert "trough" not in session.saved_points()


def test_a_line_stands_between_the_memo_and_the_saved_points(points, session):
    line = points.divider
    assert line.isVisible()
    assert points.row("M2").geometry().bottom() < line.y() \
        < points.row("trough").y()
    session.delete_memo_point("M1")
    session.delete_memo_point("M2")
    assert not line.isVisible()


def test_the_columns_line_up_with_their_captions(points):
    row = points.row("M1")
    for key, caption in zip("xyz", points.findChildren(QLabel, "caption")[1:]):
        cell = row.cells[key]
        assert caption.mapToGlobal(caption.rect().topRight()).x() \
            == cell.mapToGlobal(cell.rect().topRight()).x()


# -- editing -------------------------------------------------------------------

def _double_click(cell):
    QTest.mouseDClick(cell, Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def _editor(row):
    return row.editor


def _type(box, text):
    box.selectAll() if isinstance(box, QLineEdit) else box.lineEdit().selectAll()
    QTest.keyClicks(box if isinstance(box, QLineEdit) else box.lineEdit(), text)


def test_a_double_click_edits_x_y_and_z(points, session):
    row = points.row("M1")
    _double_click(row.cells["y"])
    assert isinstance(_editor(row), QAbstractSpinBox)
    _type(_editor(row), "111.5")
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert session.memo_point("M1").y == 111.5
    assert points.row("M1").editor is None


def test_a_typed_z_is_said_and_a_z_left_as_it_was_is_not(points, session):
    typed = []
    points.z_typed.connect(typed.append)
    row = points.row("M1")
    _double_click(row.cells["z"])
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert typed == []
    _double_click(row.cells["z"])
    _type(_editor(row), "7.5")
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert typed == [7.5]


def test_a_memo_points_z_goes_down_to_travel_and_a_saved_points_does_not(
        points, session):
    row = points.row("M1")
    _double_click(row.cells["z"])
    assert _editor(row).specialValueText() == "travel"
    _editor(row).setValue(_editor(row).minimum())
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert session.memo_point("M1").z is None
    row = points.row("trough")
    _double_click(row.cells["z"])
    assert _editor(row).specialValueText() == ""
    _editor(row).setValue(0.0)
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert session.saved_points()["trough"] == (150.0, 160.0, 0.0)


def test_only_a_saved_points_name_is_edited(points, session):
    _double_click(points.row("M1").cells["name"])
    assert points.row("M1").editor is None
    row = points.row("trough")
    _double_click(row.cells["name"])
    assert isinstance(_editor(row), QLineEdit)
    _type(_editor(row), "bench")
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert "bench" in session.saved_points()


def test_a_rename_refused_is_said_and_the_row_keeps_its_name(points, session):
    said = []
    points.said.connect(said.append)
    row = points.row("trough")
    _double_click(row.cells["name"])
    _type(_editor(row), "M1")
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    assert said == ['A point is already called "M1".']
    assert points.names == ["M1", "M2", "trough"]


def test_the_chosen_point_stays_chosen_when_renamed(points):
    points.choose("trough")
    row = points.row("trough")
    _double_click(row.cells["name"])
    _type(_editor(row), "bench")
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    _settle()
    assert points.chosen_name == "bench"


def test_a_double_click_on_the_chosen_row_keeps_it_chosen(points):
    points.choose("M1")
    _double_click(points.row("M1").cells["x"])
    _settle()
    assert points.chosen_name == "M1"
    assert points.row("M1").editor is not None


def test_esc_in_a_field_puts_it_back_and_is_no_stop(points, session):
    row = points.row("M1")
    _double_click(row.cells["x"])
    box = _editor(row)
    _type(box, "5")
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape,
                         Qt.KeyboardModifier.NoModifier)
    override.ignore()
    QApplication.sendEvent(box, override)
    assert override.isAccepted()            # the window's Stop does not get it
    QTest.keyClick(box, Qt.Key.Key_Escape)
    assert session.memo_point("M1").x == 100.0
    assert row.editor is None


def test_leaving_a_field_keeps_what_was_typed(points, session):
    row = points.row("M1")
    _double_click(row.cells["x"])
    box = _editor(row)
    box.setValue(101.0)
    QApplication.sendEvent(box, QEvent(QEvent.Type.FocusOut))
    assert session.memo_point("M1").x == 101.0


def test_a_field_gives_the_keyboard_back_to_no_other_field(points, app):
    from PySide6.QtWidgets import QWidget
    row = points.row("M1")
    _double_click(row.cells["x"])
    QTest.keyClick(_editor(row), Qt.Key.Key_Return)
    app.processEvents()
    focus = QApplication.focusWidget()
    assert not isinstance(focus, (QLineEdit, QAbstractSpinBox))
    assert focus is None or isinstance(focus, QWidget)


def test_the_list_takes_no_keyboard(points):
    assert points.area.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert points.row("M1").star.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert points.camera_to.focusPolicy() == Qt.FocusPolicy.NoFocus


# -- the card ------------------------------------------------------------------

def test_the_card_has_the_list_and_under_it_what_makes_points(page):
    card = page.points_card
    assert card.title.text() == "Points"
    assert card.isAncestorOf(page.points_list)
    assert page.place_memo_button.text() == "Point at crosshair"
    assert page.tip_crosshair_button.text() == "Tip to crosshair"
    assert card.isAncestorOf(page.new_z)
    assert card.isAncestorOf(page.undo_button)


def test_new_points_get_z_starts_on_the_last_z_given(page):
    assert [page.new_z.itemText(i) for i in range(page.new_z.count())] == [
        "Travel height", "Z of the tip now", "Last Z given"]
    assert page.new_z.currentText() == "Last Z given"
    assert page.points_card.new_point_z() is None                # none given yet: travel


def test_new_points_get_the_z_chosen(page):
    page.points_card.set_last_z(44.5)
    assert page.points_card.new_point_z() == 44.5
    page.new_z.setCurrentText("Travel height")
    assert page.points_card.new_point_z() is None
    page.new_z.setCurrentText("Z of the tip now")
    page.jog.pose = (1.0, 2.0, 61.234)
    assert page.points_card.new_point_z() == 61.23
    page.jog.pose = None
    assert page.points_card.new_point_z() is None


def test_the_select_leaves_the_arrows_to_the_robot(page):
    assert page.new_z.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_at_a_point_lists_the_same_points_as_chips(page, session):
    session.add_memo_point(10.0, 20.0, None)
    assert page.points_list.actions is not None
    assert page.source_points.names == page.target_points.names == ["M1"]


def test_the_chip_is_on_at_start_on_manual_only(app, page):
    assert page.view.marks_default is True
    assert CameraView().marks_default is False
