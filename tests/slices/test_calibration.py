"""The failures found running the notebook's calibration, each refused."""

import numpy as np
import pytest

from micropick.slices.calibration import (CalibrationRefused, Match,
                                          PixelToRobot, click_refusal,
                                          find_blade, judge, load_template,
                                          offsets, room_refusal, verify)

# The notebook's last calibration, of mirrored frames.
TRUE = PixelToRobot(((0.0038953175, -0.0000685829, 291.0435928),
                     (-0.0001803626, -0.0038690407, 135.2691247)))
START = (294.0, 133.2)
FRAME = (1600, 1200)


def poses():
    return [(START[0] + dx, START[1] + dy) for dx, dy in offsets(0.5)]


def seen(pose):
    return tuple(TRUE.pixel(*pose))


def good_pairs():
    return [(pose, seen(pose)) for pose in poses()]


def test_good_points_give_the_true_map():
    found, residual = judge(good_pairs())
    assert residual < 1e-6
    assert np.allclose(found.robot(800, 600), TRUE.robot(800, 600), atol=1e-6)


def test_a_frame_not_mirrored_keeps_its_true_direction():
    """Pairs are as they were read: nothing sorts them into a direction."""
    unmirrored = [(pose, (1600 - u, v)) for pose, (u, v) in good_pairs()]
    found, _ = judge(list(reversed(unmirrored)))
    for pose, pixel in unmirrored:
        assert np.allclose(found.robot(*pixel), pose, atol=1e-6)


@pytest.mark.parametrize("stale", [
    lambda c: c[:3] + [c[2]],             # the 4th frame kept the 3rd's point
    lambda c: c[:2] + [c[1], c[1]],       # the 3rd and 4th kept the 2nd's
])
def test_points_left_over_from_a_frame_before_are_refused(stale):
    robot, pixels = zip(*good_pairs())
    with pytest.raises(CalibrationRefused):
        judge(list(zip(robot, stale(list(pixels)))))


def test_a_match_on_something_that_stands_still_is_refused():
    still = [(1000, 600), (1001, 600), (1000, 601), (1001, 601)]
    with pytest.raises(CalibrationRefused):
        judge(list(zip(poses(), still)))


def test_the_same_pixel_every_time_is_refused():
    with pytest.raises(CalibrationRefused):
        judge(list(zip(poses(), [(1000, 600)] * 4)))


def test_one_false_match_is_refused():
    robot, pixels = zip(*good_pairs())
    with pytest.raises(CalibrationRefused):
        judge(list(zip(robot, list(pixels[:3]) + [(300, 900)])))


def test_too_few_points_are_refused():
    with pytest.raises(CalibrationRefused):
        judge(good_pairs()[:2])


def test_the_point_back_at_the_start_has_to_agree():
    found, _ = judge(good_pairs())
    assert verify(found, START, seen(START)) < 1e-6
    u, v = seen(START)
    with pytest.raises(CalibrationRefused):
        verify(found, START, (u + 50, v))


def test_no_room_in_the_picture_is_said_before_moving():
    middle = Match(494, 294, 612, 612, 0.9)
    assert room_refusal(middle, FRAME, 0.5) == ""
    assert room_refusal(Match(10, 294, 612, 612, 0.9), FRAME, 0.5)
    assert room_refusal(Match(494, 580, 612, 612, 0.9), FRAME, 0.5)


@pytest.mark.parametrize("spacing", [0.05, 1.5])
def test_a_step_out_of_range_is_refused(spacing):
    assert room_refusal(Match(494, 294, 612, 612, 0.9), FRAME, spacing)


def test_a_click_needs_the_tool_in_the_picture():
    assert click_refusal(TRUE, START, TRUE.robot(100, 100), FRAME) == ""
    over_a_plate = (150.0, 200.0)
    assert click_refusal(TRUE, over_a_plate, TRUE.robot(100, 100), FRAME)


def test_a_target_past_the_picture_is_refused():
    assert click_refusal(TRUE, START, (START[0] + 50, START[1]), FRAME)


def test_the_template_is_found_where_it_was_put_and_its_apex_said():
    template = load_template()
    frame = np.zeros((1200, 1600), np.uint8)
    frame[100:100 + 612, 300:300 + 612] = template
    match = find_blade(frame, template)
    assert (match.x, match.y) == (300, 100)
    assert match.point == (300 + 306 + 200, 100 + 306)


def test_an_empty_picture_has_no_blade():
    template = load_template()
    assert find_blade(np.zeros((1200, 1600), np.uint8), template) is None
    assert find_blade(np.zeros((100, 100), np.uint8), template) is None
