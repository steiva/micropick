"""Reset setup (REDESIGN §12.8): the profile folder is archived beside it,
dated, then the cameras, the camera calibration, the platforms and the
positions are cleared. The deck and the picking settings are kept."""

import os
import zipfile

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                     # noqa: E402

from micropick.config import store                              # noqa: E402
from micropick.config.schema import (DeckModule, PixelMap,      # noqa: E402
                                     WellCenter)
from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.session import Session                      # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options())
    s.load_last_profile()
    yield s
    s.shutdown()


def _pixel_map() -> PixelMap:
    return PixelMap(degree=1, cu=0, cv=0, s=1, coef=[[1, 0], [0, 1]],
                    zero=[0, 0], ref=[0, 0], bounds=[0, 0, 1, 1],
                    image_size=[100, 100], sweep_z=80.0)


def _set_up(session):
    session.add_camera("upper", "20 MP USB camera", [(2592, 1944)])
    session.add_camera("lower", "Arducam", [(2000, 1500)])
    profile = session.profile
    profile.calibration.pixel_map = _pixel_map()
    profile.save_calibration()
    session.set_deck_modules([DeckModule(slots=[5, 8], offset=[0, 0, 64.2],
                                         name="Picking platform")])
    session.remember("observe", (245.1, 132.4, 80.0))
    session.remember("shake", (251.0, 140.2, 62.5))
    session.remember("tip_calib", (380.2, 220.6, 95.0))
    session.remember("waste", (10.0, 20.0, 30.0))
    session.set_dish_bottom(66.4)
    profile.deck.well_centers.append(WellCenter(
        slot=2, load_name="corning_96", offset=[0.1, 0.2, 0.3]))
    profile.deck.remember_labware("corning_96")
    profile.save_deck()


def test_reset_clears_the_setup_and_keeps_the_deck_and_picking(session):
    _set_up(session)
    session.reset_setup()
    again = store.load_profile(session.profile.name)
    for profile in (session.profile, again):
        assert profile.cameras == {}
        assert profile.meta.camera_label is None
        assert profile.pixel_map is None
        assert profile.deck.modules == []
        assert set(profile.positions) == {"waste"}       # a saved point
        assert profile.picking.dish_bottom == 66.4
        assert [c.load_name for c in profile.deck.well_centers] == [
            "corning_96"]
        assert profile.deck.recent_labware == ["corning_96"]
    assert session.upper_camera_label is None
    assert session.lower_camera_label is None


def test_reset_archives_the_profile_beside_it_dated_first(session):
    _set_up(session)
    folder = session.profile.path
    archive = session.reset_setup()
    assert archive.parent == folder.parent
    assert archive.name.startswith(f"{folder.name}-") and archive.suffix == ".zip"
    with zipfile.ZipFile(archive) as zf:
        positions = zf.read(f"{folder.name}/{store.POSITIONS_FILE}").decode()
    assert "observe" in positions and "tip_calib" in positions
    # Not a profile of its own: never loaded in place of the real one.
    assert store.list_profiles() == [folder.name]


def test_reset_is_refused_while_the_robot_is_busy(session):
    _set_up(session)
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        with pytest.raises(Exception, match="Robot busy"):
            session.reset_setup()
    finally:
        lease.release()
    assert "observe" in session.profile.positions
