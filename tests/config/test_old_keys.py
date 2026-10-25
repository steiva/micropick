"""Profiles written by 0.4.0 still load: keys spelled "centre" then are read,
and written back spelled "center"."""
from __future__ import annotations

import json

from micropick.config.schema import CameraHomography, DeckConfig


def test_a_deck_json_with_well_centres_loads_and_is_saved_as_well_centers():
    old = {"modules": [], "well_centres": [
        {"slot": 5, "load_name": "corning_96", "offset": [0.4, -0.3, -1.5],
         "well": "B2"}]}
    deck = DeckConfig.model_validate(old)
    assert deck.well_center(5, "corning_96").offset == [0.4, -0.3, -1.5]
    saved = json.loads(deck.model_dump_json())
    assert "well_centers" in saved and "well_centres" not in saved
    assert DeckConfig.model_validate(saved).well_center(5, "corning_96")


def test_a_homography_with_centre_error_px_loads():
    old = {"matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "gantry_xy": [1, 2],
           "centre_error_px": 0.7}
    homography = CameraHomography.model_validate(old)
    assert homography.center_error_px == 0.7
    assert "center_error_px" in json.loads(homography.model_dump_json())
