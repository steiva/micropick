"""The check on a target's Z against the bottom of its slot (#101), on its
own: how far under a bottom the tip may be sent, so that it can still press
lightly on it."""

import pytest

from micropick.workflows.manual import under_the_bottom

MEASURED, DRAWN = 4.22, 3.55


def test_a_target_above_the_bottom_has_no_reason():
    assert under_the_bottom(10.0, MEASURED, 5, measured=True) == ""
    assert under_the_bottom(10.0, DRAWN, 5, measured=False) == ""


def test_a_target_on_the_bottom_has_no_reason():
    assert under_the_bottom(MEASURED, MEASURED, 5, measured=True) == ""


@pytest.mark.parametrize("z", [3.72, 3.22])
def test_up_to_1_mm_under_a_measured_bottom_is_allowed(z):
    assert under_the_bottom(z, MEASURED, 5, measured=True) == ""


def test_past_1_mm_under_a_measured_bottom_is_refused():
    assert under_the_bottom(3.21, MEASURED, 5, measured=True) == \
        "Z 3.21 is below the measured bottom in slot 5"


@pytest.mark.parametrize("z", [2.55, 1.55])
def test_up_to_2_mm_under_a_drawings_bottom_is_allowed(z):
    assert under_the_bottom(z, DRAWN, 5, measured=False) == ""


def test_past_2_mm_under_a_drawings_bottom_is_refused():
    assert under_the_bottom(1.54, DRAWN, 5, measured=False) == \
        "Z 1.54 is below the drawing's bottom in slot 5"


def test_the_limit_depends_on_the_kind_of_bottom():
    assert under_the_bottom(2.0, 3.5, 5, measured=False) == ""
    assert under_the_bottom(2.0, 3.5, 5, measured=True) != ""


def test_a_z_on_the_limit_is_not_refused_for_rounding():
    # 2.14 - 1.14 is 1.0000000000000002 in floats.
    assert under_the_bottom(1.14, 2.14, 5, measured=True) == ""
