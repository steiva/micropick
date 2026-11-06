"""A path (`widgets.path_steps`, TIP.md §2.2): done, current and later, only
the current item with a body, and no numbers."""

import os
import re

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget      # noqa: E402

from micropick.gui.widgets.path_steps import (CURRENT, DONE, LATER,  # noqa: E402
                                              PathItem, PathSteps)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def path(app):
    bodies = {key: QLabel(key) for key in ("go", "check", "find", "fine")}
    widget = PathSteps([
        PathItem("go", "Camera over the disc", "Camera is over the disc",
                 bodies["go"]),
        PathItem("check", "Disc in the picture", "Disc is in the picture",
                 bodies["check"]),
        PathItem("find", "Find the tip", "Tip found", bodies["find"],
                 link_text="Redo"),
        PathItem("fine", "Adjust by hand · optional",
                 "Adjust by hand · optional", bodies["fine"]),
    ])
    widget.bodies = bodies
    widget.show()
    yield widget
    widget.deleteLater()


def _states(path):
    return [path.state(k) for k in ("go", "check", "find", "fine")]


def test_set_current_marks_those_before_done_and_after_later(path):
    path.set_current("find")
    assert _states(path) == [DONE, DONE, CURRENT, LATER]
    assert path.current == "find"


def test_set_all_done_leaves_no_current_item(path):
    path.set_all_done()
    assert _states(path) == [DONE] * 4
    assert path.current is None


def test_it_starts_later_and_can_go_back_there(path):
    assert _states(path) == [LATER] * 4
    path.set_current("check")
    path.set_all_later()
    assert _states(path) == [LATER] * 4


def test_a_done_item_shows_its_result_and_the_others_their_task(path):
    path.set_current("check")
    assert path.title("go") == "Camera is over the disc"
    assert path.title("check") == "Disc in the picture"
    assert path.title("find") == "Find the tip"


def test_only_the_current_item_has_a_body(path):
    path.set_current("check")
    shown = [k for k, body in path.bodies.items() if body.isVisible()]
    assert shown == ["check"]
    path.set_all_done()
    assert not any(b.isVisible() for b in path.bodies.values())


def test_a_link_is_on_a_done_item_only_and_names_it(path):
    said = []
    path.link_clicked.connect(said.append)
    link = path.link("find")
    path.set_current("find")
    assert not link.isVisible()
    path.set_current("fine")
    assert link.isVisible() and link.text() == "Redo"
    link.click()
    assert said == ["find"]
    assert path.link("go") is None


def test_no_title_ever_shows_a_digit(path):
    for move in (path.set_all_later, path.set_all_done,
                 lambda: path.set_current("go"),
                 lambda: path.set_current("fine")):
        move()
        for key in ("go", "check", "find", "fine"):
            assert not re.search(r"\d", path.title(key))


def test_a_hidden_item_leaves_the_line_ending_at_the_one_before(path):
    path.set_hidden("fine", True)
    path.set_all_done()
    rows = path._rows
    assert not rows["fine"].box.isVisible()
    assert rows["find"].node.last and not rows["check"].node.last
    path.set_hidden("fine", False)
    assert rows["fine"].node.last and not rows["find"].node.last


def test_it_paints_every_state(path):
    path.set_current("check")
    assert not path.grab().isNull()


def test_the_page_keeps_its_body_widgets(app):
    body = QWidget()
    path = PathSteps([PathItem("only", "Task", "Done", body)])
    assert body.parent() is not None
    path.set_current("only")
    assert path.state("only") == CURRENT
    assert path._rows["only"].node.last
    path.deleteLater()


def test_the_rows_show_the_card_behind_them(themed):
    """A plain QWidget under the theme paints the window's color."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor

    from micropick.gui.theme import COLORS
    from micropick.gui.theme.factory import card
    frame = card()
    widget = PathSteps([PathItem("go", "Go", "Gone", QLabel("a sentence"))],
                       frame)
    frame.layout().addWidget(widget)
    widget.set_current("go")
    frame.resize(400, 200)
    image = frame.grab().toImage()
    inside = widget.mapTo(frame, QPoint(widget.width() - 4, 4))
    assert image.pixelColor(inside) == QColor(COLORS["card"])
    frame.deleteLater()
