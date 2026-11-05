"""Points as chips, under Aspirate's and Dispense's At a point (MANUAL.md
§4.2): the session's points, one chosen."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.point_chips import PointChips         # noqa: E402


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


@pytest.fixture
def chips(session):
    widget = PointChips(session)
    widget.resize(360, 120)
    widget.show()
    yield widget
    widget.deleteLater()


def test_every_point_is_a_chip_a_star_saying_which_kind(session, chips):
    session.add_memo_point(100.0, 100.0, None)
    session.add_memo_point(110.0, 100.0, 70.0)
    session.save_memo_point("M2", "Source")
    assert sorted(chips.names()) == ["M1", "Source"]
    assert chips.chip("M1").text() == "☆ M1"
    assert chips.chip("Source").text() == "★ Source"


def test_a_click_chooses_again_lets_go(session, chips):
    session.add_memo_point(100.0, 100.0, None)
    said = []
    chips.chosen.connect(said.append)
    chips.chip("M1").click()
    assert chips.chosen_name == "M1" and chips.chip("M1").isChecked()
    chips.chip("M1").click()
    assert chips.chosen_name is None and not chips.chip("M1").isChecked()
    assert said == ["M1", None]


def test_one_is_chosen_at_a_time(session, chips):
    session.add_memo_point(100.0, 100.0, None)
    session.add_memo_point(110.0, 100.0, None)
    chips.choose("M1")
    chips.chip("M2").click()
    assert chips.chosen_name == "M2" and not chips.chip("M1").isChecked()


def test_the_chosen_point_going_lets_go(session, chips):
    session.add_memo_point(100.0, 100.0, None)
    said = []
    chips.chosen.connect(said.append)
    chips.choose("M1")
    session.delete_memo_point("M1")
    assert chips.chosen_name is None and said == ["M1", None]


def test_the_chosen_point_stays_as_others_come(session, chips):
    session.add_memo_point(100.0, 100.0, None)
    chips.choose("M1")
    session.add_memo_point(110.0, 100.0, None)
    assert chips.chosen_name == "M1" and chips.chip("M1").isChecked()


def test_a_point_the_tip_cannot_go_to_is_flagged(session, chips, monkeypatch):
    monkeypatch.setattr(session, "memo_problem",
                        lambda p: "Z 1 is below the platform in slot 8")
    session.add_memo_point(100.0, 100.0, 1.0)
    chip = chips.chip("M1")
    assert chip.property("problem") is True
    assert "below the platform" in chip.toolTip()


def test_chips_wrap_inside_their_width_and_scroll(session, chips):
    for i in range(12):
        session.add_memo_point(100.0 + i, 100.0, None)
    chips.resize(360, 120)
    QApplication.processEvents()
    tops = {chips.chip(n).y() for n in chips.names()}
    assert len(tops) > 1
    assert all(chips.chip(n).geometry().right() <= chips.area.viewport().width()
               for n in chips.names())
    assert chips.height() == 120


def test_a_long_name_is_cut_whole_under_the_pointer(session, chips):
    name = "a very long name for a point that goes on and on and on and on"
    session.add_memo_point(100.0, 100.0, 70.0)
    session.save_memo_point("M1", name)
    chip = chips.chip(name)
    assert chip.toolTip() == name and chip.text() != f"★ {name}"
    assert chip.sizeHint().width() <= 360
