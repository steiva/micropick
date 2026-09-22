"""The jog panel's positions are the profile's, and its sections fold.

Offscreen, with a mock session. What is checked is the part that used to be
two stores pretending to be one: saving, renaming, deleting and driving to a
named pose all go to `profile.positions`, and the list shows what is there.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import (POSITION_ROLE,      # noqa: E402
                                             SECTIONS, JogPanel)


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


def rows(panel):
    return [panel.saved.item(i).data(POSITION_ROLE)
            for i in range(panel.saved.count())]


def test_the_list_is_the_profiles_positions(app, session):
    session.profile.positions.clear()
    panel = JogPanel(session)
    assert rows(panel) == []
    session.remember("dish", (10.0, 20.0, 30.0))
    session.remember("plate", (40.0, 50.0, 60.0))
    assert rows(panel) == ["dish", "plate"]
    # The coordinates are on the row, which is the whole reason a name is
    # worth having: "dish" alone says nothing about where it is.
    assert "10.00" in panel.saved.item(0).text()
    assert "30.00" in panel.saved.item(0).text()
    panel.deleteLater()


def test_rename_and_delete_go_to_the_profile(app, session, tmp_path):
    import json
    session.profile.positions.clear()
    session.remember("dish", (10.0, 20.0, 30.0))
    panel = JogPanel(session)
    panel.saved.setCurrentRow(0)

    session.rename_position("dish", "dish A")
    assert rows(panel) == ["dish A"]
    on_disk = json.loads((session.profile.path / "positions.json").read_text())
    assert on_disk == {"dish A": [10.0, 20.0, 30.0]}

    session.forget("dish A")
    assert rows(panel) == []
    assert json.loads((session.profile.path / "positions.json").read_text()) == {}
    panel.deleteLater()


def test_rename_refuses_to_collide(app, session):
    session.profile.positions.clear()
    session.remember("a", (1.0, 2.0, 3.0))
    session.remember("b", (4.0, 5.0, 6.0))
    with pytest.raises(Exception, match="already has a position"):
        session.rename_position("a", "b")
    assert session.profile.positions["a"] == (1.0, 2.0, 3.0)


def test_go_to_drives_to_the_named_pose(app, session):
    session.profile.positions.clear()
    session.remember("corner", (30.0, 40.0, 80.0))
    panel = JogPanel(session)
    panel.saved.setCurrentRow(0)
    assert panel._chosen_position() == "corner"
    # Straight to the controller, without the worker: what is being checked
    # is that the pose comes from the profile.
    panel.controller.move_to(session.profile.positions["corner"])
    assert tuple(round(v, 2) for v in panel.controller.position) == (30.0, 40.0, 80.0)
    panel.deleteLater()


def test_the_positions_outlive_the_robot(app, session):
    session.profile.positions.clear()
    session.remember("kept", (1.0, 2.0, 3.0))
    panel = JogPanel(session)
    session.disconnect_robot()
    app.processEvents()
    # The old in-memory list emptied here, which is what made it useless.
    assert rows(panel) == ["kept"]
    panel.deleteLater()


def test_sections_fold_and_the_names_are_checked(app, session):
    panel = JogPanel(session, collapsed=("positions",))
    assert not panel.move_section.collapsed
    assert panel.positions_section.collapsed
    assert panel.positions_section.body.isHidden()
    panel.positions_section.toggle.click()
    assert not panel.positions_section.collapsed
    assert not panel.positions_section.body.isHidden()
    panel.deleteLater()

    both = JogPanel(session, collapsed=SECTIONS)
    assert both.move_section.collapsed and both.positions_section.collapsed
    both.deleteLater()

    with pytest.raises(ValueError, match="no such jog panel section"):
        JogPanel(session, collapsed=("moove",))


def test_undo_is_with_the_steps_it_undoes(app, session):
    panel = JogPanel(session)
    # It sat under Positions, where it read as undoing a save.
    assert panel.undo_button.parent() is panel.move_section.body
    panel.deleteLater()
