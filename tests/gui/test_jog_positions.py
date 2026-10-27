"""The jog panel has no Move or Positions section; the saved positions are
the profile's and are drawn on the picture's "points" layer.

Offscreen, with a mock session.
"""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QListWidget,        # noqa: E402
                               QWidget)

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.position_marks import PositionMarks           # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.theme.factory import Section                  # noqa: E402
from micropick.gui.widgets.camera_view import CameraView         # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402


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


def _settle(app, panel):
    deadline = time.monotonic() + 10
    while panel.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_the_panel_has_no_move_or_positions_section(app, session):
    panel = JogPanel(session)
    assert panel.findChildren(Section) == []
    # The list of saved positions is gone with its section.
    assert panel.findChildren(QListWidget) == []
    # The pad is still the panel's, outside any folding section.
    assert panel.isAncestorOf(panel.pad)
    panel.deleteLater()


def test_no_page_can_ask_for_a_folded_section(app, session):
    with pytest.raises(TypeError):
        JogPanel(session, collapsed=("positions",))


def test_the_points_layer_follows_the_panels_pose(app, session):
    session.remember("dish", (10.0, 20.0, 30.0))
    host = QWidget()
    view = CameraView(host)
    panel = JogPanel(session, shortcut_host=host, parent=host)
    panel.show_position_on(view)
    _settle(app, panel)
    marks = PositionMarks.on(session, view)
    assert panel.pose is not None
    assert marks.pose == panel.pose
    host.deleteLater()




@pytest.mark.parametrize("name", ["tip_calib", "shake", "observe"])
def test_save_point_refuses_a_setup_positions_name(app, session,
                                                   monkeypatch, name):
    from PySide6.QtWidgets import QInputDialog
    host = QWidget()
    panel = JogPanel(session, shortcut_host=host, parent=host)
    _settle(app, panel)
    session.profile.positions.pop(name, None)
    asked = []
    monkeypatch.setattr(
        QInputDialog, "getText",
        lambda *args, **kwargs: (asked.append(args[1]), (name, True))[1])
    panel._command("save")
    _settle(app, panel)
    assert asked == ["Save point"]
    assert name not in session.profile.positions
    assert "REFUSED" in panel._message
    host.deleteLater()


def test_save_point_saves_a_named_point(app, session, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    host = QWidget()
    panel = JogPanel(session, shortcut_host=host, parent=host)
    _settle(app, panel)
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *args, **kwargs: ("trough", True))
    panel._command("save")
    _settle(app, panel)
    assert "trough" in session.profile.positions
    host.deleteLater()


def test_the_space_key_reads_save_point():
    from micropick.gui import keys
    from micropick.workflows.jog import LAYOUT
    assert keys.action("save").label == "Save point"
    assert [k.help for k in LAYOUT if k.command == "save"] == ["save point"]
