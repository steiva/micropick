"""JogPanel options for a turned picture: arrows through view_axes."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.schema import TipTarget                    # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import (JogPanel,           # noqa: E402
                                             check_view_axes, view_move)

LOWER = check_view_axes(TipTarget().axes)          # [[0, 1], [1, 0]], det -1


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


def test_without_axes_the_arrows_are_the_robot_axes():
    assert view_move(None, "x", +1) == ("x", +1)
    assert view_move(None, "y", -1) == ("y", -1)


def test_through_the_lower_camera_axes_each_arrow_lands_on_its_picture_way():
    # Right on the picture is +u; axes takes +u to robot +y. Up is -v, which
    # axes takes to robot -x. Z is never turned.
    assert view_move(LOWER, "x", +1) == ("y", +1)
    assert view_move(LOWER, "x", -1) == ("y", -1)
    assert view_move(LOWER, "y", +1) == ("x", -1)
    assert view_move(LOWER, "y", -1) == ("x", +1)
    assert view_move(LOWER, "z", +1) == ("z", +1)


def test_a_slightly_turned_axes_rounds_to_the_nearest_robot_axis():
    tilted = check_view_axes([[0.1, 0.99], [0.99, -0.1]])
    assert view_move(tilted, "x", +1) == ("y", +1)


def test_a_singular_axes_is_refused():
    with pytest.raises(ValueError):
        check_view_axes([[1, 0], [1, 0]])


def _wait(app, panel):
    import time
    end = time.monotonic() + 5
    while panel.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_the_right_arrow_moves_the_mock_robot_along_y(app, session):
    panel = JogPanel(session, view_axes=TipTarget().axes)
    _wait(app, panel)
    before = session.robot.get_position()[0]
    panel._pad[("x", +1)].click()
    _wait(app, panel)
    after = session.robot.get_position()[0]
    step = panel.controller.step
    assert after["y"] == pytest.approx(before["y"] + step, abs=0.05)
    assert after["x"] == pytest.approx(before["x"], abs=0.05)
    assert panel._pad[("x", +1)].text() == "→"
    assert any("as on the picture" in line for line in panel.help_lines)
    panel.set_view_axes(None)
    assert panel._pad[("x", +1)].text() == "X →"
    assert not any("as on the picture" in line for line in panel.help_lines)
    panel.deleteLater()


def test_xy_only_hides_z_unbinds_its_keys_and_leaves_it_out_of_the_help(app, session):
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    panel = JogPanel(session, shortcut_host=host, xy_only=True, parent=host)
    host.show()
    _wait(app, panel)
    assert all(not b.isVisible() for b in panel._z_buttons)
    assert panel._z_shortcuts
    assert all(not s.isEnabled() for s in panel._z_shortcuts)
    assert any(s.isEnabled() for s in panel._shortcuts
               if s not in panel._z_shortcuts)
    assert not any("PgUp" in line or "PgDn" in line
                   for line in panel.help_lines)
    before = session.robot.get_position()[0]["z"]
    panel._move("z", +1)
    _wait(app, panel)
    assert session.robot.get_position()[0]["z"] == pytest.approx(before)
    panel.set_xy_only(False)
    assert all(s.isEnabled() for s in panel._z_shortcuts)
    assert any("PgUp" in line for line in panel.help_lines)
    host.deleteLater()
