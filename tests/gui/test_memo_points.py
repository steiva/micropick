"""Memo points (REDESIGN §8.9, #18): one can be deleted, by name, and its
number is the next new point's."""

import os
import re

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


# -- saved points (#61, #62) --------------------------------------------------

def _setup_positions(session):
    for name in ("observe", "shake", "tip_calib"):
        session.profile.remember(name, (100.0, 100.0, 80.0))


def test_setup_positions_are_not_saved_points(session):
    _setup_positions(session)
    session.profile.remember("trough", (50.0, 60.0, 30.0))
    assert session.saved_points() == {"trough": (50.0, 60.0, 30.0)}
    session.add_memo_point(10, 20, 5.0)
    assert [(p.name, p.saved) for p in session.points] == [
        ("M1", False), ("trough", True)]
    assert session.point("trough").z == 30.0
    assert session.point("observe") is None


def test_save_turns_a_memo_point_into_a_saved_point_kept_in_the_profile(
        session):
    session.add_memo_point(10, 20, 5.0)
    changes = []
    session.memo_points_changed.connect(lambda: changes.append(1))
    session.save_memo_point("M1", "trough")
    assert session.memo_points == []
    assert changes
    session.load_profile("mock")
    assert session.saved_points() == {"trough": (10.0, 20.0, 5.0)}


@pytest.mark.parametrize("name, said", [
    ("trough", 'A point is already called "trough".'),
    ("M2", 'A point is already called "M2".'),
    ("observe", "observe is set in Robot setup, Position over the dish."),
    ("shake", "shake is set in Robot setup, Shake position."),
    ("  ", "A point needs a name."),
    ("plan-spot:x", "A point's name cannot begin with plan-spot:"),
])
def test_save_refuses_a_used_name_and_a_setup_positions_name(session, name,
                                                             said):
    from micropick.gui.session import SessionError
    session.profile.remember("trough", (1.0, 2.0, 3.0))
    session.add_memo_point(10, 20, 5.0)
    session.add_memo_point(30, 20, 5.0)
    with pytest.raises(SessionError, match=f"^{re.escape(said)}$"):
        session.save_memo_point("M1", name)
    assert [p.name for p in session.memo_points] == ["M1", "M2"]


def test_save_with_no_z_stores_the_top_of_the_jog_envelope(session):
    # No robot top measured yet: the jog envelope's Z top, a plain number.
    session.add_memo_point(10, 20, None)
    top = session.motion.envelope.z[1]
    session.save_memo_point("M1", "trough")
    assert session.saved_points() == {"trough": (10.0, 20.0, top)}


def test_save_with_no_z_stores_the_travel_height_once_measured(
        session, monkeypatch):
    from micropick.workflows.motion import Motion
    monkeypatch.setattr(Motion, "travel_z", property(lambda self: 123.5))
    session.add_memo_point(10, 20, None)
    session.save_memo_point("M1", "trough")
    assert session.saved_points() == {"trough": (10.0, 20.0, 123.5)}


def test_a_saved_point_is_renamed_changed_and_deleted_in_the_profile(
        session):
    from micropick.gui.session import SessionError
    _setup_positions(session)
    session.profile.remember("trough", (1.0, 2.0, 3.0))
    session.rename_saved_point("trough", "bench")
    session.change_point("bench", z=7.5)
    session.load_profile("mock")
    assert session.saved_points() == {"bench": (1.0, 2.0, 7.5)}
    with pytest.raises(SessionError):
        session.rename_saved_point("bench", "tip_calib")
    with pytest.raises(SessionError):
        session.change_point("bench", z=None)          # no travel height
    assert session.delete_saved_point("bench") is True
    session.load_profile("mock")
    assert session.saved_points() == {}
    assert "tip_calib" in session.profile.positions


def test_a_new_memo_point_does_not_take_a_saved_points_name(session):
    session.profile.remember("M1", (1.0, 2.0, 3.0))
    assert session.add_memo_point(10, 20, None).name == "M2"


# -- a saved point made a memo point again (#112) ------------------------------

def _reloaded_positions(session):
    from micropick.config.store import load_profile
    return load_profile(session.profile.name,
                        directory=session.profile.path).positions


def test_unkeep_makes_a_saved_point_a_memo_point_with_its_name_and_z(session):
    session.add_memo_point(10, 20, 30.5)
    session.save_memo_point("M1", "trough")
    changes = []
    session.memo_points_changed.connect(lambda: changes.append(1))
    assert session.unkeep_position("trough") is True
    point = session.memo_point("trough")
    assert (point.x, point.y, point.z, point.saved) == (10, 20, 30.5, False)
    assert "trough" not in session.saved_points()
    assert "trough" not in _reloaded_positions(session)     # at once
    assert changes == [1]


def test_unkeep_tells_the_profile_changed(session):
    session.add_memo_point(10, 20, 30.5)
    session.save_memo_point("M1", "trough")
    profiles = []
    session.profile_changed.connect(profiles.append)
    session.unkeep_position("trough")
    assert profiles == [session.profile]


def test_unkeep_is_one_undo_step(session):
    session.add_memo_point(10, 20, 30.5)
    session.save_memo_point("M1", "trough")
    session.unkeep_position("trough")
    session.undo_points()
    assert session.saved_points()["trough"] == (10, 20, 30.5)
    assert session.memo_point("trough") is None
    assert _reloaded_positions(session)["trough"] == (10, 20, 30.5)
    session.redo_points()
    assert session.memo_point("trough").z == 30.5
    assert "trough" not in session.saved_points()


def test_unkeep_leaves_robot_setups_positions_and_unknown_names_alone(session):
    session.remember("shake", (1, 2, 3))
    assert session.unkeep_position("shake") is False
    assert session.unkeep_position("nothing") is False
    assert session.profile.positions["shake"] == (1, 2, 3)
    assert session.memo_points == []
