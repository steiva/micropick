"""Memo points (REDESIGN §8.9, #18): one can be deleted, by name, and its
number is the next new point's."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                  # noqa: E402

from micropick.gui.app import Options                       # noqa: E402
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


def test_a_deleted_point_is_gone_and_its_number_is_the_next_ones(session):
    for x in (10, 20, 30):
        session.add_memo_point(x, 50, None)
    changes = []
    session.memo_points_changed.connect(lambda: changes.append(1))
    assert session.delete_memo_point("M2") is True
    assert [p.name for p in session.memo_points] == ["M1", "M3"]
    assert changes == [1]
    assert session.add_memo_point(40, 50, 12.0).name == "M2"


def test_deleting_a_point_that_is_not_there_changes_nothing(session):
    session.add_memo_point(10, 50, None)
    changes = []
    session.memo_points_changed.connect(lambda: changes.append(1))
    assert session.delete_memo_point("M7") is False
    assert [p.name for p in session.memo_points] == ["M1"]
    assert changes == []
