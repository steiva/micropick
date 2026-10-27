"""Manual Work (REDESIGN §6.6, #18): a left click moves the camera or the
tip, a right click marks a point, Look at, past picks, the tip gauge and
the sizes after Analyze the dish."""

import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.detector import Detection                     # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.session import Session                        # noqa: E402


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
def page(app, session):
    p = ManualPage(session)
    yield p
    p.deleteLater()


def _detection(sizes):
    df = pd.DataFrame({"diameter_microns": np.asarray(sizes, dtype=float)})
    empty = df.iloc[0:0]
    return Detection(np.empty((0, 4)), np.empty(0), df, empty, empty, empty,
                     {}, classified=True, standin=False)


def test_detected_sizes_go_to_the_shared_histogram(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    assert page.sizes.toggle.isChecked() is False          # folded
    page._cuboids = _detection([300, 320, 600])
    page._show_sizes()
    assert page.histogram.state.text().startswith(
        "2 of 3 cuboids are inside 250–500 µm")
    page._forget_targets()
    assert page.histogram.state.text() == "nothing measured yet"


W, H, K = 2592, 1944, 0.026


def _calibrate(session):
    """A linear toy map over the whole frame and a pipette offset."""
    from micropick.config.schema import PipetteOffset
    from micropick.config.schema import PixelMap as PixelMapConfig
    s = W / 2
    profile = session.profile
    profile.calibration.pixel_map = PixelMapConfig(
        degree=1, cu=W / 2, cv=H / 2, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0],
        ref=[W / 2, H / 2], bounds=[100.0, 100.0, W - 100.0, H - 100.0],
        image_size=[W, H], sweep_z=0.0)
    profile.calibration.pipette_offset = PipetteOffset(dx=10.0, dy=-5.0)


def _open_upper(app, page, session):
    page.show()                      # the picture's layers draw on screen
    session.open_camera(session.upper_camera_label)
    app.processEvents()


def _wait(app, page):
    import time
    end = time.monotonic() + 5
    while page.jog.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _over_the_deck(app, page, session):
    session.robot.move_to_coordinates((150.0, 150.0, 90.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)


def _left_click(app, page, u, v):
    page.view.clicked.emit(u, v)
    _wait(app, page)


def _xy(session):
    pos = session.robot.get_position(verbose=False)[0]
    return pos["x"], pos["y"]


def test_a_single_left_click_moves_the_camera_there(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    # 100 px right of the reference pixel is 2.6 mm in x.
    _left_click(app, page, W / 2 + 100, H / 2)
    assert _xy(session) == pytest.approx((150.0 + 100 * K, 150.0), abs=0.05)


def test_in_tip_mode_a_click_sends_the_tip_over_the_clicked_point(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    page.click_mode.buttons[1].click()
    assert page.center_button.text() == "Tip back to center"
    _over_the_deck(app, page, session)
    # The tip is the pipette offset further than the camera's point.
    _left_click(app, page, W / 2 + 100, H / 2)
    assert _xy(session) == pytest.approx((150.0 + 100 * K + 10.0, 145.0),
                                         abs=0.05)


def test_a_click_outside_the_jog_envelope_moves_nothing(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    session.robot.move_to_coordinates((20.0, 20.0, 90.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)
    before = _xy(session)
    # Far to the left and up of the deck's corner: past the soft limits.
    _left_click(app, page, 110.0, 110.0)
    assert _xy(session) == pytest.approx(before)
    assert "UNREACHABLE" in page.jog.position_lines[-1]


def test_a_click_while_the_robot_is_busy_elsewhere_moves_nothing(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    before = _xy(session)
    lease = session.robot_lease.claim("someone else")
    try:
        _left_click(app, page, W / 2 + 100, H / 2)
    finally:
        lease.release()
    assert _xy(session) == pytest.approx(before)


def _points_drawn(page):
    from micropick.viz import overlays
    from micropick.gui.position_marks import MEMO
    return [i for i in page.view._marks
            if isinstance(i, overlays.Circle) and i.color == MEMO]


def test_a_right_click_marks_a_point_drawn_only_while_points_are_shown(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    before = _xy(session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
    assert [p.name for p in session.memo_points] == ["M1"]
    assert _xy(session) == pytest.approx(before)        # marking moves nothing
    # Marked to be seen: the points open.
    assert page.points_shown
    assert len(_points_drawn(page)) == 1
    page.show_points(False)
    assert _points_drawn(page) == []


def test_a_point_is_deleted_by_its_cross(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    page.view.right_clicked.emit(W / 2 - 100, H / 2)
    _wait(app, page)
    assert [p.name for p in session.memo_points] == ["M1", "M2"]
    page.memo_table.delete_button("M1").click()
    assert [p.name for p in session.memo_points] == ["M2"]
    assert len(_points_drawn(page)) == 1


def test_look_at_moves_the_camera_over_the_slot(app, page, session):
    from micropick.core.deck import slot_center
    _calibrate(session)
    _wait(app, page)
    page.look_at.slot_chosen.emit(5)
    _wait(app, page)
    x, y = slot_center(5)
    # The slot's center is the tip's frame: the gantry stops a pipette
    # offset short of it.
    assert _xy(session) == pytest.approx((x - 10.0, y + 5.0), abs=0.05)


def test_the_tip_gauge_follows_aspirate_and_dispense(app, page, session):
    from micropick.config.labware import LabwareDefinition
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")
    assert session.tip.attached is True
    page.volume.setValue(15.0)
    page._aspirate()
    _wait(app, page)
    assert page.gauge.volume.text() == "15 µl"
    session.read_tip()                       # the same tip, read again
    assert page.gauge.volume.text() == "15 µl"
    page._dispense()
    _wait(app, page)
    assert page.gauge.volume.text() == "0 µl"
    page._aspirate()
    _wait(app, page)
    session.drop_tip_in_trash()
    assert page.gauge.volume.text() == "0 µl"           # dropped with the tip


def test_past_picks_are_drawn_while_shown(app, page, session):
    from micropick.viz import overlays
    from micropick.gui.position_marks import PICK
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.record_pick((150.0, 150.0))
    page.record_pick((152.0, 150.0))

    def rings():
        return [i for i in page.view._marks
                if isinstance(i, overlays.Circle) and i.color[0] > 0
                and i.color != (255, 220, 0)]
    assert rings() == []                                 # off to begin with
    page.past_picks_button.click()
    assert page.past_picks_button.isChecked()
    drawn = rings()
    assert len(drawn) == 2
    # The last bright, the older faint.
    assert sum(c.color[0] for c in drawn if c.color == PICK) > 0
    assert {c.color for c in drawn} != {PICK}


def test_the_same_sizes_are_drawn_over_the_picture(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    page._cuboids = _detection([300, 320, 600])
    page._show_sizes()
    bins = page.view._histogram
    assert bins is not None and (bins.inside, bins.total) == (2, 3)
    assert page.view.sizes_box.isChecked()
    page._forget_targets()
    assert page.view._histogram is None
