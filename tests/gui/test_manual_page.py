"""Manual pick's Run step (REDESIGN §6.6, #18, #59, #60): a left click
moves the camera, By hand aspirates under the crosshair, a right click
marks a point, Look at, past picks, the tip gauge and the sizes after
Analyze the dish."""

import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import DeckModule                   # noqa: E402
from micropick.core.deck import slot_center                      # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.detector import Detection                     # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.point_list import BAD                 # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402


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


@pytest.fixture(autouse=True)
def _shared_waits(request):
    """The conftest's `robot_free` and `read_pose`, for this file's helpers,
    which take no fixture: `robot_free(page.session, page.jog)` waits
    asserting, on `job_pending`; `read_pose(page.jog)` reads the pose for
    sure, where a bare refresh asked while the panel is busy is dropped."""
    global robot_free, read_pose
    robot_free = request.getfixturevalue("robot_free")
    read_pose = request.getfixturevalue("read_pose")


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


def test_detected_sizes_go_to_the_picture(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    page._cuboids = _detection([300, 320, 600])
    page._show_sizes()
    page._refresh()
    assert page.view._histogram.inside == 2
    assert "3 cuboids found · 2 in size" in page.view._status
    page._forget_targets()
    assert page.view._histogram is None
    assert not any("found" in line for line in page.view._status)


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


def _over_the_deck(app, page, session):
    session.robot.move_to_coordinates((150.0, 150.0, 90.0), verbose=False)
    read_pose(page.jog)


def _left_click(app, page, u, v):
    page.view.clicked.emit(u, v)
    robot_free(page.session, page.jog)


def _xy(session):
    pos = session.robot.get_position(verbose=False)[0]
    return pos["x"], pos["y"]


def test_a_single_left_click_moves_the_camera_there(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    # 100 px right of the reference pixel is 2.6 mm in x.
    _left_click(app, page, W / 2 + 100, H / 2)
    assert _xy(session) == pytest.approx((150.0 + 100 * K, 150.0), abs=0.05)


def test_a_click_reads_no_pose_on_the_gui_thread(app, page, session,
                                                 monkeypatch):
    """#36: a pose read is a robot command, and on the GUI thread the
    window and its picture waited on it. The click takes the pose the
    panel shows; the job reads one to see the tip is at the travel
    height and one to verify the move, and the panel's line is made from
    that last one, not from a read of its own."""
    import threading
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    robot, reads = session.robot, []
    real = robot.get_position

    def get_position(*args, **kwargs):
        thread = threading.current_thread().name
        if not thread.startswith("grab-"):     # the mock camera's scene
            reads.append(thread)
        return real(*args, **kwargs)

    monkeypatch.setattr(robot, "get_position", get_position)
    _left_click(app, page, W / 2 + 100, H / 2)
    assert len(reads) == 2
    assert "MainThread" not in reads
    assert page.jog.pose[0] == pytest.approx(150.0 + 100 * K, abs=0.05)


def _cuboid_at(page, u, v, half=20):
    """A detected cuboid, a square of side 2 * `half` px centered on (u, v),
    as Analyze the dish would leave it."""
    from micropick.core.calibration.pixel_map import PixelMap
    contour = np.array([[u - half, v - half], [u + half, v - half],
                        [u + half, v + half], [u - half, v + half]],
                       dtype=np.int32).reshape(-1, 1, 2)
    df = pd.DataFrame({"contour": [contour], "cX": [float(u)],
                       "cY": [float(v)], "diameter_microns": [300.0]})
    empty = df.iloc[0:0]
    detection = Detection(np.empty((0, 4)), np.empty(0), df, empty, empty,
                          empty, {}, classified=False, standin=False)
    pmap = PixelMap.from_config(page.session.profile.calibration.pixel_map)
    world = pmap.to_robot(u, v, np.array(page.jog.pose[:2]))
    page._cuboids_found(((H, W, 3), detection, np.array([world]),
                         np.array([True])))


def test_a_click_on_a_cuboid_moves_only_the_camera(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.source.buttons[2].click()                     # In a well
    _cuboid_at(page, W / 2 + 100, H / 2)
    assert page.view._overlay                           # the cuboid is drawn
    _left_click(app, page, W / 2 + 100, H / 2)
    pos = session.robot.get_position(verbose=False)[0]
    assert (pos["x"], pos["y"]) == pytest.approx((150.0 + 100 * K, 150.0),
                                                 abs=0.05)
    # Never down to the pickup height: the camera at the travel height.
    assert pos["z"] > session.profile.picking.pickup_height + 10


def test_no_click_choice_and_no_way_back_to_center(page):
    from PySide6.QtWidgets import QAbstractButton, QLabel
    texts = [w.text() for w in page.findChildren(QLabel)
             + page.findChildren(QAbstractButton) if w.isVisibleTo(page)]
    assert not [t for t in texts if "back to center" in t
                or "A click moves" in t]


def test_a_click_outside_the_jog_envelope_moves_nothing(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    session.robot.move_to_coordinates((20.0, 20.0, 90.0), verbose=False)
    read_pose(page.jog)
    before = _xy(session)
    # Far to the left and up of the deck's corner: past the soft limits.
    _left_click(app, page, 110.0, 110.0)
    assert _xy(session) == pytest.approx(before)
    said = toast_of(page)
    assert said.text().startswith("Not reachable: ")
    assert said.text().endswith(".")
    assert said.property("tone") == "refusal"
    assert len(page.jog.position_lines) == 1


def test_a_click_while_the_robot_is_busy_elsewhere_moves_nothing(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    before = _xy(session)
    lease = session.robot_lease.claim("someone else")
    try:
        page.view.clicked.emit(W / 2 + 100, H / 2)      # refused, never queued
    finally:
        lease.release()
    robot_free(session, page.jog)
    assert _xy(session) == pytest.approx(before)


def _rings(page):
    """The rings round the chosen point, the one mark wider than a point."""
    from micropick.viz import overlays
    from micropick.gui.position_marks import RADIUS_PX
    return len([i for i in page.view._marks if isinstance(i, overlays.Circle)
                and i.radius > RADIUS_PX])


def _points_drawn(page):
    from micropick.viz import overlays
    from micropick.gui.position_marks import MEMO, RADIUS_PX
    return [i for i in page.view._marks
            if isinstance(i, overlays.Circle) and i.color == MEMO
            and i.radius == RADIUS_PX]


def test_a_right_click_marks_a_point_drawn_only_while_points_are_shown(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    before = _xy(session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    robot_free(page.session, page.jog)
    assert [p.name for p in session.memo_points] == ["M1"]
    assert _xy(session) == pytest.approx(before)        # marking moves nothing
    assert page.points_list.chosen_name == "M1"
    assert len(_points_drawn(page)) == 1


def test_the_points_chip_alone_says_whether_points_are_drawn(
        app, page, session):
    """On at start, whatever is chosen below; turned off, a new point is
    listed and still not drawn (§10.14)."""
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.source.buttons[2].click()                     # In a well
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    robot_free(page.session, page.jog)
    assert page.view.marks_box.isChecked()
    assert len(_points_drawn(page)) == 1
    page.view.marks_box.setChecked(False)
    assert _points_drawn(page) == []
    page.view.right_clicked.emit(W / 2 - 100, H / 2)
    robot_free(page.session, page.jog)
    assert not page.view.marks_box.isChecked()
    assert page.points_list.names == ["M1", "M2"]
    assert _points_drawn(page) == []


def test_the_chosen_point_is_ringed_on_the_picture(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    robot_free(page.session, page.jog)
    assert _rings(page) == 1
    page.points_list.choose(None)
    assert _rings(page) == 0


def test_a_new_point_is_chosen_in_the_list_and_said(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    robot_free(page.session, page.jog)
    assert page.points_list.chosen_name == "M1"
    assert page.points_list.row("M1").isAncestorOf(page.points_list.actions)
    assert _said(page) == ("M1 added.", "answer")


def test_a_point_is_deleted_under_its_row(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    page.view.right_clicked.emit(W / 2 - 100, H / 2)
    robot_free(page.session, page.jog)
    assert [p.name for p in session.memo_points] == ["M1", "M2"]
    page.points_list.row("M1").delete.click()
    assert [p.name for p in session.memo_points] == ["M2"]
    assert len(_points_drawn(page)) == 1


def test_look_at_moves_the_camera_over_the_slot(app, page, session):
    from micropick.core.deck import slot_center
    _calibrate(session)
    robot_free(page.session, page.jog)
    page.look_at.slot_chosen.emit(5)
    robot_free(page.session, page.jog)
    x, y = slot_center(5)
    # The slot's center is the tip's frame: the gantry stops a pipette
    # offset short of it.
    assert _xy(session) == pytest.approx((x - 10.0, y + 5.0), abs=0.05)


def _tip_on(app, page, session):
    from micropick.config.labware import LabwareDefinition
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")
    assert session.tip.attached is True
    # the panel reads the pose again for the tip
    robot_free(page.session, page.jog)


def test_the_tip_gauge_follows_aspirate_and_dispense(app, page, session):
    _tip_on(app, page, session)
    page.in_place_amount.setValue(15.0)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    assert page.gauge.volume.text() == "15 µl"
    session.read_tip()                       # the same tip, read again
    assert page.gauge.volume.text() == "15 µl"
    page.amount.buttons[1].click()                       # All
    page._dispense_in_place()
    robot_free(page.session, page.jog)
    assert page.gauge.volume.text() == "0 µl"
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    session.drop_tip_in_trash()
    app.processEvents()
    assert page.gauge.volume.text() == "0 µl"           # dropped with the tip


def test_aspirates_summary_says_the_height_amount_speed_and_lift(page):
    page.above_bottom.setValue(0.5)
    page.volume.setValue(15.0)
    page.flow_rate.setValue(50.0)
    page.hand_z.setValue(70.0)
    # By clicking without a dish bottom in this robot session: a Z.
    assert page.aspirate_card.summary.full == (
        "By clicking · Z 70 mm · 15 µl · 50 µl/s · lift right away")
    page.source.buttons[2].click()                       # In a well
    assert page.aspirate_card.summary.full == (
        "In a well · 15 µl · 50 µl/s · lift right away")


def _ready_by_hand(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _tip_on(app, page, session)
    _over_the_deck(app, page, session)
    page.source.buttons[0].click()                     # By clicking
    page.volume.setValue(15.0)
    page.above_bottom.setValue(0.5)


def _aspirated_at(session):
    """Where the tip was at each aspirate, (x, y, z)."""
    robot, at = session.robot, []
    aspirate = robot.aspirate_in_place

    def record(*args, **kwargs):
        pos = robot.get_position(verbose=False)[0]
        at.append((pos["x"], pos["y"], pos["z"]))
        return aspirate(*args, **kwargs)
    robot.aspirate_in_place = record
    return at


def _pose(session):
    pos = session.robot.get_position(verbose=False)[0]
    return pos["x"], pos["y"], pos["z"]


def _dispense_at_a_point(page, session, x, y, z):
    """Dispense's At a point, on a new point at (x, y, z)."""
    point = session.add_memo_point(x, y, z)
    page.target.buttons[1].click()
    page.target_points.choose(point.name)


def test_by_hand_aspirate_is_enabled_with_a_tip_on_and_no_click(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    page.source.buttons[0].click()                     # By clicking
    assert not page.aspirate_button.isEnabled()         # no tip yet
    _tip_on(app, page, session)
    app.processEvents()
    assert page.aspirate_button.isEnabled()
    lease = session.robot_lease.claim("someone else")
    try:
        page._refresh()
        assert not page.aspirate_button.isEnabled()     # the robot is not free
    finally:
        lease.release()


def test_by_hand_aspirates_under_the_crosshair_and_comes_back(
        app, page, session):
    _ready_by_hand(app, page, session)
    at = _aspirated_at(session)
    before = _pose(session)
    page.height_mode.buttons[0].click()                 # Z
    page.hand_z.setValue(70.0)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    # The crosshair is the picture's center: the camera's point, and the
    # tip the pipette offset from it.
    assert at == [pytest.approx((150.0 + 10.0, 150.0 - 5.0, 70.0), abs=0.05)]
    assert session.in_tip == pytest.approx(15.0)
    assert len(page._picks) == 1                         # a past pick
    assert _pose(session) == pytest.approx(before, abs=0.05)


def test_by_hand_above_the_bottom_only_with_a_bottom_this_session(
        app, page, session):
    _ready_by_hand(app, page, session)
    above = page.height_mode.buttons[1]
    assert not above.isEnabled()
    assert page.height_mode.current() == 0               # Z
    session.profile.positions["observe"] = [150.0, 150.0, 90.0]
    session.confirm("dish_bottom")
    app.processEvents()
    assert above.isEnabled()
    assert page.height_mode.current() == 1               # the default then
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    bottom = session.profile.picking.dish_bottom
    assert at[0][2] == pytest.approx(bottom + 0.5, abs=0.05)


def test_by_hand_snap_puts_the_crosshair_on_the_cuboid(app, page, session):
    _ready_by_hand(app, page, session)
    assert page.snap.isChecked()
    _cuboid_at(page, W / 2 + 100, H / 2 + 40)
    # Clicked 8 px off the cuboid's center, inside its outline.
    _left_click(app, page, W / 2 + 108, H / 2 + 40)
    assert _xy(session) == pytest.approx((150.0 + 100 * K, 150.0 + 40 * K),
                                         abs=0.01)
    assert session.in_tip == 0.0                         # aimed, not taken


def test_by_hand_a_click_beside_every_cuboid_moves_to_the_point(
        app, page, session):
    _ready_by_hand(app, page, session)
    _cuboid_at(page, W / 2 + 100, H / 2 + 40)
    _left_click(app, page, W / 2 - 200, H / 2)
    assert _xy(session) == pytest.approx((150.0 - 200 * K, 150.0), abs=0.01)


def test_by_hand_aspirate_as_soon_as_i_click(app, page, session):
    _ready_by_hand(app, page, session)
    page.aspirate_on_click.setChecked(True)
    page.height_mode.buttons[0].click()                 # Z
    page.hand_z.setValue(70.0)
    at = _aspirated_at(session)
    _left_click(app, page, W / 2 + 100, H / 2)
    aimed = (150.0 + 100 * K, 150.0)
    assert at == [pytest.approx((aimed[0] + 10.0, aimed[1] - 5.0, 70.0),
                                abs=0.05)]
    assert session.in_tip == pytest.approx(15.0)
    # The camera back over the spot it aimed at.
    assert _xy(session) == pytest.approx(aimed, abs=0.05)


def test_part_is_chosen_when_the_page_opens(app, session):
    """#99: the run's volume, not all of the tip, unless All is chosen."""
    session.profile.picking.vol = 7.0
    page = ManualPage(session)
    assert [b.text() for b in page.amount.buttons] == ["Part", "All"]
    assert page.amount.buttons[0].isChecked()
    assert not page.part_row.isHidden()
    assert page.part.value() == pytest.approx(7.0)
    page.deleteLater()


def test_dispense_is_gray_until_the_tip_holds_and_gives_back_part_then_all(
        app, page, session):
    _tip_on(app, page, session)
    assert not page.dispense_button.isEnabled()          # the tip is empty
    page.in_place_amount.setValue(15.0)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    assert page.dispense_button.isEnabled()
    page.part.setValue(6.0)                              # Part, from the start
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(9.0)
    page.amount.buttons[1].click()                       # All
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0
    assert not page.dispense_button.isEnabled()


def test_liquid_in_place_and_a_right_click_move_no_card(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _tip_on(app, page, session)
    _over_the_deck(app, page, session)
    page.resize(1600, 900)
    app.processEvents()

    def where():
        return [c.geometry() for c in (page.aspirate_card, page.dispense_card)]

    before = where()
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    assert session.in_tip > 0
    assert where() == before
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    robot_free(page.session, page.jog)
    assert where() == before
    assert len(_points_drawn(page)) == 1                 # and it is seen


def test_dispense_in_a_well_says_the_well_and_goes_there(app, page, session):
    from micropick.config.labware import resolve_definition
    plate = session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    _tip_on(app, page, session)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    page.target.buttons[0].click()                       # In a well
    page.target_wells.map.well_clicked.emit("A7")
    assert page.dispense_button.text() == "Dispense into A7"
    robot_free(page.session, page.jog)                 # a busy panel refuses
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    calls = session.robot.calls
    lid = session.run_state.labware["5"].labware_id
    assert ("move_to_well", lid, "A7", "bottom") in calls
    assert calls[-1][0] == "dispense_in_place"
    assert session.in_tip == 0.0
    del plate


def test_the_plan_map_chooses_the_well_to_dispense_into(app, page, session):
    _held(app, page, session)
    from micropick.config.labware import resolve_definition
    from micropick.core.routine import Destination, Routine
    definition = resolve_definition("corning_96_wellplate_360ul_flat")
    session.load_labware(definition, 5)
    destination = Destination.from_definition(definition, 5)
    session.set_routine(Routine(destination, {"A7": 1, "B7": 1}, name="t"))
    page.target.buttons[0].click()
    page.target_wells.map.well_clicked.emit("B7")
    assert page.target_wells.chosen()[3] == "B7"
    assert page.dispense_button.text() == "Dispense into B7"


def test_no_counter_finish_or_step_numbers(page):
    import re
    from PySide6.QtWidgets import QAbstractButton, QLabel
    texts = [w.text() for w in page.findChildren(QLabel)
             + page.findChildren(QAbstractButton)]
    assert not [t for t in texts
                if "Finish" in t or "moved" in t
                or re.match(r"^\s*\d+\s*/\s*\d+", t)]


def test_past_picks_are_drawn_while_shown(app, page, session):
    from micropick.viz import overlays
    from micropick.gui.position_marks import PICK
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
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


def _mouse_click(app, view, u, v):
    """A real left click on the picture at sensor (u, v), the
    double-click interval run out."""
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent
    end = time.monotonic() + 5
    while view.held_frame is None and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    point = view.sensor_to_widget(u, v)
    for kind, buttons in ((QMouseEvent.Type.MouseButtonPress,
                           Qt.MouseButton.LeftButton),
                          (QMouseEvent.Type.MouseButtonRelease,
                           Qt.MouseButton.NoButton)):
        app.sendEvent(view, QMouseEvent(kind, point, view.mapToGlobal(point),
                                        Qt.MouseButton.LeftButton, buttons,
                                        Qt.KeyboardModifier.NoModifier))
    view._click_timer.setInterval(0)
    if view._click_timer.isActive():
        view._click_timer.start(0)
    app.processEvents()


def test_without_a_pixel_map_a_click_moves_nothing(app, page, session):
    session.profile.calibration.pixel_map = None
    _open_upper(app, page, session)
    _over_the_deck(app, page, session)
    page.source.buttons[2].click()                     # In a well
    clicks = []
    page.view.clicked.connect(lambda u, v: clicks.append((u, v)))
    _mouse_click(app, page.view, 400, 300)
    assert clicks == []
    assert page.view.mouse_help[0] == "Use the arrows to move the camera."


def test_without_a_pixel_map_a_click_by_hand_moves_nothing(
        app, page, session):
    session.profile.calibration.pixel_map = None
    _open_upper(app, page, session)
    _over_the_deck(app, page, session)
    page.source.buttons[0].click()                     # By clicking
    clicks = []
    page.view.clicked.connect(lambda u, v: clicks.append((u, v)))
    _mouse_click(app, page.view, 400, 300)
    assert clicks == []


# -- saved points (#61, #62) --------------------------------------------------

def test_memo_and_saved_points_are_two_lists_both_under_at_a_point(
        app, page, session):
    from micropick.viz import overlays
    for name in ("observe", "shake", "tip_calib"):
        session.remember(name, (150.0, 150.0, 80.0))
    session.remember("trough", (150.0, 150.0, 30.0))
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    robot_free(page.session, page.jog)
    for points in (page.points_list, page.source_points, page.target_points):
        assert points.names == ["M1", "trough"]
    assert page.points_list.row("M1").star.text() == "☆"
    assert page.points_list.row("trough").star.text() == "★"
    labels = [i.text for i in page.view._marks
              if isinstance(i, overlays.Text)]
    assert "trough z 30" in labels
    assert not {"observe", "shake", "tip_calib"} & set(labels)


def test_a_saved_point_is_drawn_cyan_with_a_filled_mark(app, page, session):
    from micropick.viz import overlays
    from micropick.gui.position_marks import MEMO
    session.remember("trough", (150.0, 150.0, 30.0))
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    filled = [i for i in page.view._marks if isinstance(i, overlays.Circle)
              and i.color == MEMO and i.fill]
    assert len(filled) == 1
    session.add_memo_point(150.0, 150.0, None)
    filled = [i for i in page.view._marks if isinstance(i, overlays.Circle)
              and i.color == MEMO and i.fill]
    assert len(filled) == 1                         # the memo point is not
    assert len(_points_drawn(page)) == 2            # a mark each


def test_the_chosen_point_is_the_lists(page, session):
    session.remember("trough", (150.0, 150.0, 30.0))
    session.add_memo_point(100.0, 100.0, None)
    page.points_list.choose("M1")
    assert page.chosen_point() == "M1"
    page.points_list.choose("trough")
    assert page.points_list.chosen_name == "trough"
    assert page.chosen_point() == "trough"
    page.points_list.choose("M1")
    assert page.chosen_point() == "M1"


def test_camera_to_brings_the_camera_over_the_point_at_the_travel_height(
        app, page, session):
    _calibrate(session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    session.remember("trough", (120.0, 130.0, 30.0))
    page.points_list.choose("trough")
    assert page.camera_to_button.isEnabled()
    page.camera_to_button.click()
    robot_free(page.session, page.jog)
    # The gantry a pipette offset short of the point; the Z is not used.
    assert _xy(session) == pytest.approx((110.0, 135.0), abs=0.05)
    z = session.robot.get_position(verbose=False)[0]["z"]
    assert z > 30.0


def test_tip_to_is_gray_without_a_tip_and_goes_down_to_the_points_z(
        app, page, session):
    _calibrate(session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    session.remember("trough", (120.0, 130.0, 30.0))
    page.points_list.choose("trough")
    assert not page.tip_to_button.isEnabled()
    assert page.tip_to_button.toolTip() == "Put a tip on first."
    _tip_on(app, page, session)
    page.points_list.choose("trough")
    assert page.tip_to_button.isEnabled()
    page.tip_to_button.click()
    robot_free(page.session, page.jog)
    pos = session.robot.get_position(verbose=False)[0]
    assert (pos["x"], pos["y"], pos["z"]) == pytest.approx(
        (120.0, 130.0, 30.0), abs=0.05)


def test_place_memo_at_crosshair_marks_the_spot_under_it_with_no_z(
        app, page, session):
    assert not page.place_memo_button.isEnabled()
    assert page.place_memo_button.toolTip() == "Calibrate the camera first."
    _calibrate(session)
    session.profile_changed.emit(session.profile)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    assert page.place_memo_button.isEnabled()
    page.place_memo_button.click()
    # The crosshair is the reference pixel: the gantry plus the tip offset.
    [point] = session.memo_points
    assert (point.x, point.y, point.z) == pytest.approx((160.0, 145.0, None))
    assert page.points_list.chosen_name == point.name


def test_tip_to_crosshair_lands_under_it_at_the_travel_height(
        app, page, session):
    """#99: the tip where By hand aims, and it stays there."""
    _calibrate(session)
    session.profile_changed.emit(session.profile)
    _tip_on(app, page, session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    assert page.tip_crosshair_button.isEnabled()
    page.tip_crosshair_button.click()
    robot_free(page.session, page.jog)
    pos = session.robot.get_position(verbose=False)[0]
    # The crosshair is the reference pixel: the gantry plus the tip offset.
    assert (pos["x"], pos["y"]) == pytest.approx((160.0, 145.0), abs=0.05)
    assert pos["z"] == pytest.approx(session.motion.travel_z, abs=0.05)
    assert toast_of(page).property("tone") == "answer"


def test_tip_to_crosshair_is_checked_at_the_height_it_moves_at(
        app, page, session):
    """A tip low beside a platform can go over it: the move is at the
    travel height, not where the tip is now."""
    from micropick.config.schema import PipetteOffset
    _calibrate(session)
    session.profile.calibration.pipette_offset = PipetteOffset(dx=100.0,
                                                               dy=0.0)
    session.set_deck_modules(
        [DeckModule(slots=[8], offset=[0, 0, 64.0], name="Platform")])
    _tip_on(app, page, session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    x, y = slot_center(8)[:2]
    session.robot.move_to_coordinates((x - 100.0, y, 30.0), verbose=False)
    read_pose(page.jog)
    page.tip_crosshair_button.click()
    robot_free(page.session, page.jog)
    assert _xy(session) == pytest.approx((x, y), abs=0.05)


def test_tip_to_crosshair_is_gray_without_a_tip_or_a_pixel_map(
        app, page, session):
    button = page.tip_crosshair_button
    _tip_on(app, page, session)
    assert not button.isEnabled()
    assert button.toolTip() == "Calibrate the camera first."
    _calibrate(session)
    session.profile_changed.emit(session.profile)
    assert button.isEnabled()
    session.drop_tip_in_place()
    robot_free(page.session, page.jog)
    assert not button.isEnabled()
    assert button.toolTip() == "Put a tip on first."


def test_tip_to_crosshair_is_gray_while_the_robot_is_busy(app, page, session):
    page.show()                 # a hidden page reads the lease once shown
    _calibrate(session)
    session.profile_changed.emit(session.profile)
    _tip_on(app, page, session)
    lease = session.robot_lease.claim("Picking", run=True)
    app.processEvents()
    assert not page.tip_crosshair_button.isEnabled()
    assert page.tip_crosshair_button.toolTip() == "Robot busy: Picking."
    lease.release()


def test_tip_to_crosshair_says_why_a_spot_cannot_be_reached(
        app, page, session):
    _calibrate(session)
    session.profile_changed.emit(session.profile)
    _tip_on(app, page, session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    session.robot.move_to_coordinates((375.0, 150.0, 90.0), verbose=False)
    read_pose(page.jog)
    page.tip_crosshair_button.click()
    robot_free(page.session, page.jog)
    assert _xy(session) == pytest.approx((375.0, 150.0), abs=0.05)
    assert toast_of(page).text().startswith("Not reachable: X 385")
    assert toast_of(page).property("tone") == "refusal"


def test_the_star_is_enabled_for_a_point_at_the_travel_height(
        app, page, session):
    session.add_memo_point(100.0, 100.0, None)
    assert page.points_list.row("M1").star.isEnabled()


def test_space_no_longer_saves_a_point(page, session):
    from micropick.gui import keys
    assert "save" not in {a.id for a in keys.ACTIONS}
    page.jog.pad.keys.click()
    assert "save point" not in page.jog.pad.key_list.text().lower()


def test_a_wells_offset_does_not_read_a_measurement_of_another_height(page):
    """#92: a well's offset is the plate's center only on the module height
    it was measured on."""
    session = page.session
    plate = "corning_96_wellplate_360ul_flat"
    definition = resolve_definition(plate)
    session.load_labware(definition, 5)
    session.profile.picking.well_offset_x = 0.0
    session.profile.picking.well_offset_y = 0.0
    session.set_well_center(5, plate, (0.4, -0.3, -1.5), "B2", depth_mm=10.0)
    assert page._well_offset("5", definition, 1.0) == \
        pytest.approx((0.4, -0.3, 1.0))
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2])])
    assert page._well_offset("5", definition, 1.0) == \
        pytest.approx((0.0, 0.0, 1.0))


# -- In a well: the plate's measured bottom (#89) ---------------------------

PLATE = "corning_96_wellplate_360ul_flat"


def _measured_plate(app, page, session):
    """A plate in slot 5, measured: 0.4, -0.3 off the drawing's center, the
    rim 1 mm under it, 9 mm deep."""
    session.load_labware(resolve_definition(PLATE), 5)
    session.profile.picking.well_offset_x = 0.0
    session.profile.picking.well_offset_y = 0.0
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    _tip_on(app, page, session)
    page.source.buttons[2].click()                       # In a well
    page.source_wells.map.well_clicked.emit("A7")
    page.above_bottom.setValue(0.5)
    robot_free(page.session, page.jog)


def test_aspirate_in_a_measured_plate_reaches_the_rim_less_depth_plus_height(
        app, page, session):
    _measured_plate(app, page, session)
    at = _aspirated_at(session)
    z = _pose(session)[2]              # the mock stays here: over the rim
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    # Over the rim by the approach, at the measured x and y ...
    assert session.robot.well_offset == pytest.approx((0.4, -0.3, 1.0))
    # ... then straight down: 2 mm to the rim, 9 to the bottom, 0.5 up.
    assert at[-1][2] == pytest.approx(z - 2.0 - 9.0 + 0.5, abs=0.01)


def test_aspirate_prepares_the_plunger_over_the_rim_not_in_the_well(
        app, page, session):
    _measured_plate(app, page, session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    names = [c[0] for c in session.robot.calls]
    well = len(names) - 1 - names[::-1].index("move_to_well")
    assert names[well + 1] == "prepare_to_aspirate"


def test_dispense_in_a_measured_plate_reaches_the_deposit_height(
        app, page, session):
    _measured_plate(app, page, session)
    page.well_offset.setValue(1.5)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    _lift(page.dispense_retract, "Stay")
    page.target.buttons[0].click()                       # In a well
    page.target_wells.map.well_clicked.emit("A7")
    robot_free(page.session, page.jog)
    z = _pose(session)[2]
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.robot.well_offset == pytest.approx((0.4, -0.3, 1.0))
    assert _pose(session)[2] == pytest.approx(z - 2.0 - 9.0 + 1.5, abs=0.01)
    assert session.robot.calls[-1][0] == "dispense_in_place"


def _well_moves(session):
    return [c for c in session.robot.calls if c[0] == "move_to_well"]


def test_a_plate_measured_in_an_earlier_session_takes_its_measured_bottom(
        app, page, session):
    _measured_plate(app, page, session)
    session.new_run()                        # nothing pressed in this one
    session.load_labware(resolve_definition(PLATE), 5)
    page.source_wells.map.well_clicked.emit("A7")
    robot_free(page.session, page.jog)
    assert "above the measured bottom" in page.aspirate_card.summary.full
    at = _aspirated_at(session)
    z = _pose(session)[2]              # the mock stays here: over the rim
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert session.robot.well_offset == pytest.approx((0.4, -0.3, 1.0))
    assert at[-1][2] == pytest.approx(z - 2.0 - 9.0 + 0.5, abs=0.01)


def test_a_plate_not_measured_takes_the_drawings_bottom(app, page, session):
    session.load_labware(resolve_definition(PLATE), 5)
    session.profile.picking.well_offset_x = 0.0
    session.profile.picking.well_offset_y = 0.0
    _tip_on(app, page, session)
    page.source.buttons[2].click()
    page.source_wells.map.well_clicked.emit("A7")
    page.above_bottom.setValue(0.5)
    robot_free(page.session, page.jog)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert _well_moves(session)[-1][3] == "bottom"
    assert session.robot.well_offset == pytest.approx((0.0, 0.0, 0.5))


def test_the_summary_names_the_bottom_and_follows_the_measurement(
        app, page, session):
    session.load_labware(resolve_definition(PLATE), 5)
    page.source.buttons[2].click()
    page.source_wells.map.well_clicked.emit("A7")
    page.above_bottom.setValue(0.5)
    assert page.aspirate_card.summary.full.startswith(
        "Slot 5 · A7 · 0.5 mm above the drawing's bottom · ")
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    assert page.aspirate_card.summary.full.startswith(
        "Slot 5 · A7 · 0.5 mm above the measured bottom · ")


def test_the_summary_follows_the_plate_chosen(app, page, session):
    session.load_labware(resolve_definition(PLATE), 5)
    session.load_labware(resolve_definition(PLATE), 6)
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    page.source.buttons[2].click()
    page.source_wells.plate.slot_chosen.emit(5)
    assert "measured bottom" in page.aspirate_card.summary.full
    page.source_wells.plate.slot_chosen.emit(6)
    assert "drawing's bottom" in page.aspirate_card.summary.full


def test_the_summary_goes_back_when_the_module_height_changes(
        app, page, session):
    """#92: a measurement on another height is not the plate's."""
    session.load_labware(resolve_definition(PLATE), 5)
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    page.source.buttons[2].click()
    assert "measured bottom" in page.aspirate_card.summary.full
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2])])
    assert "drawing's bottom" in page.aspirate_card.summary.full


def test_by_hand_height_is_above_the_bottom(page):
    page.source.buttons[0].click()                       # By clicking
    labels = [b.text() for b in page.height_mode.buttons]
    assert labels == ["Z", "Above the bottom"]


# -- By hand: the bottom of the spot's slot, never beside a well (#90) --------
# The tip offset is (10, -5). The plate of `_measured_plate` is in slot 5,
# whose corner is (132.5, 90.5): A1 at (146.87, 164.74) on the drawing, and at
# (147.27, 164.44) measured, 6.858 across. Its rim is 1 mm under the drawing's
# 14.22, so the bottom is 14.22 - 1 - 9 = 4.22.

A1 = (147.27, 164.44)
PLATE_BOTTOM = 4.22


def _aim_at(app, page, session, x, y):
    """The crosshair over (x, y): the tip would go down there."""
    z = _pose(session)[2]
    session.robot.move_to_coordinates((x - 10.0, y + 5.0, z), verbose=False)
    read_pose(page.jog)


def _by_hand_on_a_plate(app, page, session):
    """By hand, the crosshair on A1 of the measured plate, at a safe Z."""
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    page.source.buttons[0].click()                       # By clicking
    page.volume.setValue(15.0)
    _aim_at(app, page, session, *A1)


def _dish_in_slot_8(session):
    """The camera over the dish in slot 8 and its bottom confirmed."""
    session.profile.positions["observe"] = [186.0, 229.0, 90.0]
    session.profile.picking.dish_bottom = 66.0
    session.confirm("dish_bottom")


def test_by_hand_over_a_measured_plate_goes_above_its_bottom(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    assert page.height_mode.current() == 1               # the default then
    assert page.aspirate_card.summary.full.startswith(
        "By clicking · 0.5 mm above the bottom of slot 5 · ")
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at[0] == pytest.approx((*A1, PLATE_BOTTOM + 0.5), abs=0.05)


def test_by_hand_the_bottom_follows_the_spot_from_slot_to_slot(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    _dish_in_slot_8(session)
    _aim_at(app, page, session, 196.0, 224.0)            # slot 8
    assert page.aspirate_card.summary.full.startswith(
        "By clicking · 0.5 mm above the bottom of slot 8 · ")
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at[0][2] == pytest.approx(66.5, abs=0.05)
    _aim_at(app, page, session, *A1)
    assert page.aspirate_card.summary.full.startswith(
        "By clicking · 0.5 mm above the bottom of slot 5 · ")


def test_by_hand_over_a_slot_with_no_bottom_is_a_z(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    above = page.height_mode.buttons[1]
    assert above.isEnabled()
    _aim_at(app, page, session, 64.0, 133.0)             # slot 4: nothing
    assert not above.isEnabled()
    assert page.height_mode.current() == 0               # Z
    assert page.aspirate_card.summary.full.startswith("By clicking · Z ")
    _aim_at(app, page, session, *A1)
    assert above.isEnabled()
    assert page.height_mode.current() == 1


def test_by_hand_the_dish_bottom_is_only_the_dishs_slots(app, page, session):
    _ready_by_hand(app, page, session)                   # over slot 5
    _dish_in_slot_8(session)
    app.processEvents()
    assert not page.height_mode.buttons[1].isEnabled()


def test_by_hand_a_plate_measured_in_an_earlier_session_has_its_bottom(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    session.new_run()                        # nothing pressed in this one
    session.load_labware(resolve_definition(PLATE), 5)
    _aim_at(app, page, session, *A1)         # a new session homed the robot
    assert page.height_mode.buttons[1].isEnabled()
    assert page.height_mode.current() == 1


def test_by_hand_a_plate_not_measured_offers_only_z(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    session.forget_well_center(5, PLATE)
    app.processEvents()
    assert not page.height_mode.buttons[1].isEnabled()
    assert page.height_mode.current() == 0


def test_by_hand_a_move_in_flight_keeps_the_choice_of_z(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    page.height_mode.buttons[0].click()                  # Z, chosen
    page.snap.setChecked(False)
    _left_click(app, page, W / 2 + 9.0 / K, H / 2)       # to A2: a job
    assert page.jog.pose[0] == pytest.approx(A1[0] + 9.0 - 10.0, abs=0.05)
    assert page.height_mode.current() == 0


def test_by_hand_a_spot_on_the_wall_is_refused_and_nothing_moves(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    _aim_at(app, page, session, A1[0] + 4.5, A1[1])      # between A1 and A2
    before, calls = _pose(session), len(session.robot.calls)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert toast_of(page).text() == "The spot is not in a well."
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)
    assert not [c for c in session.robot.calls[calls:]
                if c[0] in ("move_to_coordinates", "move_relative")]


def test_by_hand_a_click_on_the_wall_is_refused_as_soon_as_i_click(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    page.snap.setChecked(False)
    page.aspirate_on_click.setChecked(True)
    before = _pose(session)
    # 4.5 mm to the right of the crosshair: the wall between A1 and A2.
    _left_click(app, page, W / 2 + 4.5 / K, H / 2)
    assert toast_of(page).text() == "The spot is not in a well."
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)


def test_by_hand_a_click_into_a_slot_without_a_bottom_is_refused(
        app, page, session):
    """The line said above a bottom: a click to where there is none does
    not go down at whatever Z is in the field."""
    _by_hand_on_a_plate(app, page, session)
    page.snap.setChecked(False)
    page.aspirate_on_click.setChecked(True)
    before = _pose(session)
    _left_click(app, page, W / 2 - 20.0 / K, H / 2)      # over slot 4
    assert toast_of(page).text() == "No bottom is known in slot 4."
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)


def test_by_hand_z_is_not_refused_on_the_wall(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    _aim_at(app, page, session, A1[0] + 4.5, A1[1])
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(20.0)
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at[0] == pytest.approx((A1[0] + 4.5, A1[1], 20.0), abs=0.05)


# -- what the page says: the toast, in sentences (#94) -----------------------

BELOW = "Z 50 is below the platform in slot 8"


def _said(page):
    toast = toast_of(page)
    return toast.text(), toast.property("tone")


def _point_below_the_platform(app, page, session):
    """M1 over slot 8's platform, 14 mm under its top, chosen."""
    session.set_deck_modules(
        [DeckModule(slots=[8], offset=[0, 0, 64.0], name="Platform")])
    x, y = slot_center(8)
    point = session.add_memo_point(x, y, 50.0)
    page.points_list.choose(point.name)
    app.processEvents()
    return point


def test_a_point_below_a_platform_is_refused_in_a_sentence(app, page, session):
    _calibrate(session)
    _tip_on(app, page, session)
    _point_below_the_platform(app, page, session)
    before = _pose(session)
    page.tip_to_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == (f"Not reachable: {BELOW}.", "refusal")
    assert len(page.jog.position_lines) == 1


def test_a_point_marked_below_a_platform_says_so_and_its_z_stays_red(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    session.set_deck_modules(
        [DeckModule(slots=[8], offset=[0, 0, 64.0], name="Platform")])
    x, y = slot_center(8)
    session.robot.move_to_coordinates((x, y, 90.0), verbose=False)
    read_pose(page.jog)
    page.points_card.set_last_z(50.0)
    page.points_card.mark(W / 2, H / 2)
    point = session.memo_points[-1]
    assert _said(page) == (f"{point.name} is not reachable: {BELOW}.",
                           "refusal")
    z_cell = page.points_list.row(point.name).cells["z"]
    assert BAD in z_cell.styleSheet()
    assert z_cell.toolTip() == BELOW


def test_a_memo_point_that_can_be_reached_is_an_answer(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.points_card.mark(W / 2, H / 2)
    text, tone = _said(page)
    assert text == "M1 added."
    assert tone == "answer"


def test_nothing_in_the_tip_is_refused_in_a_sentence(app, page, session):
    _tip_on(app, page, session)
    page._dispense(("here", None))
    assert _said(page) == ("Nothing is in the tip.", "refusal")


def test_more_than_the_tip_holds_is_refused_in_a_sentence(app, page, session):
    _tip_on(app, page, session)
    session.aspirate(5.0, 50.0)
    page.part.setValue(8.0)
    page._dispense(("here", None))
    assert _said(page) == ("The tip holds 5 µl.", "refusal")


def test_no_tip_is_refused_in_a_sentence(app, page, session):
    page._aspirate(("here", None), None)
    assert _said(page) == ("Put a tip on first.", "refusal")


def test_no_tip_calibration_is_refused_in_a_sentence(app, page, session):
    _calibrate(session)
    session.profile.calibration.pipette_offset = None
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.points_card.mark(W / 2, H / 2)
    assert _said(page) == ("Calibrate the tip first.", "refusal")


def test_a_right_click_outside_the_map_is_refused_in_a_sentence(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page.points_card.mark(-5000.0, -5000.0)
    assert _said(page) == ("That spot is outside the calibrated area.",
                           "refusal")


def test_a_click_without_a_map_is_refused_in_the_maps_own_words(page, session):
    page._clicked(100.0, 100.0)
    text, tone = _said(page)
    assert tone == "refusal"
    assert not text.startswith("Click")


def test_a_robot_held_is_refused_in_red(app, page, session):
    _tip_on(app, page, session)
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        page._aspirate_in_place()
    finally:
        lease.release()
    assert _said(page) == (session.robot_lease.refusal, "refusal")


def test_an_aspirate_is_answered_in_a_sentence(app, page, session):
    _tip_on(app, page, session)
    page.in_place_amount.setValue(10.0)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    assert _said(page) == ("Aspirated 10 µl here.", "answer")
    assert len(page.jog.position_lines) == 1


# -- the plate's heights on the picture (#95) -------------------------------
#
# The drawing's 96 well plate: the top of its wells 3.55 + 10.67 = 14.22 over
# the slot, their bottom 3.55. `_measured_plate`'s: 13.22 and 4.22, in slot 5.


def _camera_over(app, page, session, slot):
    """The crosshair over `slot`'s center."""
    _aim_at(app, page, session, *slot_center(slot))


def _calibrated_on_screen(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)


def test_the_height_line_gives_an_unmeasured_plate_from_its_drawing(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 14.22 · bottom 3.55 · drawing"


def test_the_height_line_stands_the_plate_on_its_module(app, page, session):
    _calibrated_on_screen(app, page, session)
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2])])
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 78.42 · bottom 67.75 · drawing"


def test_the_height_line_gives_a_measured_plate_as_measured(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    _measured_plate(app, page, session)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 13.22 · bottom 4.22 · measured"


def test_the_height_line_reads_a_measurement_of_an_earlier_session(
        app, page, session):
    """As In a well goes: a stored measurement, with nothing pressed."""
    _calibrated_on_screen(app, page, session)
    _measured_plate(app, page, session)
    session.new_run()                        # nothing pressed in this one
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 13.22 · bottom 4.22 · measured"


def test_the_height_line_says_drawing_for_a_measurement_on_another_height(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    _measured_plate(app, page, session)
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2])])
    session.new_run()
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 78.42 · bottom 67.75 · drawing"


def test_the_height_line_turns_measured_as_the_plate_is_measured(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    app.processEvents()
    assert page.view.height_line == \
        "Slot 5 · top 13.22 · bottom 4.22 · measured"


def test_the_height_line_follows_the_camera_and_an_empty_slot_has_none(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    session.load_labware(resolve_definition(PLATE), 5)
    session.load_labware(resolve_definition("corning_24_wellplate_3.4ml_flat"),
                         6)
    _camera_over(app, page, session, 5)
    assert page.view.height_line.startswith("Slot 5 · top 14.22 ")
    _camera_over(app, page, session, 6)
    assert page.view.height_line.startswith("Slot 6 · top ")
    assert "14.22" not in page.view.height_line
    _camera_over(app, page, session, 2)
    assert page.view.height_line == ""
    _camera_over(app, page, session, 5)
    assert page.view.height_line.startswith("Slot 5 · top 14.22 ")


def test_the_height_line_is_the_cameras_slot_not_the_tips(app, page, session):
    """The tip 60 mm from the camera: a slot away. Before a tip calibration
    the distance is Settings' start."""
    _calibrate(session)
    session.profile.calibration.pipette_offset.dx = 60.0
    session.load_labware(resolve_definition(PLATE), 5)
    x, y = slot_center(5)
    session.robot.move_to_coordinates((x - 60.0, y + 5.0, 90.0), verbose=False)
    read_pose(page.jog)
    assert page.view.height_line.startswith("Slot 5 ")
    session.profile.calibration.pipette_offset = None
    dx, dy = session.settings.tip_cal_start_offset
    session.robot.move_to_coordinates((x - dx, y - dy, 90.0), verbose=False)
    read_pose(page.jog)
    assert page.view.height_line.startswith("Slot 5 ")


def test_the_height_line_gives_the_source_dish_its_bottom_once_confirmed(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    session.profile.positions["observe"] = [186.0, 229.0, 90.0]
    session.profile.picking.dish_bottom = 66.0
    _camera_over(app, page, session, 8)
    assert page.view.height_line == ""
    session.confirm("dish_bottom")
    app.processEvents()
    assert page.view.height_line == "Slot 8 · bottom 66.00 · measured"
    _camera_over(app, page, session, 9)
    assert page.view.height_line == ""


def test_the_height_line_stands_during_a_move_and_goes_with_the_robot(
        app, page, session):
    import threading
    _calibrated_on_screen(app, page, session)
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    shown = page.view.height_line
    assert shown
    go_on = threading.Event()
    assert page.jog.run_job(lambda _log: go_on.wait(5) and None)
    app.processEvents()
    assert page.jog.pose is None
    assert page.view.height_line == shown
    go_on.set()
    robot_free(page.session, page.jog)
    session.disconnect_robot()
    app.processEvents()
    assert page.view.height_line == ""


# -- Dispense's own speed (#97) ---------------------------------------------


def _liquid_calls(session):
    return [c for c in session.robot.calls
            if c[0] in ("aspirate_in_place", "dispense_in_place")]


def test_in_place_uses_its_own_values_not_the_cards(app, page, session):
    _tip_on(app, page, session)
    page.in_place_amount.setValue(6.0)
    page.in_place_rate.setValue(12.0)
    page.volume.setValue(15.0)
    page.flow_rate.setValue(40.0)
    page.dispense_rate.setValue(7.0)
    page.part.setValue(2.0)
    page._aspirate_in_place()                                # A
    robot_free(page.session, page.jog)
    page._dispense_in_place()                                # D
    robot_free(page.session, page.jog)
    assert _liquid_calls(session) == [("aspirate_in_place", 6.0, 12.0),
                                      ("dispense_in_place", 6.0, 12.0)]
    assert _said(page) == ("Dispensed 6 µl here.", "answer")


def test_dispense_starts_at_the_profiles_flow_rate(app, session):
    session.profile.picking.flow_rate = 33.0
    page = ManualPage(session)
    assert page.dispense_rate.value() == 33.0
    page.deleteLater()


def test_the_dispense_speed_is_kept_in_the_page_not_the_profile(
        app, page, session):
    start = session.profile.picking.flow_rate
    page.dispense_rate.setValue(start + 5.0)
    assert session.profile.picking.flow_rate == start
    session.profile.save_picking()                       # a setting changed
    session.profile_changed.emit(session.profile)
    assert page.dispense_rate.value() == start + 5.0     # the same profile
    session.load_profile("mock")
    assert page.dispense_rate.value() == start


def test_the_dispense_button_uses_dispenses_speed(app, page, session):
    _tip_on(app, page, session)
    page.flow_rate.setValue(40.0)
    page.dispense_rate.setValue(7.0)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert _liquid_calls(session)[-1][0] == "dispense_in_place"
    assert _liquid_calls(session)[-1][2] == 7.0


def test_dispenses_summary_says_the_speed(app, page, session):
    _held(app, page, session)
    page.target.buttons[1].click()                       # At a point
    page.dispense_rate.setValue(25.0)
    assert page.dispense_card.summary.full.endswith(
        "25 µl/s · lift right away")
    page.dispense_rate.setValue(12.5)
    assert page.dispense_card.summary.full.endswith(
        "12.5 µl/s · lift right away")


def test_aspirates_summary_does_not_follow_dispenses_speed(app, page, session):
    _held(app, page, session)
    page.flow_rate.setValue(40.0)
    before = page.aspirate_card.summary.full
    page.dispense_rate.setValue(7.0)
    assert page.aspirate_card.summary.full == before
    assert "7 µl/s" in page.dispense_card.summary.full
    page.flow_rate.setValue(41.0)
    assert "7 µl/s" in page.dispense_card.summary.full


def test_dispense_reads_where_then_how_much_then_how_it_leaves(app, page):
    page.dispense_card.set_open(True)
    page.show()
    app.processEvents()

    def top(widget):
        return widget.mapTo(page, widget.rect().topLeft()).y()

    assert (top(page.target) < top(page.amount)
            < top(page.dispense_retract))


# -- a Z checked against the bottom of its slot (#101) ------------------------

DRAWN_BOTTOM = 3.55        # PLATE's drawing, on the deck of slot 5
MEASURED_BOTTOM = 4.22     # `_measured_plate`: the rim 13.22, 9 mm deep


def _point_in_slot(app, page, session, slot, z):
    """A point over `slot` at `z`, chosen."""
    x, y = slot_center(slot)
    point = session.add_memo_point(x, y, z)
    page.points_list.choose(point.name)
    app.processEvents()
    return point


def _drawn_plate(app, page, session):
    """A plate not measured in slot 5, a tip on, the tip up over the deck."""
    session.load_labware(resolve_definition(PLATE), 5)
    _tip_on(app, page, session)
    _over_the_deck(app, page, session)


def _tip_to_refused(app, page, session, said):
    before = _pose(session)
    page.tip_to_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == (f"Not reachable: {said}.", "refusal")


def _tip_to_goes(app, page, session, z):
    page.tip_to_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session)[2] == pytest.approx(z, abs=0.05)
    assert _said(page)[1] == "answer"


def test_tip_to_past_2_mm_under_a_drawings_bottom_is_refused(
        app, page, session):
    _drawn_plate(app, page, session)
    _point_in_slot(app, page, session, 5, DRAWN_BOTTOM - 2.05)
    _tip_to_refused(app, page, session,
                    "Z 1.5 is below the drawing's bottom in slot 5")


def test_tip_to_within_2_mm_under_a_drawings_bottom_goes(app, page, session):
    _drawn_plate(app, page, session)
    _point_in_slot(app, page, session, 5, DRAWN_BOTTOM - 1.95)
    _tip_to_goes(app, page, session, DRAWN_BOTTOM - 1.95)


def test_tip_to_past_1_mm_under_a_measured_bottom_is_refused(
        app, page, session):
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    _point_in_slot(app, page, session, 5, MEASURED_BOTTOM - 1.02)
    _tip_to_refused(app, page, session,
                    "Z 3.2 is below the measured bottom in slot 5")


def test_tip_to_within_1_mm_under_a_measured_bottom_goes(app, page, session):
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    _point_in_slot(app, page, session, 5, MEASURED_BOTTOM - 0.95)
    _tip_to_goes(app, page, session, MEASURED_BOTTOM - 0.95)


def test_a_measurement_of_an_earlier_session_is_what_a_target_is_checked_by(
        app, page, session):
    _measured_plate(app, page, session)
    session.new_run()                        # nothing pressed in this one
    session.load_labware(resolve_definition(PLATE), 5)
    _over_the_deck(app, page, session)
    _point_in_slot(app, page, session, 5, MEASURED_BOTTOM - 1.02)
    _tip_to_refused(app, page, session,
                    "Z 3.2 is below the measured bottom in slot 5")


def test_the_bottom_stands_on_the_slots_module(app, page, session):
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2])])
    _drawn_plate(app, page, session)
    # Over the module's top, so the jog envelope has nothing against it.
    _point_in_slot(app, page, session, 5, 65.0)
    _tip_to_refused(app, page, session,
                    "Z 65 is below the drawing's bottom in slot 5")


def test_the_bottom_is_the_targets_slot_not_the_cameras(app, page, session):
    _drawn_plate(app, page, session)
    _camera_over(app, page, session, 5)
    _point_in_slot(app, page, session, 6, 1.0)
    _tip_to_goes(app, page, session, 1.0)


def test_a_slot_with_no_bottom_known_is_not_checked(app, page, session):
    _tip_on(app, page, session)
    _over_the_deck(app, page, session)
    _point_in_slot(app, page, session, 5, 1.0)
    _tip_to_goes(app, page, session, 1.0)


def test_a_point_with_no_z_goes_to_the_travel_height_over_a_plate(
        app, page, session):
    _drawn_plate(app, page, session)
    _point_in_slot(app, page, session, 5, None)
    page.tip_to_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session)[:2] == pytest.approx(slot_center(5), abs=0.05)
    assert _said(page)[1] == "answer"


def test_the_source_dish_is_checked_once_its_bottom_is_confirmed(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    _tip_on(app, page, session)
    point = _point_in_slot(app, page, session, 8, 64.9)
    session.profile.positions["observe"] = [186.0, 229.0, 90.0]
    session.profile.picking.dish_bottom = 66.0
    _tip_to_goes(app, page, session, 64.9)          # not confirmed yet
    _over_the_deck(app, page, session)
    _dish_in_slot_8(session)
    _tip_to_refused(app, page, session,
                    "Z 64.9 is below the measured bottom in slot 8")
    session.change_point(point.name, z=65.1)
    _tip_to_goes(app, page, session, 65.1)


def test_aspirate_at_a_point_past_the_limit_moves_nothing(app, page, session):
    _drawn_plate(app, page, session)
    point = _point_in_slot(app, page, session, 5, DRAWN_BOTTOM - 2.05)
    page.source.buttons[1].click()                       # At a point
    page.source_points.choose(point.name)
    at, before = _aspirated_at(session), _pose(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at == [] and session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == ("Not reachable: Z 1.5 is below the drawing's "
                           "bottom in slot 5.", "refusal")


def test_aspirate_at_a_point_within_the_limit_goes(app, page, session):
    _drawn_plate(app, page, session)
    point = _point_in_slot(app, page, session, 5, DRAWN_BOTTOM - 1.95)
    page.source.buttons[1].click()                       # At a point
    page.source_points.choose(point.name)
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at[0][2] == pytest.approx(DRAWN_BOTTOM - 1.95, abs=0.05)


def _liquid_in_the_tip(app, page, session):
    page.in_place_amount.setValue(15.0)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    page.target.buttons[1].click()                       # At a point
    page.amount.buttons[1].click()                       # All


def test_dispense_at_a_point_past_the_limit_moves_nothing(app, page, session):
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    _liquid_in_the_tip(app, page, session)
    point = _point_in_slot(app, page, session, 5, MEASURED_BOTTOM - 1.02)
    page.target_points.choose(point.name)
    before = _pose(session)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(15.0)
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == ("Not reachable: Z 3.2 is below the measured "
                           "bottom in slot 5.", "refusal")


def test_dispense_at_a_point_within_the_limit_goes(app, page, session):
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    _liquid_in_the_tip(app, page, session)
    point = _point_in_slot(app, page, session, 5, MEASURED_BOTTOM - 0.95)
    _lift(page.dispense_retract, "Stay")
    page.target_points.choose(point.name)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0
    assert _pose(session)[2] == pytest.approx(MEASURED_BOTTOM - 0.95, abs=0.05)


def test_by_hand_z_past_the_limit_moves_nothing(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(3.2)
    at, before = _aspirated_at(session), _pose(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at == [] and session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == ("Not reachable: Z 3.2 is below the measured "
                           "bottom in slot 5.", "refusal")


def test_by_hand_z_within_the_limit_goes(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(3.3)
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at[0][2] == pytest.approx(3.3, abs=0.05)


def test_by_hand_z_past_the_limit_as_soon_as_i_click_moves_nothing(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    page.snap.setChecked(False)
    page.aspirate_on_click.setChecked(True)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(3.2)
    before = _pose(session)
    _left_click(app, page, W / 2, H / 2)
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)
    assert _said(page)[1] == "refusal"


def test_a_jog_step_is_not_checked_against_the_bottom(app, page, session):
    _drawn_plate(app, page, session)
    x, y = slot_center(5)
    session.robot.move_to_coordinates((x, y, 4.0), verbose=False)
    read_pose(page.jog)
    page.jog.set_step(1.0)
    for _ in range(3):
        page.jog.step("z", -1)
        robot_free(page.session, page.jog)
    assert _pose(session)[2] == pytest.approx(1.0, abs=0.05)
    assert _said(page)[1] != "refusal"


# -- how the tip leaves (#102) --------------------------------------------------


def _at_a_point(app, page, session):
    """Ready to aspirate at a point, the tip at the travel height."""
    _ready_by_hand(app, page, session)
    session.add_memo_point(170.0, 140.0, 70.0)
    page.source.buttons[1].click()                       # At a point
    page.source_points.choose("M1")
    assert page.aspirate_button.isEnabled()


def _travel_z(session):
    return session.motion.travel_z


def test_aspirate_at_a_point_rises_there_by_default(app, page, session):
    _at_a_point(app, page, session)
    at = _aspirated_at(session)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert at == [pytest.approx((170.0, 140.0, 70.0), abs=0.05)]
    assert _pose(session) == pytest.approx(
        (170.0, 140.0, _travel_z(session)), abs=0.05)


def test_aspirate_in_a_well_rises_there_by_default(app, page, session):
    _ready_by_hand(app, page, session)
    session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    page.source.buttons[2].click()                       # In a well
    page.source_wells.map.well_clicked.emit("A1")
    x, y = _pose(session)[:2]
    robot, to_well = session.robot, session.robot.move_to_well

    def down_in_the_well(*args, **kwargs):               # the mock stays
        result = to_well(*args, **kwargs)
        robot.move_to_coordinates((x, y, 70.0), verbose=False)
        return result
    robot.move_to_well = down_in_the_well
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(15.0)
    assert _pose(session) == pytest.approx((x, y, _travel_z(session)),
                                           abs=0.05)


def test_dont_retract_leaves_the_tip_down_at_a_point(app, page, session):
    _at_a_point(app, page, session)
    _lift(page.aspirate_retract, "Stay")
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx((170.0, 140.0, 70.0), abs=0.05)


def test_by_hand_dont_retract_leaves_the_tip_down_and_the_camera_where_it_is(
        app, page, session):
    _ready_by_hand(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(70.0)
    _lift(page.aspirate_retract, "Stay")
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx((160.0, 145.0, 70.0), abs=0.05)


def _pour(app, page, session):
    """Something in the tip, the tip at (150, 150) over the deck."""
    _ready_by_hand(app, page, session)
    session.aspirate(15.0, 50.0)
    session.motion.tip_to((150.0, 150.0), 70.0)
    read_pose(page.jog)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.amount.buttons[1].click()                       # All


def test_dispense_rises_by_default(
        app, page, session):
    _pour(app, page, session)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(
        (150.0, 150.0, _travel_z(session)), abs=0.05)


def test_dispense_dont_retract_stays_down(app, page, session):
    _pour(app, page, session)
    _lift(page.dispense_retract, "Stay")
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx((150.0, 150.0, 70.0), abs=0.05)


def test_each_card_has_its_own_lift_on_its_row(page):
    rows = page.dispense_retract
    _lift(rows, "After a wait")
    assert rows.words() == "lift after 5 s"
    rows.delay.setValue(7.5)
    assert rows.words() == "lift after 7.5 s"
    assert "lift right away" in page.aspirate_card.summary.full
    _lift(page.aspirate_retract, "Stay")
    assert page.aspirate_card.summary.full.endswith("stay")
    assert rows.words() == "lift after 7.5 s"


def test_the_wait_is_asked_only_after_a_wait(page):
    rows = page.aspirate_retract
    assert rows.wait_row.isHidden()
    _lift(rows, "After a wait")
    assert not rows.wait_row.isHidden()
    assert (rows.delay.minimum(), rows.delay.maximum()) == (0.5, 60.0)
    assert rows.delay.singleStep() == 0.5
    assert rows.delay.value() == 5.0


def _waiting(app, page, session):
    """Until the tip is down, in the delay: the liquid has been taken."""
    import time
    end = time.monotonic() + 5
    while session.in_tip == 0 and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_esc_in_the_delay_leaves_the_tip_down_and_says_so(app, page, session):
    _at_a_point(app, page, session)
    rows = page.aspirate_retract
    _lift(rows, "After a wait")
    rows.delay.setValue(60.0)
    page.aspirate_button.click()
    _waiting(app, page, session)
    assert session.robot_lease.busy                      # the delay holds it
    assert not page.aspirate_button.isEnabled()
    assert not page.dispense_button.isEnabled()
    session.request_stop()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx((170.0, 140.0, 70.0), abs=0.05)
    assert toast_of(page).text() == "Stopped: the tip stays down."
    assert page.aspirate_button.isEnabled()


def test_by_hand_esc_in_the_delay_does_not_bring_the_camera_back(
        app, page, session):
    _ready_by_hand(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(70.0)
    rows = page.aspirate_retract
    _lift(rows, "After a wait")
    rows.delay.setValue(60.0)
    page.aspirate_button.click()
    _waiting(app, page, session)
    session.request_stop()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx((160.0, 145.0, 70.0), abs=0.05)


def test_by_hand_after_the_delay_brings_the_camera_back(app, page, session):
    _ready_by_hand(app, page, session)
    before = _pose(session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(70.0)
    rows = page.aspirate_retract
    _lift(rows, "After a wait")
    rows.delay.setValue(0.5)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert _pose(session) == pytest.approx(before, abs=0.05)


# -- Retract slowly (#103) -------------------------------------------------------


def test_retract_slowly_sends_one_slow_move_then_rises(app, page, session):
    _at_a_point(app, page, session)
    page.aspirate_retract.slowly.setChecked(True)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert [c for c in session.robot.calls if c[0] == "move_with_speed"] == [
        ("move_with_speed", pytest.approx((170.0, 140.0, 72.0), abs=0.05),
         0.5)]
    assert _pose(session) == pytest.approx(
        (170.0, 140.0, _travel_z(session)), abs=0.05)


def test_lift_slowly_is_off_at_start_and_hidden_with_stay(page):
    for rows in (page.aspirate_retract, page.dispense_retract):
        assert not rows.slowly.isChecked()
        assert not rows.slowly.isHidden()
        rows.slowly.setChecked(True)
        _lift(rows, "Stay")
        assert rows.slowly.isHidden()
        assert not rows.how().rises
        _lift(rows, "Right away")
        assert not rows.slowly.isHidden()
        assert rows.how().slowly


def test_the_words_say_lift_slowly(page):
    rows = page.dispense_retract
    rows.slowly.setChecked(True)
    assert rows.words() == "lift right away, slowly"
    _lift(rows, "After a wait")
    assert rows.words() == "lift after 5 s, slowly"
    _lift(rows, "Stay")
    assert rows.words() == "stay"


# -- Here, and the keys that do it (#104) ----------------------------------------


def _rebind(session, action, key):
    session.settings.keys = {**session.settings.keys, action: key}
    session.settings_changed.emit(session.settings)


def test_from_and_where_have_no_here(page):
    assert "Here" not in [b.text() for b in page.source.buttons
                          + page.target.buttons]


def test_in_place_draws_the_keys_as_bound(page, session):
    assert page.aspirate_in_place.text() == "Aspirate in place"
    assert page.dispense_in_place.text() == "Dispense in place"
    assert page.aspirate_in_place.keycap == "A"
    assert page.dispense_in_place.keycap == "D"
    _rebind(session, "aspirate", "Q")
    _rebind(session, "dispense", "W")
    assert page.aspirate_in_place.keycap == "Q"
    assert page.dispense_in_place.keycap == "W"


def test_the_pads_list_says_what_a_and_d_do(page):
    lines = page.jog._pad_help()
    assert "A   aspirate in place" in lines
    assert "D   dispense in place" in lines


def test_in_place_starts_as_aspirate_then_stays_its_own(page, session):
    picking = session.profile.picking
    assert page.in_place_amount.value() == pytest.approx(picking.vol)
    assert page.in_place_rate.value() == pytest.approx(picking.flow_rate)
    page.in_place_amount.setValue(3.0)
    page.in_place_rate.setValue(7.0)
    page.volume.setValue(25.0)
    page.flow_rate.setValue(90.0)
    page.dispense_rate.setValue(60.0)
    assert page.in_place_amount.value() == 3.0
    assert page.in_place_rate.value() == 7.0
    assert page.volume.value() == 25.0
    assert page.flow_rate.value() == 90.0


def _low(app, page, session):
    _ready_by_hand(app, page, session)
    session.motion.tip_to((180.0, 120.0), 70.0)
    read_pose(page.jog)


def _stayed(session):
    assert _pose(session) == pytest.approx((180.0, 120.0, 70.0), abs=0.05)


def test_aspirate_in_place_neither_lifts_nor_moves(app, page, session):
    _low(app, page, session)
    at = _aspirated_at(session)
    page.in_place_amount.setValue(12.0)
    page.in_place_rate.setValue(30.0)
    page.aspirate_in_place.click()
    robot_free(page.session, page.jog)
    assert at == [pytest.approx((180.0, 120.0, 70.0), abs=0.05)]
    assert session.in_tip == pytest.approx(12.0)
    assert _liquid_calls(session)[-1][2] == 30.0
    _stayed(session)
    assert toast_of(page).text() == "Aspirated 12 µl here."


def test_dispense_in_place_neither_lifts_nor_moves(app, page, session):
    _low(app, page, session)
    session.aspirate(15.0, 50.0)
    robot_free(page.session, page.jog)
    page.in_place_amount.setValue(10.0)
    page.in_place_rate.setValue(20.0)
    page.dispense_in_place.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(5.0)
    assert _liquid_calls(session)[-1][2] == 20.0
    _stayed(session)
    assert toast_of(page).text() == "Dispensed 10 µl here."


def test_the_keys_do_what_the_buttons_do(app, page, session):
    _low(app, page, session)
    page.in_place_amount.setValue(10.0)
    page._key(page._aspirate_in_place)
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(10.0)
    page._key(page._dispense_in_place)
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(0.0)
    _stayed(session)


def test_a_and_d_do_nothing_while_a_field_has_the_keyboard(
        app, page, session, monkeypatch):
    _low(app, page, session)
    monkeypatch.setattr(page.jog, "_typing", True)
    page._key(page._aspirate_in_place)
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0


def test_dispense_in_place_is_gray_while_the_tip_is_empty(app, page, session):
    _tip_on(app, page, session)
    assert page.aspirate_in_place.isEnabled()
    assert not page.dispense_in_place.isEnabled()
    session.aspirate(5.0, 50.0)
    app.processEvents()
    assert page.dispense_in_place.isEnabled()


def test_in_place_is_gray_without_a_tip(page):
    assert not page.aspirate_in_place.isEnabled()
    assert not page.dispense_in_place.isEnabled()


def test_dispense_in_place_above_what_the_tip_holds_is_refused(
        app, page, session):
    _low(app, page, session)
    session.aspirate(5.0, 50.0)
    robot_free(page.session, page.jog)
    page.in_place_amount.setValue(10.0)
    calls = len(_liquid_calls(session))
    page.dispense_in_place.click()
    robot_free(page.session, page.jog)
    assert toast_of(page).text() == "Only 5 µl in the tip."
    assert toast_of(page).property("tone") == "refusal"
    assert session.in_tip == pytest.approx(5.0)
    assert len(_liquid_calls(session)) == calls
    _stayed(session)


def test_in_place_keeps_what_was_found_on_the_picture(app, page, session):
    """The gantry has not moved: what is detected stands."""
    _low(app, page, session)
    _cuboid_at(page, W / 2 + 100, H / 2)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    assert page._cuboids is not None


# -- Aspirate and Dispense, folding cards (#114) -----------------------------

def _held(app, page, session, ul=15.0):
    """Liquid in the tip by In place, which opens no card."""
    _tip_on(app, page, session)
    page.in_place_amount.setValue(ul)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)


def test_aspirate_is_open_at_start_and_one_card_opens_at_a_time(page):
    assert page.aspirate_card.is_open()
    assert not page.dispense_card.is_open()
    page.dispense_card.header.click()
    assert page.dispense_card.is_open()
    assert not page.aspirate_card.is_open()


def test_the_buttons_are_in_the_headers_and_work_folded(app, page, session):
    _held(app, page, session)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.part.setValue(10.0)
    assert not page.dispense_card.is_open()
    assert not page.dispense_card.body.isAncestorOf(page.dispense_button)
    assert not page.aspirate_card.body.isAncestorOf(page.aspirate_button)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(5.0)


def test_the_empty_tip_grays_dispense_and_a_full_one_makes_it_primary(
        app, page, session):
    _tip_on(app, page, session)
    assert page.aspirate_button.objectName() == "primary"
    assert page.dispense_button.objectName() == "secondary"
    assert not page.dispense_button.isEnabled()
    assert page.dispense_card.summary.full == (
        "The tip is empty. Aspirate first.")
    assert page.dispense_card.summary.property("warn") is True

    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    assert page.aspirate_button.objectName() == "secondary"
    assert page.dispense_button.objectName() == "primary"
    assert page.dispense_card.summary.property("warn") is False


def test_an_empty_tip_leaves_dispense_openable_and_its_settings_at_hand(page):
    page.dispense_card.header.click()
    assert page.dispense_card.is_open()
    assert page.dispense_card.isEnabled()


def test_a_successful_aspirate_opens_dispense(app, page, session):
    _tip_on(app, page, session)
    session.add_memo_point(150.0, 150.0, 70.0)
    page.source.buttons[1].click()                       # At a point
    page.source_points.choose("M1")
    robot_free(page.session, page.jog)
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip > 0
    assert page.dispense_card.is_open()
    assert not page.aspirate_card.is_open()


def test_an_aspirate_by_a_click_opens_dispense(app, page, session):
    _ready_by_hand(app, page, session)
    page.aspirate_on_click.setChecked(True)
    page.height_mode.buttons[0].click()                 # Z
    page.hand_z.setValue(70.0)
    _left_click(app, page, W / 2 + 100, H / 2)
    assert session.in_tip > 0
    assert page.dispense_card.is_open()


def test_an_aspirate_refused_opens_nothing(app, page, session):
    session.add_memo_point(150.0, 150.0, 70.0)
    page.source.buttons[1].click()                       # At a point
    page.source_points.choose("M1")
    robot_free(page.session, page.jog)
    page.aspirate_button.click()                         # no tip on
    robot_free(page.session, page.jog)
    assert page.aspirate_card.is_open()


def test_a_dispense_that_empties_the_tip_opens_aspirate(app, page, session):
    _held(app, page, session)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.dispense_card.set_open(True)
    page.part.setValue(6.0)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == pytest.approx(9.0)
    assert page.dispense_card.is_open()                  # some is left
    page.amount.buttons[1].click()                       # All
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0
    assert page.aspirate_card.is_open()
    assert not page.dispense_card.is_open()


def test_in_place_switches_no_card(app, page, session):
    _held(app, page, session)
    assert session.in_tip > 0
    assert page.aspirate_card.is_open()                  # not Dispense
    page.dispense_card.set_open(True)
    page._dispense_in_place()                            # empties it
    robot_free(page.session, page.jog)
    assert session.in_tip == 0.0
    assert page.dispense_card.is_open()                  # not Aspirate


def test_the_summaries_state_the_whole_action(app, page, session):
    from micropick.config.labware import resolve_definition
    session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    aspirate = page.aspirate_card.summary
    assert aspirate.full.startswith("By clicking · ")
    page.volume.setValue(12.0)
    page.flow_rate.setValue(30.0)
    _lift(page.aspirate_retract, "After a wait")
    assert aspirate.full.endswith(
        "12 µl · 30 µl/s · lift after 5 s")
    page.aspirate_retract.slowly.setChecked(True)
    assert aspirate.full.endswith("lift after 5 s, slowly")
    page.source.buttons[2].click()                       # In a well
    page.source_wells.map.well_clicked.emit("A3")
    assert aspirate.full.startswith("Slot 5 · A3 · 0.5 mm above ")

    _held(app, page, session)
    dispense = page.dispense_card.summary
    page.target.buttons[0].click()                       # In a well
    page.target_wells.map.well_clicked.emit("A7")
    page.part.setValue(6.0)
    page.dispense_rate.setValue(20.0)
    assert dispense.full == ("Slot 5 · A7 · 0.5 mm above the bottom · 6 µl · "
                             "20 µl/s · lift right away")
    page.amount.buttons[1].click()                       # All
    assert dispense.full.startswith(
        "Slot 5 · A7 · 0.5 mm above the bottom · everything · ")


def test_a_point_in_a_summary_is_the_point_as_it_stands(app, page, session):
    """Its own Z, not a height above a bottom; and edited, it follows."""
    session.add_memo_point(150.0, 150.0, 70.0)
    page.source.buttons[1].click()                       # At a point
    page.source_points.choose("M1")
    summary = page.aspirate_card.summary
    assert summary.full.startswith("M1 · Z 70 mm · 10 µl")
    session.change_memo_point("M1", z=65.0)
    assert summary.full.startswith("M1 · Z 65 mm · ")


def test_the_dispense_button_says_what_it_does_in_a_header_it_fits(
        app, page, session):
    _held(app, page, session)
    page.target.buttons[1].click()                       # At a point
    assert page.dispense_button.text() == "Dispense"     # none chosen
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.part.setValue(6.0)
    assert page.dispense_button.text() == "Dispense 6 µl"
    page.amount.buttons[1].click()                       # All
    assert page.dispense_button.text() == "Dispense 15 µl"


# -- What the cards hold (#115, MANUAL.md §4.2-4.4) ---------------------------

def _from(page, name):
    from micropick.gui.pages.manual import FROM
    page.source.buttons[FROM.index(name)].click()


def _lift(row, name):
    from micropick.gui.pages.manual import LIFT
    row.choice.buttons[LIFT.index(name)].click()


def _visible_texts(page):
    from PySide6.QtWidgets import QAbstractButton, QLabel
    return [w.text() for w in page.findChildren(QLabel)
            + page.findChildren(QAbstractButton) if w.isVisibleTo(page)]


def test_the_cards_choices_read_as_the_design_says(page):
    assert [b.text() for b in page.source.buttons] == [
        "By clicking", "At a point", "In a well"]
    assert [b.text() for b in page.target.buttons] == [
        "In a well", "At a point"]
    assert page.source.current() == 0
    assert page.aspirate_card.summary.full.startswith("By clicking")


def test_the_fields_are_in_the_card_with_no_change_to_open(page):
    page.show()
    assert page.volume.isVisible() and page.flow_rate.isVisible()
    assert "Change" not in _visible_texts(page)
    assert "Amount, µl" in _visible_texts(page)
    assert "Speed, µl/s" in _visible_texts(page)


def test_by_clicking_has_z_or_above_the_bottom_from_tip_with_z(app, page):
    from micropick.gui.pages.manual import HEIGHT
    page.show()
    page.height_mode.buttons[HEIGHT.index("Z")].click()
    assert page.hand_z_row.isVisible() and page.from_tip.isVisible()
    assert not page.above_row.isVisible()
    assert page.height_row.isVisible()


def test_from_tip_takes_the_z_of_the_tip_now(app, page, session):
    from micropick.gui.pages.manual import HEIGHT
    _over_the_deck(app, page, session)
    page.height_mode.buttons[HEIGHT.index("Z")].click()
    page.from_tip.click()
    assert page.hand_z.value() == pytest.approx(90.0)


def test_in_a_well_has_above_the_bottom_and_a_point_no_height(app, page):
    page.show()
    _from(page, "In a well")
    assert page.above_row.isVisible() and not page.hand_z_row.isVisible()
    assert not page.height_row.isVisible()
    _from(page, "At a point")
    assert not page.above_row.isVisible()
    assert not page.hand_z_row.isVisible()
    assert page.volume.isVisible()


def test_which_bottom_stays_said_in_the_summary_and_the_field(app, page,
                                                              session):
    _plate(session, 2)
    page.show()
    _from(page, "In a well")
    assert "above the drawing's bottom" in page.aspirate_card.summary.full
    assert "above the drawing's bottom" in page.above_bottom.toolTip()


def test_the_size_window_is_on_snaps_row_and_follows_the_profile(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    page._show_size_window()
    assert page.sizes_link.text() == "250–500 µm"


def test_the_size_window_opens_the_histogram_in_a_popover(app, page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    page._cuboids = _detection([300, 320, 600])
    page.show()
    page.sizes_link.click()
    assert page.sizes_popover.isVisible()
    assert "2 of 3" in page.sizes_popover.histogram.state.text()
    page.sizes_popover.close()


def test_the_popover_says_when_nothing_is_measured(app, page):
    page.show()
    page.sizes_link.click()
    assert page.sizes_popover.histogram.state.text() == "nothing measured yet"
    page.sizes_popover.close()


def test_there_is_no_sizes_card(page):
    assert not hasattr(page, "sizes")
    assert "Sizes" not in _visible_texts(page)


def test_at_a_point_are_chips_of_the_points_list(app, page, session):
    from micropick.gui.widgets.point_chips import PointChips
    session.add_memo_point(100.0, 100.0, 70.0)
    assert isinstance(page.source_points, PointChips)
    assert isinstance(page.target_points, PointChips)
    _from(page, "At a point")
    page.source_points.chip("M1").click()
    assert page.aspirate_card.summary.full.startswith("M1 · Z 70 mm")


def test_in_a_well_opens_on_the_plate_chosen_on_well_center(app, page, session):
    _plate(session, 2)
    _plate(session, 5)
    session.choose_plate(5)
    page.show()
    app.processEvents()
    assert page.source_wells.chosen()[0] == "5"


def test_the_plate_chosen_gives_way_to_a_click_on_run(app, page, session):
    _plate(session, 2)
    _plate(session, 5)
    session.choose_plate(5)
    page.show()
    app.processEvents()
    page.source_wells.plate.slot_chosen.emit(2)
    page.hide()
    page.show()
    app.processEvents()
    assert page.source_wells.chosen()[0] == "2"
    assert session.chosen_plate.slot == "5"        # Run's click binds nothing


def test_with_no_plate_chosen_in_a_well_opens_on_the_first_plate(
        app, page, session):
    _plate(session, 2)
    _plate(session, 5)
    page.show()
    app.processEvents()
    assert session.chosen_plate is None
    assert page.source_wells.chosen()[0] == "2"


def _plate(session, slot):
    session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), slot)


def _plan_on(session, slot, wells):
    from micropick.core.routine import Destination, Routine
    definition = resolve_definition("corning_96_wellplate_360ul_flat")
    destination = Destination.from_definition(definition, slot)
    session.set_routine(Routine(destination, {w: 1 for w in wells}, name="t"))


def test_dispenses_map_is_the_plate_with_the_plans_wells_in_one_color(
        app, page, session):
    from PySide6.QtGui import QColor
    from micropick.gui.theme import COLORS
    _plate(session, 2)
    _plate(session, 5)
    _plan_on(session, 5, ["A1", "A2", "B3"])
    picker = page.target_wells
    assert picker.chosen()[0] == "5"
    assert picker.map._fills["A2"] == picker.map._fills["B3"] == QColor(
        COLORS["planned"])
    # Nothing of what Picking filled: a well done reads as planned.
    picker.choose("5", "C1")
    session.routine.next()
    session.routine.record(delivered=1)
    page._show_plan()
    assert not session.routine.remaining("A1")
    assert picker.map._fills["A1"] == QColor(COLORS["planned"])
    assert not {"Filled", "Planned"} & set(_visible_texts(page))


def test_any_well_of_the_plate_can_be_dispensed_into(app, page, session):
    _plate(session, 2)
    _plan_on(session, 2, ["A1"])
    page.target_wells.map.well_clicked.emit("H12")
    assert page.target_wells.chosen()[3] == "H12"


def test_the_lifting_row_folds_to_words(app, page):
    row = page.aspirate_retract
    page.show()
    assert row.words() == "lift right away"
    assert row.line.text().endswith("lift right away</b>")
    assert row.line.text().startswith("After aspirating:")
    assert not row.box.isVisible()
    row.header.click()
    assert row.box.isVisible()
    row.header.click()
    assert not row.box.isVisible()
    assert page.dispense_retract.line.text().startswith("After dispensing:")


def test_the_lifting_choices_are_the_codes_three(app, page):
    row = page.aspirate_retract
    assert [b.text() for b in row.choice.buttons] == [
        "Right away", "After a wait", "Stay"]
    assert row.how() == moves_retract()
    _lift(row, "After a wait")
    row.delay.setValue(7.0)
    assert row.how() == moves_retract(delay_s=7.0)
    assert row.words() == "lift after 7 s"
    row.slowly.setChecked(True)
    assert row.how() == moves_retract(delay_s=7.0, slowly=True)
    assert row.words() == "lift after 7 s, slowly"
    _lift(row, "Stay")
    assert row.how().rises is False
    assert row.words() == "stay"
    _lift(row, "Right away")
    assert row.words() == "lift right away, slowly"
    assert "lift right away, slowly" in page.aspirate_card.summary.full


def moves_retract(**kw):
    from micropick.workflows.manual import Retract
    return Retract(**kw)


def test_wait_shows_only_with_a_wait_and_lift_slowly_goes_with_stay(app, page):
    row = page.aspirate_retract
    page.show()
    row.header.click()
    assert not row.wait_row.isVisible() and row.slowly.isVisible()
    _lift(row, "After a wait")
    assert row.wait_row.isVisible() and row.wait_row.isEnabled()
    _lift(row, "Stay")
    assert not row.wait_row.isVisible() and not row.slowly.isVisible()


def test_each_card_keeps_its_own_lifting(app, page, session):
    _lift(page.aspirate_retract, "Stay")
    assert page.dispense_retract.words() == "lift right away"
    page.work_cards.open(page.dispense_card)
    page.work_cards.open(page.aspirate_card)
    session.profile_changed.emit(session.profile)
    assert page.aspirate_retract.words() == "stay"


def test_no_hint_sentence_of_the_mockup_is_on_screen(app, page):
    words = " ".join(_visible_texts(page))
    for hint in ("Click the picture", "Right-click the picture",
                 "Click a well", "Filled wells"):
        assert hint not in words


def test_an_aspirate_and_a_dispense_answer_in_the_designs_words(app, page,
                                                                session):
    _plate(session, 5)
    _tip_on(app, page, session)
    _from(page, "In a well")
    page.source_wells.choose("5", "A3")
    page.aspirate_button.click()
    robot_free(page.session, page.jog)
    assert toast_of(page).text() == "Aspirated 10 µl."
    page.target_wells.choose("5", "A7")
    page.part.setValue(4.0)
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert toast_of(page).text() == "Dispensed 4 µl into A7."


def test_a_dispense_at_a_point_says_the_point(app, page, session):
    session.add_memo_point(150.0, 150.0, 90.0)
    _tip_on(app, page, session)
    page._aspirate_in_place()
    robot_free(page.session, page.jog)
    page.target.buttons[1].click()                       # At a point
    page.target_points.choose("M1")
    page.dispense_button.click()
    robot_free(page.session, page.jog)
    assert toast_of(page).text().endswith("µl at M1.")


def test_look_at_answers_camera_moved_to_slot(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    page._slot_clicked(4)
    robot_free(page.session, page.jog)
    assert toast_of(page).text() == "Camera moved to slot 4."


def test_detecting_answers_how_many_were_found(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    robot_free(page.session, page.jog)
    _over_the_deck(app, page, session)
    _cuboid_at(page, W / 2, H / 2)
    assert toast_of(page).text() == "1 cuboid found."


def test_nothing_on_the_cards_is_a_qt_table(page):
    from PySide6.QtWidgets import QTableView
    assert page.findChildren(QTableView) == []


def test_every_button_on_the_cards_is_one_of_three_heights(app, page, themed):
    from PySide6.QtWidgets import QPushButton
    page.resize(1920, 1000)
    page.show()
    page.points_list.choose(None)
    app.processEvents()
    cards = (page.in_place_card, page.aspirate_card, page.dispense_card,
             page.points_card)
    for card in cards:
        for button in card.findChildren(QPushButton):
            if button.objectName() != "segment" and button.isVisible():
                assert button.height() in (36, 40, 44), button.text()


def test_the_numbers_on_the_cards_are_fields_of_the_design(page):
    fields = (page.in_place_amount, page.in_place_rate, page.volume,
              page.flow_rate, page.hand_z, page.above_bottom, page.part,
              page.dispense_rate, page.aspirate_retract.delay,
              page.dispense_retract.delay)
    for field in fields:
        assert field.objectName() == "numberField" and not field.suffix()


def test_no_field_takes_the_keyboard_as_the_page_opens(app, page):
    # It would take the robot's keys with it.
    for field in (page.volume, page.flow_rate, page.above_bottom):
        assert field.focusPolicy() == Qt.FocusPolicy.ClickFocus
