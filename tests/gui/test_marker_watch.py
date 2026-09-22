"""The live marker watch, and the primitives it draws.

No Qt: the watch blocks and knows nothing about widgets, and the geometry
is pure. Frames come from `MarkerScene`, which renders a real 6X6_250
marker, so what is exercised here is cv2's own detector and not a stub.
"""

import cv2
import numpy as np
import pytest

from micropick.gui.marker_watch import DICTIONARIES, MarkerWatch
from micropick.hardware.mock import MarkerScene
from micropick.viz import markers
from micropick.viz.overlays import Circle, Polyline, Text


@pytest.fixture(scope="module")
def frame():
    # The scene's own size. Halving it renders a marker cv2 no longer
    # detects, which would make this a test of the mock's rendering rather
    # than of the watch; a detection here is about 16 ms.
    return MarkerScene().render((150.0, 150.0))


@pytest.fixture(scope="module")
def watch():
    return MarkerWatch()


def test_found_in_the_dictionary_it_was_asked_for(watch, frame):
    sighting = watch.look(frame, "DICT_6X6_250")
    assert sighting.found and sighting.as_asked
    assert sighting.marker_id == 1
    assert sighting.corners.shape == (4, 2)
    assert sighting.side_px > 50
    assert "found" in sighting.describe()


def test_the_wrong_dictionary_still_finds_it_and_says_which(watch, frame):
    sighting = watch.look(frame, "DICT_4X4_50")
    assert sighting.found and not sighting.as_asked
    assert sighting.dictionary == "DICT_6X6_250"
    assert "DICT_6X6_250" in sighting.describe()
    assert "step 2" in sighting.describe()


def test_a_mirrored_marker_is_in_no_dictionary(watch, frame):
    """The whole point: face down or printed through the paper reads as
    mirrored, and mirrored is undetectable - so the message has to say it."""
    sighting = watch.look(cv2.flip(frame, 1), "DICT_6X6_250")
    assert not sighting.found
    assert sighting.items() == []
    assert "mirrored" in sighting.describe()
    for name in DICTIONARIES:
        assert name in sighting.describe()


def test_an_empty_frame_finds_nothing(watch):
    blank = np.full((480, 640, 3), 255, np.uint8)
    assert not watch.look(blank, "DICT_6X6_250").found


def test_rotation_reads_as_the_marker_is_turned(watch, frame):
    """The marker's own top edge, so which way up is visible rather than
    inferred. A quarter turn of the picture is a quarter turn here."""
    upright = watch.look(frame, "DICT_6X6_250")
    base = markers.rotation_deg(upright.corners)
    turned = watch.look(cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE),
                        "DICT_6X6_250")
    assert turned.found
    delta = (markers.rotation_deg(turned.corners) - base + 180) % 360 - 180
    assert abs(delta - 90) < 5
    assert markers.orientation(turned.corners) != markers.orientation(upright.corners)


def test_offset_is_from_the_frame_centre(watch, frame):
    sighting = watch.look(frame, "DICT_6X6_250")
    height, width = frame.shape[:2]
    expected = sighting.corners.mean(axis=0) - np.array([width / 2, height / 2])
    assert np.allclose(sighting.offset_px, expected)
    assert f"{np.linalg.norm(expected):.0f} px" in sighting.describe()


def test_items_carry_the_outline_the_first_corner_and_a_caption(watch, frame):
    sighting = watch.look(frame, "DICT_6X6_250")
    items = sighting.items()
    kinds = [type(i) for i in items]
    assert kinds == [Polyline, Polyline, Circle, Text]
    outline, top_edge, corner, caption = items
    assert len(outline.points) == 4 and outline.closed
    # The top edge is the first two corners, and it is not closed: it marks
    # an edge, not a shape.
    assert len(top_edge.points) == 2 and not top_edge.closed
    assert np.allclose(corner.center, np.round(sighting.corners[0]), atol=1)
    assert corner.fill
    assert "id 1" in caption.text


def test_a_dictionary_is_built_once(watch, frame):
    watch.look(frame, "DICT_6X6_250")
    first = watch.detector("DICT_6X6_250")
    watch.look(frame, "DICT_6X6_250")
    assert watch.detector("DICT_6X6_250") is first


# -- the crosshair disc, for the check page ----------------------------------

def test_crosshairs_draw_the_reference_the_points_and_the_choice():
    points = np.array([[100.0, 100.0], [300.0, 100.0], [200.0, 260.0]])
    items = markers.crosshairs(points, reference=(200, 180), chosen=1,
                               outside=np.array([[10.0, 10.0]]))
    kinds = [type(i) for i in items]
    # two arms of the reference cross, the one outside, then the three found
    assert kinds == [Polyline, Polyline, Circle, Circle, Circle, Circle]
    assert all(not i.closed for i in items[:2])
    assert items[0].color == markers.REFERENCE
    assert items[2].color == markers.OUTSIDE
    chosen = items[3 + 1]
    others = [items[3], items[3 + 2]]
    assert chosen.color == markers.CHOSEN
    assert all(o.color == markers.FOUND for o in others)
    # The choice is drawn larger, which is how it reads as a choice.
    assert chosen.radius > others[0].radius


def test_crosshairs_without_a_reference_or_a_choice():
    items = markers.crosshairs(np.array([[5.0, 5.0]]))
    assert [type(i) for i in items] == [Circle]
    assert items[0].color == markers.FOUND


def test_crosshairs_of_nothing_is_nothing():
    assert markers.crosshairs(np.empty((0, 2))) == []
