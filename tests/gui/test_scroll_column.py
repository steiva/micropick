"""A scroll column measures its text once, not without end (GUI-01)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from micropick.gui.theme import factory                          # noqa: E402

WIDTH = 300


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def column(app, monkeypatch):
    """A shown column of one wrapped label, its measuring passes counted."""
    passes = []
    measure = factory._ScrollColumn._fit_height

    def counted(self):
        passes.append(time.monotonic())
        measure(self)

    monkeypatch.setattr(factory._ScrollColumn, "_fit_height", counted)
    inner = QWidget()
    label = QLabel("One line.")
    label.setWordWrap(True)
    QVBoxLayout(inner).addWidget(label)
    made = factory.scroll_column(inner, WIDTH)
    made.resize(WIDTH + 40, 400)
    made.show()
    made.label = label
    made.passes = passes
    yield made
    made.close()
    made.deleteLater()


def _events_for(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def test_a_column_that_changes_nothing_measures_itself_no_more(app, column):
    _events_for(app, 0.2)
    before = len(column.passes)
    _events_for(app, 0.2)
    assert len(column.passes) == before


def test_a_longer_text_is_still_given_its_height(app, column):
    _events_for(app, 0.2)
    one_line = column.label.minimumHeight()
    column.label.setText("A sentence long enough to wrap. " * 12)
    _events_for(app, 0.2)
    wanted = factory._text_height(column.label, column.label.width())
    assert wanted > 2 * one_line
    assert column.label.minimumHeight() == wanted
