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

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import DeckModule                   # noqa: E402
from micropick.core.deck import slot_center                      # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.detector import Detection                     # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.session import SOURCE_DISH, Session           # noqa: E402
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
    _wait(app, page)
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.source.buttons[0].click()                     # In a well
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
    _wait(app, page)
    session.robot.move_to_coordinates((20.0, 20.0, 90.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)
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
    _wait(app, page)
    before = _xy(session)
    lease = session.robot_lease.claim("someone else")
    try:
        _left_click(app, page, W / 2 + 100, H / 2)
    finally:
        lease.release()
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    before = _xy(session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.source.buttons[0].click()                     # In a well
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
    assert page.view.marks_box.isChecked()
    assert len(_points_drawn(page)) == 1
    page.view.marks_box.setChecked(False)
    assert _points_drawn(page) == []
    page.view.right_clicked.emit(W / 2 - 100, H / 2)
    _wait(app, page)
    assert not page.view.marks_box.isChecked()
    assert page.points_list.names == ["M1", "M2"]
    assert _points_drawn(page) == []


def test_the_chosen_point_is_ringed_on_the_picture(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
    assert _rings(page) == 1
    page.points_list.choose(None)
    assert _rings(page) == 0


def test_a_new_point_is_chosen_in_the_list_and_said(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
    assert page.points_list.chosen_name == "M1"
    assert page.points_list.row("M1").isAncestorOf(page.points_list.actions)
    assert _said(page) == ("M1 added.", "answer")


def test_a_point_is_deleted_under_its_row(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    page.view.right_clicked.emit(W / 2 - 100, H / 2)
    _wait(app, page)
    assert [p.name for p in session.memo_points] == ["M1", "M2"]
    page.points_list.choose("M1")
    page.delete_point_button.click()
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


def _tip_on(app, page, session):
    from micropick.config.labware import LabwareDefinition
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")
    assert session.tip.attached is True
    _wait(app, page)          # the panel reads the pose again for the tip


def test_the_tip_gauge_follows_aspirate_and_dispense(app, page, session):
    _tip_on(app, page, session)
    page.in_place_amount.setValue(15.0)
    page._aspirate_in_place()
    _wait(app, page)
    assert page.gauge.volume.text() == "15 µl"
    session.read_tip()                       # the same tip, read again
    assert page.gauge.volume.text() == "15 µl"
    page.amount.buttons[1].click()                       # All
    page._dispense_in_place()
    _wait(app, page)
    assert page.gauge.volume.text() == "0 µl"
    page._aspirate_in_place()
    _wait(app, page)
    session.drop_tip_in_trash()
    app.processEvents()
    assert page.gauge.volume.text() == "0 µl"           # dropped with the tip


def test_the_aspirate_settings_are_one_line_and_change_unfolds_them(page):
    page.above_bottom.setValue(0.5)
    page.volume.setValue(15.0)
    page.flow_rate.setValue(50.0)
    page.hand_z.setValue(70.0)
    # By hand without a dish bottom in this robot session: a Z.
    assert page.settings_line.text() == (
        "Z 70 mm · 15 µl · 50 µl/s · retract automatically")
    page.source.buttons[0].click()                       # In a well
    assert page.settings_line.text() == (
        "0.5 mm above the bottom · 15 µl · 50 µl/s · retract automatically")
    assert not page.settings_box.isVisibleTo(page)
    page.change_button.click()
    assert page.settings_box.isVisibleTo(page)


def _ready_by_hand(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _tip_on(app, page, session)
    _over_the_deck(app, page, session)
    page.source.buttons[2].click()                     # By hand
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
    _wait(app, page)
    page.source.buttons[2].click()                     # By hand
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
    _wait(app, page)
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
    _wait(app, page)
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
    assert not page.part.isHidden()
    assert page.part.value() == pytest.approx(7.0)
    page.deleteLater()


def test_dispense_is_gray_until_the_tip_holds_and_gives_back_part_then_all(
        app, page, session):
    _tip_on(app, page, session)
    assert page.dispense_card.is_open()                  # #105: always
    assert not page.dispense_card.isEnabled()            # the tip is empty
    assert not page.dispense_button.isEnabled()
    page.in_place_amount.setValue(15.0)
    page._aspirate_in_place()
    _wait(app, page)
    assert page.dispense_card.isEnabled()
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    assert page.dispense_button.text() == "Go to M1 and dispense"
    assert page.dispense_button.isEnabled()
    page.part.setValue(6.0)                              # Part, from the start
    page.dispense_button.click()
    _wait(app, page)
    assert session.in_tip == pytest.approx(9.0)
    page.amount.buttons[1].click()                       # All
    page.dispense_button.click()
    _wait(app, page)
    assert session.in_tip == 0.0
    assert not page.dispense_card.isEnabled()
    assert not page.dispense_button.isEnabled()


def test_aspirate_and_dispense_never_fold(page):
    """#105: what the tip holds or a click cannot move a card."""
    for card in (page.aspirate_card, page.dispense_card):
        assert card.is_open()
        card.set_open(False)
        assert card.is_open()


def test_liquid_in_the_tip_and_a_right_click_move_no_card(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _tip_on(app, page, session)
    _over_the_deck(app, page, session)
    page.resize(1600, 900)
    app.processEvents()

    def where():
        return [c.geometry() for c in (page.aspirate_card, page.dispense_card)]

    before = where()
    page._aspirate_in_place()
    _wait(app, page)
    assert session.in_tip > 0
    assert where() == before
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
    assert where() == before
    assert len(_points_drawn(page)) == 1                 # and it is seen


def test_dispense_in_a_well_says_the_well_and_goes_there(app, page, session):
    from micropick.config.labware import resolve_definition
    plate = session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    _tip_on(app, page, session)
    page._aspirate_in_place()
    _wait(app, page)
    page.target.buttons[0].click()                       # In a well
    page.target_wells.well.setCurrentText("A7")
    assert page.dispense_button.text() == "Go to A7 and dispense"
    _wait(app, page)                                     # a busy panel refuses
    page.dispense_button.click()
    _wait(app, page)
    calls = session.robot.calls
    lid = session.run_state.labware["5"].labware_id
    assert ("move_to_well", lid, "A7", "bottom") in calls
    assert calls[-1][0] == "dispense_in_place"
    assert session.in_tip == 0.0
    del plate


def test_the_plan_map_chooses_the_well_to_dispense_into(app, page, session):
    from micropick.config.labware import resolve_definition
    from micropick.core.routine import Destination, Routine
    definition = resolve_definition("corning_96_wellplate_360ul_flat")
    session.load_labware(definition, 5)
    destination = Destination.from_definition(definition, 5)
    session.set_routine(Routine(destination, {"A7": 1, "B7": 1}, name="t"))
    page.target.buttons[0].click()
    page.plan_map.well_clicked.emit("B7")
    assert page.target_wells.chosen()[3] == "B7"
    assert page.dispense_button.text() == "Go to B7 and dispense"


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
    page.source.buttons[0].click()                     # In a well
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
    page.source.buttons[2].click()                     # By hand
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    page.view.right_clicked.emit(W / 2 + 100, H / 2)
    _wait(app, page)
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
    _wait(app, page)
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    session.remember("trough", (120.0, 130.0, 30.0))
    page.points_list.choose("trough")
    assert page.camera_to_button.isEnabled()
    page.camera_to_button.click()
    _wait(app, page)
    # The gantry a pipette offset short of the point; the Z is not used.
    assert _xy(session) == pytest.approx((110.0, 135.0), abs=0.05)
    z = session.robot.get_position(verbose=False)[0]["z"]
    assert z > 30.0


def test_tip_to_is_gray_without_a_tip_and_goes_down_to_the_points_z(
        app, page, session):
    _calibrate(session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    session.remember("trough", (120.0, 130.0, 30.0))
    page.points_list.choose("trough")
    assert not page.tip_to_button.isEnabled()
    assert page.tip_to_button.toolTip() == "Put a tip on first."
    _tip_on(app, page, session)
    page.points_list.choose("trough")
    assert page.tip_to_button.isEnabled()
    page.tip_to_button.click()
    _wait(app, page)
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
    _wait(app, page)
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    assert page.tip_crosshair_button.isEnabled()
    page.tip_crosshair_button.click()
    _wait(app, page)
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
    _wait(app, page)
    x, y = slot_center(8)[:2]
    session.robot.move_to_coordinates((x - 100.0, y, 30.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)
    page.tip_crosshair_button.click()
    _wait(app, page)
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
    _wait(app, page)
    assert not button.isEnabled()
    assert button.toolTip() == "Put a tip on first."


def test_tip_to_crosshair_is_gray_while_the_robot_is_busy(app, page, session):
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
    _wait(app, page)
    session.robot.move_to_coordinates((375.0, 150.0, 90.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)
    page.tip_crosshair_button.click()
    _wait(app, page)
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
    """A plate in slot 5, measured and so confirmed: 0.4, -0.3 off the
    drawing's center, the rim 1 mm under it, 9 mm deep."""
    session.load_labware(resolve_definition(PLATE), 5)
    session.profile.picking.well_offset_x = 0.0
    session.profile.picking.well_offset_y = 0.0
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    _tip_on(app, page, session)
    page.source.buttons[0].click()                       # In a well
    page.source_wells.well.setCurrentText("A7")
    page.above_bottom.setValue(0.5)
    _wait(app, page)


def test_aspirate_in_a_confirmed_plate_reaches_the_rim_less_depth_plus_height(
        app, page, session):
    _measured_plate(app, page, session)
    at = _aspirated_at(session)
    z = _pose(session)[2]              # the mock stays here: over the rim
    page.aspirate_button.click()
    _wait(app, page)
    # Over the rim by the approach, at the measured x and y ...
    assert session.robot.well_offset == pytest.approx((0.4, -0.3, 1.0))
    # ... then straight down: 2 mm to the rim, 9 to the bottom, 0.5 up.
    assert at[-1][2] == pytest.approx(z - 2.0 - 9.0 + 0.5, abs=0.01)


def test_aspirate_prepares_the_plunger_over_the_rim_not_in_the_well(
        app, page, session):
    _measured_plate(app, page, session)
    page.aspirate_button.click()
    _wait(app, page)
    names = [c[0] for c in session.robot.calls]
    well = len(names) - 1 - names[::-1].index("move_to_well")
    assert names[well + 1] == "prepare_to_aspirate"


def test_dispense_in_a_confirmed_plate_reaches_the_deposit_height(
        app, page, session):
    _measured_plate(app, page, session)
    session.profile.picking.deposit_offset_z = 1.5
    page._aspirate_in_place()
    _wait(app, page)
    page.dispense_retract.choice.setCurrentText("Don't retract")
    page.target.buttons[0].click()                       # In a well
    page.target_wells.well.setCurrentText("A7")
    _wait(app, page)
    z = _pose(session)[2]
    page.dispense_button.click()
    _wait(app, page)
    assert session.robot.well_offset == pytest.approx((0.4, -0.3, 1.0))
    assert _pose(session)[2] == pytest.approx(z - 2.0 - 9.0 + 1.5, abs=0.01)
    assert session.robot.calls[-1][0] == "dispense_in_place"


def _well_moves(session):
    return [c for c in session.robot.calls if c[0] == "move_to_well"]


def test_a_plate_measured_but_not_confirmed_takes_the_drawings_bottom(
        app, page, session):
    _measured_plate(app, page, session)
    session.new_run()                        # measured, not said so here
    session.load_labware(resolve_definition(PLATE), 5)
    page.source_wells.well.setCurrentText("A7")
    _wait(app, page)
    page.aspirate_button.click()
    _wait(app, page)
    assert _well_moves(session)[-1][3] == "bottom"
    # The x and y are the measured center, confirmed or not.
    assert session.robot.well_offset == pytest.approx((0.4, -0.3, 0.5))


def test_a_plate_not_measured_takes_the_drawings_bottom(app, page, session):
    session.load_labware(resolve_definition(PLATE), 5)
    session.profile.picking.well_offset_x = 0.0
    session.profile.picking.well_offset_y = 0.0
    _tip_on(app, page, session)
    page.source.buttons[0].click()
    page.source_wells.well.setCurrentText("A7")
    page.above_bottom.setValue(0.5)
    _wait(app, page)
    page.aspirate_button.click()
    _wait(app, page)
    assert _well_moves(session)[-1][3] == "bottom"
    assert session.robot.well_offset == pytest.approx((0.0, 0.0, 0.5))


def test_the_settings_line_names_the_bottom_and_follows_the_confirmation(
        app, page, session):
    session.load_labware(resolve_definition(PLATE), 5)
    page.source.buttons[0].click()
    page.source_wells.well.setCurrentText("A7")
    page.above_bottom.setValue(0.5)
    assert page.settings_line.text().startswith(
        "0.5 mm above the drawing's bottom · ")
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    assert page.settings_line.text().startswith(
        "0.5 mm above the measured bottom · ")


def test_the_settings_line_follows_the_plate_chosen(app, page, session):
    session.load_labware(resolve_definition(PLATE), 5)
    session.load_labware(resolve_definition(PLATE), 6)
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    page.source.buttons[0].click()
    page.source_wells.plate.setCurrentIndex(0)
    assert "measured bottom" in page.settings_line.text()
    page.source_wells.plate.setCurrentIndex(1)
    assert "drawing's bottom" in page.settings_line.text()


def test_the_settings_line_goes_back_when_the_module_height_changes(
        app, page, session):
    """#92: a measurement on another height is not the plate's."""
    session.load_labware(resolve_definition(PLATE), 5)
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.0), "B2", depth_mm=9.0)
    page.source.buttons[0].click()
    assert "measured bottom" in page.settings_line.text()
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2])])
    assert "drawing's bottom" in page.settings_line.text()


def test_by_hand_height_is_above_the_bottom(page):
    page.source.buttons[2].click()                       # By hand
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
    page.jog.refresh_position()
    _wait(app, page)


def _by_hand_on_a_plate(app, page, session):
    """By hand, the crosshair on A1 of the measured plate, at a safe Z."""
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    page.source.buttons[2].click()                       # By hand
    page.volume.setValue(15.0)
    _aim_at(app, page, session, *A1)


def _dish_in_slot_8(session):
    """The camera over the dish in slot 8 and its bottom confirmed."""
    session.profile.positions["observe"] = [186.0, 229.0, 90.0]
    session.profile.picking.dish_bottom = 66.0
    session.confirm("dish_bottom")


def test_by_hand_over_a_confirmed_plate_goes_above_its_bottom(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    assert page.height_mode.current() == 1               # the default then
    assert page.settings_line.text().startswith(
        "0.5 mm above the bottom of slot 5 · ")
    at = _aspirated_at(session)
    page.aspirate_button.click()
    _wait(app, page)
    assert at[0] == pytest.approx((*A1, PLATE_BOTTOM + 0.5), abs=0.05)


def test_by_hand_the_bottom_follows_the_spot_from_slot_to_slot(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    _dish_in_slot_8(session)
    _aim_at(app, page, session, 196.0, 224.0)            # slot 8
    assert page.settings_line.text().startswith(
        "0.5 mm above the bottom of slot 8 · ")
    at = _aspirated_at(session)
    page.aspirate_button.click()
    _wait(app, page)
    assert at[0][2] == pytest.approx(66.5, abs=0.05)
    _aim_at(app, page, session, *A1)
    assert page.settings_line.text().startswith(
        "0.5 mm above the bottom of slot 5 · ")


def test_by_hand_the_bottom_does_not_depend_on_the_source_chosen(
        app, page, session):
    _by_hand_on_a_plate(app, page, session)
    session.choose_source(SOURCE_DISH)
    app.processEvents()
    assert page.settings_line.text().startswith(
        "0.5 mm above the bottom of slot 5 · ")


def test_by_hand_over_a_slot_with_nothing_confirmed_is_a_z(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    above = page.height_mode.buttons[1]
    assert above.isEnabled()
    _aim_at(app, page, session, 64.0, 133.0)             # slot 4: nothing
    assert not above.isEnabled()
    assert page.height_mode.current() == 0               # Z
    assert page.settings_line.text().startswith("Z ")
    _aim_at(app, page, session, *A1)
    assert above.isEnabled()
    assert page.height_mode.current() == 1


def test_by_hand_the_dish_bottom_is_only_the_dishs_slots(app, page, session):
    _ready_by_hand(app, page, session)                   # over slot 5
    _dish_in_slot_8(session)
    app.processEvents()
    assert not page.height_mode.buttons[1].isEnabled()


def test_by_hand_a_plate_not_confirmed_offers_only_z(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    session.new_run()                        # measured, not said so here
    session.load_labware(resolve_definition(PLATE), 5)
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
    _wait(app, page)
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
    assert toast_of(page).text() == "No bottom is confirmed in slot 4."
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(before)


def test_by_hand_z_is_not_refused_on_the_wall(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    _aim_at(app, page, session, A1[0] + 4.5, A1[1])
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(20.0)
    at = _aspirated_at(session)
    page.aspirate_button.click()
    _wait(app, page)
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
    _wait(app, page)
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == (f"Not reachable: {BELOW}.", "refusal")
    assert len(page.jog.position_lines) == 1


def test_a_point_marked_below_a_platform_says_so_and_its_z_stays_red(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    session.set_deck_modules(
        [DeckModule(slots=[8], offset=[0, 0, 64.0], name="Platform")])
    x, y = slot_center(8)
    session.robot.move_to_coordinates((x, y, 90.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)
    page._set_new_z(50.0)
    page._mark(W / 2, H / 2)
    point = session.memo_points[-1]
    assert _said(page) == (f"{point.name} is not reachable: {BELOW}.",
                           "refusal")
    z_cell = page.points_list.row(point.name).cells["z"]
    assert BAD in z_cell.styleSheet()
    assert z_cell.toolTip() == BELOW


def test_a_memo_point_that_can_be_reached_is_an_answer(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page._mark(W / 2, H / 2)
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
    _wait(app, page)
    _over_the_deck(app, page, session)
    page._mark(W / 2, H / 2)
    assert _said(page) == ("Calibrate the tip first.", "refusal")


def test_a_right_click_outside_the_map_is_refused_in_a_sentence(
        app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    _over_the_deck(app, page, session)
    page._mark(-5000.0, -5000.0)
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
    _wait(app, page)
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
    _wait(app, page)
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


def test_the_height_line_gives_a_confirmed_plate_as_measured(
        app, page, session):
    _calibrated_on_screen(app, page, session)
    _measured_plate(app, page, session)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 13.22 · bottom 4.22 · measured"


def test_the_height_line_says_drawing_for_a_measurement_not_confirmed(
        app, page, session):
    """As In a well goes: above the drawing's bottom until it is confirmed."""
    _calibrated_on_screen(app, page, session)
    _measured_plate(app, page, session)
    session.new_run()                        # measured, not said so here
    session.load_labware(resolve_definition(PLATE), 5)
    _camera_over(app, page, session, 5)
    assert page.view.height_line == \
        "Slot 5 · top 14.22 · bottom 3.55 · drawing"


def test_the_height_line_turns_measured_as_the_plate_is_confirmed(
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
    page.jog.refresh_position()
    _wait(app, page)
    assert page.view.height_line.startswith("Slot 5 ")
    session.profile.calibration.pipette_offset = None
    dx, dy = session.settings.tip_cal_start_offset
    session.robot.move_to_coordinates((x - dx, y - dy, 90.0), verbose=False)
    page.jog.refresh_position()
    _wait(app, page)
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
    _wait(app, page)
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
    _wait(app, page)
    page._dispense_in_place()                                # D
    _wait(app, page)
    assert _liquid_calls(session) == [("aspirate_in_place", 6.0, 12.0),
                                      ("dispense_in_place", 6.0, 12.0)]
    assert _said(page) == ("Dispensed 6 µl here.", "answer")


def test_dispense_starts_at_the_profiles_flow_rate(app, session):
    session.profile.picking.flow_rate = 33.0
    page = ManualPage(session)
    assert page.dispense_rate.value() == 33.0
    assert page.dispense_line.text() == "33 µl/s · retract automatically"
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
    _wait(app, page)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.dispense_button.click()
    _wait(app, page)
    assert _liquid_calls(session)[-1][0] == "dispense_in_place"
    assert _liquid_calls(session)[-1][2] == 7.0


def test_the_dispense_line_says_the_speed_and_change_edits_it(page):
    page.dispense_rate.setValue(25.0)
    assert page.dispense_line.text() == "25 µl/s · retract automatically"
    assert not page.dispense_box.isVisibleTo(page)
    page.dispense_change.setChecked(True)
    assert page.dispense_box.isVisibleTo(page)
    assert page.dispense_box.isAncestorOf(page.dispense_rate)
    page.dispense_rate.setValue(12.5)
    assert page.dispense_line.text() == "12.5 µl/s · retract automatically"
    page.dispense_change.setChecked(False)
    assert not page.dispense_box.isVisibleTo(page)


def test_aspirates_line_does_not_follow_dispenses_speed(page):
    page.flow_rate.setValue(40.0)
    before = page.settings_line.text()
    page.dispense_rate.setValue(7.0)
    assert page.settings_line.text() == before
    assert page.dispense_line.text() == "7 µl/s · retract automatically"
    page.flow_rate.setValue(41.0)
    assert page.dispense_line.text() == "7 µl/s · retract automatically"


def test_the_dispense_line_stands_under_amount_and_over_where(app, page):
    page.dispense_change.setChecked(True)
    page.show()
    app.processEvents()

    def top(widget):
        return widget.mapTo(page, widget.rect().topLeft()).y()

    assert (top(page.amount) < top(page.dispense_line)
            < top(page.dispense_box) < top(page.target))


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
    _wait(app, page)
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == (f"Not reachable: {said}.", "refusal")


def _tip_to_goes(app, page, session, z):
    page.tip_to_button.click()
    _wait(app, page)
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


def test_a_measurement_not_confirmed_is_checked_as_the_drawing(
        app, page, session):
    _measured_plate(app, page, session)
    session.new_run()                        # measured, not said so here
    session.load_labware(resolve_definition(PLATE), 5)
    _over_the_deck(app, page, session)
    _point_in_slot(app, page, session, 5, DRAWN_BOTTOM - 1.95)
    _tip_to_goes(app, page, session, DRAWN_BOTTOM - 1.95)


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
    _wait(app, page)
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
    _wait(app, page)
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
    _wait(app, page)
    assert at[0][2] == pytest.approx(DRAWN_BOTTOM - 1.95, abs=0.05)


def _liquid_in_the_tip(app, page, session):
    page.in_place_amount.setValue(15.0)
    page._aspirate_in_place()
    _wait(app, page)
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
    _wait(app, page)
    assert session.in_tip == pytest.approx(15.0)
    assert _pose(session) == pytest.approx(before)
    assert _said(page) == ("Not reachable: Z 3.2 is below the measured "
                           "bottom in slot 5.", "refusal")


def test_dispense_at_a_point_within_the_limit_goes(app, page, session):
    _measured_plate(app, page, session)
    _over_the_deck(app, page, session)
    _liquid_in_the_tip(app, page, session)
    point = _point_in_slot(app, page, session, 5, MEASURED_BOTTOM - 0.95)
    page.dispense_retract.choice.setCurrentText("Don't retract")
    page.target_points.choose(point.name)
    page.dispense_button.click()
    _wait(app, page)
    assert session.in_tip == 0.0
    assert _pose(session)[2] == pytest.approx(MEASURED_BOTTOM - 0.95, abs=0.05)


def test_by_hand_z_past_the_limit_moves_nothing(app, page, session):
    _by_hand_on_a_plate(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(3.2)
    at, before = _aspirated_at(session), _pose(session)
    page.aspirate_button.click()
    _wait(app, page)
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
    _wait(app, page)
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
    page.jog.refresh_position()
    _wait(app, page)
    page.jog.set_step(1.0)
    for _ in range(3):
        page.jog.step("z", -1)
        _wait(app, page)
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
    _wait(app, page)
    assert at == [pytest.approx((170.0, 140.0, 70.0), abs=0.05)]
    assert _pose(session) == pytest.approx(
        (170.0, 140.0, _travel_z(session)), abs=0.05)


def test_aspirate_in_a_well_rises_there_by_default(app, page, session):
    _ready_by_hand(app, page, session)
    session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    page.source.buttons[0].click()                       # In a well
    page.source_wells.well.setCurrentText("A1")
    x, y = _pose(session)[:2]
    robot, to_well = session.robot, session.robot.move_to_well

    def down_in_the_well(*args, **kwargs):               # the mock stays
        result = to_well(*args, **kwargs)
        robot.move_to_coordinates((x, y, 70.0), verbose=False)
        return result
    robot.move_to_well = down_in_the_well
    page.aspirate_button.click()
    _wait(app, page)
    assert session.in_tip == pytest.approx(15.0)
    assert _pose(session) == pytest.approx((x, y, _travel_z(session)),
                                           abs=0.05)


def test_dont_retract_leaves_the_tip_down_at_a_point(app, page, session):
    _at_a_point(app, page, session)
    page.aspirate_retract.choice.setCurrentText("Don't retract")
    page.aspirate_button.click()
    _wait(app, page)
    assert _pose(session) == pytest.approx((170.0, 140.0, 70.0), abs=0.05)


def test_by_hand_dont_retract_leaves_the_tip_down_and_the_camera_where_it_is(
        app, page, session):
    _ready_by_hand(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(70.0)
    page.aspirate_retract.choice.setCurrentText("Don't retract")
    page.aspirate_button.click()
    _wait(app, page)
    assert _pose(session) == pytest.approx((160.0, 145.0, 70.0), abs=0.05)


def _pour(app, page, session):
    """Something in the tip, the tip at (150, 150) over the deck."""
    _ready_by_hand(app, page, session)
    session.aspirate(15.0, 50.0)
    session.motion.tip_to((150.0, 150.0), 70.0)
    page.jog.refresh_position()
    _wait(app, page)
    _dispense_at_a_point(page, session, 150.0, 150.0, 70.0)
    page.amount.buttons[1].click()                       # All


def test_dispense_rises_by_default(
        app, page, session):
    _pour(app, page, session)
    page.dispense_button.click()
    _wait(app, page)
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx(
        (150.0, 150.0, _travel_z(session)), abs=0.05)


def test_dispense_dont_retract_stays_down(app, page, session):
    _pour(app, page, session)
    page.dispense_retract.choice.setCurrentText("Don't retract")
    page.dispense_button.click()
    _wait(app, page)
    assert session.in_tip == 0.0
    assert _pose(session) == pytest.approx((150.0, 150.0, 70.0), abs=0.05)


def test_each_card_has_its_own_retract_on_its_line(page):
    page.dispense_retract.choice.setCurrentText("Retract after delay")
    assert page.dispense_line.text() == "50 µl/s · retract after 5 s"
    page.dispense_retract.delay.setValue(7.5)
    assert page.dispense_line.text() == "50 µl/s · retract after 7.5 s"
    assert "retract automatically" in page.settings_line.text()
    page.aspirate_retract.choice.setCurrentText("Don't retract")
    assert page.settings_line.text().endswith("don't retract")
    assert page.dispense_line.text() == "50 µl/s · retract after 7.5 s"


def test_the_delay_is_asked_only_for_retract_after_delay(page):
    rows = page.aspirate_retract
    assert not rows.delay.isEnabled()
    rows.choice.setCurrentText("Retract after delay")
    assert rows.delay.isEnabled()
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
    rows.choice.setCurrentText("Retract after delay")
    rows.delay.setValue(60.0)
    page.aspirate_button.click()
    _waiting(app, page, session)
    assert session.robot_lease.busy                      # the delay holds it
    assert not page.aspirate_button.isEnabled()
    assert not page.dispense_button.isEnabled()
    session.request_stop()
    _wait(app, page)
    assert _pose(session) == pytest.approx((170.0, 140.0, 70.0), abs=0.05)
    assert toast_of(page).text() == "Stopped: the tip stays down."
    assert page.aspirate_button.isEnabled()


def test_by_hand_esc_in_the_delay_does_not_bring_the_camera_back(
        app, page, session):
    _ready_by_hand(app, page, session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(70.0)
    rows = page.aspirate_retract
    rows.choice.setCurrentText("Retract after delay")
    rows.delay.setValue(60.0)
    page.aspirate_button.click()
    _waiting(app, page, session)
    session.request_stop()
    _wait(app, page)
    assert _pose(session) == pytest.approx((160.0, 145.0, 70.0), abs=0.05)


def test_by_hand_after_the_delay_brings_the_camera_back(app, page, session):
    _ready_by_hand(app, page, session)
    before = _pose(session)
    page.height_mode.buttons[0].click()                  # Z
    page.hand_z.setValue(70.0)
    rows = page.aspirate_retract
    rows.choice.setCurrentText("Retract after delay")
    rows.delay.setValue(0.5)
    page.aspirate_button.click()
    _wait(app, page)
    assert _pose(session) == pytest.approx(before, abs=0.05)


# -- Retract slowly (#103) -------------------------------------------------------


def test_retract_slowly_sends_one_slow_move_then_rises(app, page, session):
    _at_a_point(app, page, session)
    page.aspirate_retract.slowly.setChecked(True)
    page.aspirate_button.click()
    _wait(app, page)
    assert [c for c in session.robot.calls if c[0] == "move_with_speed"] == [
        ("move_with_speed", pytest.approx((170.0, 140.0, 72.0), abs=0.05),
         0.5)]
    assert _pose(session) == pytest.approx(
        (170.0, 140.0, _travel_z(session)), abs=0.05)


def test_retract_slowly_is_off_at_start_and_gray_with_dont_retract(page):
    for rows in (page.aspirate_retract, page.dispense_retract):
        box = rows.slowly.parentWidget()     # Dispense's card is gray itself
        assert not rows.slowly.isChecked()
        assert rows.slowly.isEnabledTo(box)
        rows.slowly.setChecked(True)
        rows.choice.setCurrentText("Don't retract")
        assert not rows.slowly.isEnabledTo(box)
        assert not rows.how().rises
        rows.choice.setCurrentText("Retract automatically")
        assert rows.slowly.isEnabledTo(box)
        assert rows.how().slowly


def test_the_line_says_retract_slowly(page):
    rows = page.dispense_retract
    rows.slowly.setChecked(True)
    assert page.dispense_line.text() == "50 µl/s · retract slowly"
    rows.choice.setCurrentText("Retract after delay")
    assert page.dispense_line.text() == "50 µl/s · retract slowly after 5 s"
    rows.choice.setCurrentText("Don't retract")
    assert page.dispense_line.text() == "50 µl/s · don't retract"


# -- Here, and the keys that do it (#104) ----------------------------------------


def _rebind(session, action, key):
    session.settings.keys = {**session.settings.keys, action: key}
    session.settings_changed.emit(session.settings)


def test_from_and_where_have_no_here(page):
    assert [b.text() for b in page.source.buttons] == [
        "In a well", "At a point", "By hand"]
    assert [b.text() for b in page.target.buttons] == [
        "In a well", "At a point"]


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
    page.jog.refresh_position()
    _wait(app, page)


def _stayed(session):
    assert _pose(session) == pytest.approx((180.0, 120.0, 70.0), abs=0.05)


def test_aspirate_in_place_neither_lifts_nor_moves(app, page, session):
    _low(app, page, session)
    at = _aspirated_at(session)
    page.in_place_amount.setValue(12.0)
    page.in_place_rate.setValue(30.0)
    page.aspirate_in_place.click()
    _wait(app, page)
    assert at == [pytest.approx((180.0, 120.0, 70.0), abs=0.05)]
    assert session.in_tip == pytest.approx(12.0)
    assert _liquid_calls(session)[-1][2] == 30.0
    _stayed(session)
    assert toast_of(page).text() == "Aspirated 12 µl here."


def test_dispense_in_place_neither_lifts_nor_moves(app, page, session):
    _low(app, page, session)
    session.aspirate(15.0, 50.0)
    _wait(app, page)
    page.in_place_amount.setValue(10.0)
    page.in_place_rate.setValue(20.0)
    page.dispense_in_place.click()
    _wait(app, page)
    assert session.in_tip == pytest.approx(5.0)
    assert _liquid_calls(session)[-1][2] == 20.0
    _stayed(session)
    assert toast_of(page).text() == "Dispensed 10 µl here."


def test_the_keys_do_what_the_buttons_do(app, page, session):
    _low(app, page, session)
    page.in_place_amount.setValue(10.0)
    page._key(page._aspirate_in_place)
    _wait(app, page)
    assert session.in_tip == pytest.approx(10.0)
    page._key(page._dispense_in_place)
    _wait(app, page)
    assert session.in_tip == pytest.approx(0.0)
    _stayed(session)


def test_a_and_d_do_nothing_while_a_field_has_the_keyboard(
        app, page, session, monkeypatch):
    _low(app, page, session)
    monkeypatch.setattr(page.jog, "_typing", True)
    page._key(page._aspirate_in_place)
    _wait(app, page)
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
    _wait(app, page)
    page.in_place_amount.setValue(10.0)
    calls = len(_liquid_calls(session))
    page.dispense_in_place.click()
    _wait(app, page)
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
    _wait(app, page)
    assert page._cuboids is not None
