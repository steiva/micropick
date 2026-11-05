"""The toast: where it stands, how long it stays, what kinds there are."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QLabel, QVBoxLayout,       # noqa: E402
                               QWidget)

from micropick.gui.widgets import toast as toast_module                 # noqa: E402
from micropick.gui.widgets.footer import Footer                         # noqa: E402
from micropick.gui.widgets.toast import Toast, show_toast, toast_of     # noqa: E402

LONG = ("Not reachable: Z 50 is below the platform in slot 8, and this "
        "sentence goes on far longer than any window is wide, so that it "
        "has to wrap onto a second line and a third if the window asks.")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _in_the_theme(themed):
    """Every test here sees the program's theme (conftest's `themed`)."""


@pytest.fixture
def window(app):
    """A page over a footer, as the shell has them."""
    host = QWidget()
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    host.page = QLabel("page", host)
    host.footer = Footer(host)
    column.addWidget(host.page, 1)
    column.addWidget(host.footer)
    host.resize(1000, 700)
    host.show()
    app.processEvents()
    yield host
    host.hide()
    host.deleteLater()


def _until(app, done, seconds=2.0):
    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    return done()


def test_a_toast_stands_over_the_footer_at_the_bottom_left(window):
    show_toast(window, "Tip A3 is on.")
    toast = toast_of(window)
    place = toast.geometry()
    assert place.left() < 40
    assert window.footer.geometry().contains(place)


def test_a_toast_stays_there_when_the_window_is_resized(app, window):
    show_toast(window, "Tip A3 is on.")
    window.resize(1200, 800)
    app.processEvents()
    assert window.footer.geometry().contains(toast_of(window).geometry())


def test_a_toast_moves_nothing(app, window):
    page, footer = window.page.geometry(), window.footer.geometry()
    show_toast(window, LONG, refusal=True)
    app.processEvents()
    assert window.page.geometry() == page
    assert window.footer.geometry() == footer


def test_a_long_sentence_wraps_upward_and_is_not_cut(app, window):
    show_toast(window, LONG, refusal=True)
    toast = toast_of(window)
    place = toast.geometry()
    assert place.right() < window.width() * 0.75
    assert place.bottom() >= window.footer.geometry().top()
    assert place.height() >= toast.heightForWidth(place.width())
    assert place.height() > 60


def test_a_toast_is_over_what_was_raised_before_it(app, window):
    cover = QWidget(window)
    cover.setGeometry(window.rect())
    cover.show()
    show_toast(window, "Saved.")
    siblings = [child for child in window.children()
                if isinstance(child, QWidget)]
    assert siblings[-1] is toast_of(window)


def test_an_answer_is_green_and_a_refusal_red(window):
    show_toast(window, "Tip A3 is on.")
    toast = toast_of(window)
    assert toast.property("tone") == "answer"
    show_toast(window, "Robot busy.", refusal=True)
    assert toast.property("tone") == "refusal"
    show_toast(window, "Parked.")
    assert toast.property("tone") == "answer"


def test_an_answer_stays_3_s_and_a_refusal_6_s():
    assert toast_module.ANSWER_MS == 3000
    assert toast_module.REFUSAL_MS == 6000


def test_each_kind_goes_away_after_its_own_time(app, window, monkeypatch):
    monkeypatch.setattr(toast_module, "ANSWER_MS", 40)
    monkeypatch.setattr(toast_module, "REFUSAL_MS", 400)
    show_toast(window, "Saved.")
    toast = toast_of(window)
    assert _until(app, toast.isHidden, 0.3)
    show_toast(window, "Robot busy.", refusal=True)
    assert not _until(app, toast.isHidden, 0.1)
    assert _until(app, toast.isHidden, 1.0)


def test_a_new_toast_replaces_the_one_showing_and_its_time(
        app, window, monkeypatch):
    monkeypatch.setattr(toast_module, "ANSWER_MS", 40)
    monkeypatch.setattr(toast_module, "REFUSAL_MS", 400)
    show_toast(window, "Robot busy.", refusal=True)
    show_toast(window, "Saved.")
    toasts = window.findChildren(Toast)
    assert len(toasts) == 1 and toasts[0].text() == "Saved."
    assert _until(app, toasts[0].isHidden, 0.3)


def test_a_toast_takes_no_click(window):
    show_toast(window, "Saved.")
    place = toast_of(window).geometry().center()
    assert window.childAt(place) is not toast_of(window)
