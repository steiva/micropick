"""What each picking setting is called on screen, and what it does.

The schema's names are for code (`vol`, `one_by_one`) and its comments
cannot be read at run time, so the words an operator sees live here: a
label, a unit, a tooltip, the group it is shown in and, for the few that
are behind most failed pickups and deposits, the symptom that points at
them (`trouble`). The settings dialog still walks the schema
(`PickingConfig.model_fields`), so a field missing here is shown anyway,
under Advanced by its code name - a forgotten entry costs words, never a
setting.

The groups, in order: MAIN, the ones a run is tuned with; RUN, how the run
behaves; then the technical ones, folded under Advanced.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Field", "FIELDS", "BOUNDS", "BIG_CHANGE", "STEPS", "MAIN", "RUN",
           "ADVANCED_GROUPS", "MISS_POLICY",
           "FLOATER_MODE"]

MAIN = "Main"
RUN = "Run"
PICTURE = "The dish in the picture"
DETECTION = "Detection model"
SHAPE = "Shape filters"
BUBBLES = "Bubble filter"
FLOATERS = "Floating cuboids"
TIMING = "Timing, clips and checks"
COORDINATES = "Coordinate destinations"
ADVANCED_GROUPS = (PICTURE, DETECTION, SHAPE, BUBBLES, FLOATERS, TIMING,
                   COORDINATES)


@dataclass(frozen=True)
class Field:
    label: str
    tip: str
    group: str
    unit: str = ""
    trouble: str = ""          # the symptom that points at this setting


# The screen's words for the choices of a literal; the stored value stays.
MISS_POLICY = {"keep_successful": "Deliver what was caught",
               "return_all": "Return everything if any was missed"}
FLOATER_MODE = {"off": "Off", "observe": "Measure only",
                "enforce": "Measure and avoid"}

Z_CALIBRATION = ("Set it on the Picking page, under Dish bottom: jog the tip "
                 "down until it touches the dish, then Set dish bottom here.")

# In MAIN's order, which is the order the dialog shows them in.
FIELDS: dict[str, Field] = {
    # -- main -----------------------------------------------------------------
    "vol": Field(
        "Volume per cuboid",
        "How much the tip draws for each cuboid it picks up, and so how much "
        "goes into the well with each cuboid delivered.",
        MAIN, "µl"),
    "flow_rate": Field(
        "Flow rate",
        "How fast the pipette draws and dispenses, for the pickup, the return "
        "to the dish and the deposit. Too fast pulls neighbouring cuboids "
        "along; too slow may not lift a heavy one.",
        MAIN, "µl/s"),
    "dish_bottom": Field(
        "Dish bottom (Z)",
        "The robot's Z with the tip touching the bottom of the dish. Every "
        "pickup height is measured up from it, and no move in the dish goes "
        "below it. " + Z_CALIBRATION,
        MAIN, "mm",
        trouble="Cuboids are not picked up although the tip goes to them: "
                "the tip probably stops above them - calibrate Z again. The "
                "tip scrapes the dish or comes back blocked: it is set too "
                "low."),
    "pickup_offset": Field(
        "Pickup height above the bottom",
        "How far above the dish bottom the tip opening stops to draw a "
        "cuboid in.",
        MAIN, "mm",
        trouble="Cuboids stay in the dish: lower it (or calibrate Z first). "
                "The tip touches the dish or draws debris: raise it."),
    "cuboid_size_threshold": Field(
        "Cuboid size window (µm)",
        "The diameters a cuboid may have to be picked. The histogram on the "
        "Picking page shows the dish's cuboids against this window.",
        MAIN),
    "failure_threshold": Field(
        "Miss radius",
        "After a pickup the dish is looked at again: a cuboid still within "
        "this distance of where a chosen one was counts as a miss. Too large "
        "and a neighbour drifting in reads as a miss; too small and a cuboid "
        "the tip only nudged reads as picked.",
        MAIN, "mm"),
    "minimum_distance": Field(
        "Minimum spacing",
        "A cuboid with a neighbour closer than this is not picked, so the "
        "tip takes one cuboid and not two. The dish is shaken when too few "
        "are this far apart.",
        MAIN, "mm"),
    "one_by_one": Field(
        "One cuboid per trip",
        "On: pick one cuboid, check it, deliver it, then the next. Off: pick "
        "up to 'Most cuboids per trip' before going to the plate.",
        MAIN),
    "well_offset_x": Field(
        "Well offset X",
        "Where in the well the cuboid is put, from the well's centre, along "
        "X. Zero for a 96-well plate; small wells may need it.",
        MAIN, "mm"),
    "well_offset_y": Field(
        "Well offset Y",
        "As Well offset X, along Y.",
        MAIN, "mm"),
    "deposit_offset_z": Field(
        "Deposit height above the well bottom",
        "How far above the bottom of the well the tip opening is when it "
        "dispenses the cuboid.",
        MAIN, "mm",
        trouble="The cuboid stays in the tip after the deposit: it is too "
                "small - raise it. A drop stays hanging on the tip: the tip "
                "did not reach the liquid - lower it."),

    # -- run ------------------------------------------------------------------
    "miss_policy": Field(
        "When some of a trip are missed",
        "Deliver what was caught: the cuboids that were picked go to the "
        "well first, with their volume, and the volume the missed ones drew "
        "goes back to the dish after. Return everything: any miss sends the "
        "whole trip back to the dish.",
        RUN),
    "max_batch": Field(
        "Most cuboids per trip",
        "How many cuboids are drawn into the tip before it goes to the "
        "plate. Not used with One cuboid per trip.",
        RUN),
    "wait_time_after_deposit": Field(
        "Wait after the deposit",
        "How long the tip stays in the well after dispensing, for the "
        "cuboid to leave it.",
        RUN, "s"),
    "lift_mm": Field(
        "Lift after drawing",
        "How far the tip rises after each aspirate and over each cuboid "
        "before going down to it.",
        RUN, "mm"),
    "max_shake_retries": Field(
        "Shakes before asking",
        "How many times the dish is shaken in a row, when nothing isolated "
        "is found, before the run stops and waits for you.",
        RUN),
    "max_empty_pickups": Field(
        "Empty pickups before asking",
        "How many pickups in a row may come back with nothing before the "
        "run stops and waits for you.",
        RUN),

    # -- the dish in the picture -------------------------------------------------
    "circle_center": Field(
        "Dish centre (px)",
        "The centre of the working circle in the upper camera's picture, "
        "at the picking position.",
        PICTURE),
    "circle_radius": Field(
        "Dish radius",
        "The working circle's radius in the picture. Cuboids outside it are "
        "not picked.",
        PICTURE, "px"),

    # -- detection -------------------------------------------------------------
    "model_file": Field(
        "Model weights",
        "The cuboid detector's weights, a file in ml_models/. Chosen on the "
        "Profile page.",
        DETECTION),
    "yolo_imgsz": Field(
        "Image size",
        "The size the picture is scaled to for the detector. Must match "
        "the size the model was trained at.",
        DETECTION, "px"),
    "yolo_conf": Field(
        "Confidence",
        "The lowest detector confidence kept. Low on purpose: the shape "
        "filters reject afterwards.",
        DETECTION),
    "yolo_iou": Field(
        "Overlap (IoU)",
        "How much two boxes may overlap and both be kept. High so that "
        "touching cuboids stay two.",
        DETECTION),
    "yolo_max_det": Field(
        "Most detections",
        "The most boxes the detector returns for one picture.",
        DETECTION),

    # -- shape -------------------------------------------------------------------
    "otsu_pad": Field(
        "Contour margin",
        "Background around each box when the outline is found; the "
        "threshold needs some.",
        SHAPE, "px"),
    "otsu_open_k": Field(
        "Opening size",
        "Breaks thin bridges from a cuboid to the rim or a neighbour.",
        SHAPE, "px"),
    "aspect_ratio_window": Field(
        "Aspect ratio window",
        "Length over width of the outline's rectangle.",
        SHAPE),
    "circularity_window": Field(
        "Circularity window",
        "How round the outline is; 1 is a circle.",
        SHAPE),
    "min_solidity": Field(
        "Least solidity",
        "Outline area over its convex hull. Catches concave and merged "
        "blobs.",
        SHAPE),
    "max_radial_cv": Field(
        "Most radial spread",
        "How evenly the outline sits around its centre. Accepts circles and "
        "squares, rejects lopsided shapes.",
        SHAPE),

    # -- bubbles ---------------------------------------------------------------
    "bubble_filter_enabled": Field(
        "Reject bubbles",
        "Bubbles pass the shape filters; this rejects them by their dark core "
        "and bright glint. Measured either way, rejected only when on.",
        BUBBLES),
    "bubble_core_r": Field(
        "Core radius",
        "The centre region, as a fraction of the radius.",
        BUBBLES),
    "bubble_ring_window": Field(
        "Rim window",
        "The ring the core is compared against, as fractions of the radius.",
        BUBBLES),
    "bubble_max_core_ratio": Field(
        "Most core brightness",
        "Core over rim brightness. Below it the centre is dark like a "
        "bubble's; cuboids measured 1.00 and above.",
        BUBBLES),
    "bubble_min_spec_ratio": Field(
        "Least glint",
        "Brightest spot over the rim. Above it there is a bubble's glint; "
        "cuboids measured 1.41 and below.",
        BUBBLES),
    "bubble_min_area_px": Field(
        "Least core area",
        "Smaller cores are too few pixels to judge and are left alone.",
        BUBBLES, "px"),
    "bubble_require_both": Field(
        "Both signs needed",
        "On: rejected only with a dark core and a glint. Off: either one "
        "rejects, which spends the thin margin twice.",
        BUBBLES),

    # -- floaters ----------------------------------------------------------------
    "floater_mode": Field(
        "Floating cuboids",
        "Watches the dish for cuboids that drift. Measure only records it; "
        "Measure and avoid also keeps the drifting ones out of the pickup. "
        "Needs a floater baseline in the profile.",
        FLOATERS),
    "floater_window_s": Field(
        "Watch for",
        "How long one measurement watches the dish.",
        FLOATERS, "s"),
    "floater_fps": Field(
        "Frames a second",
        "Frames taken during the watch.",
        FLOATERS),
    "floater_pad_frac": Field(
        "Window size",
        "Each cuboid's window, as a multiple of its size. A measured "
        "optimum; a baseline is only valid at the value it was taken at.",
        FLOATERS),
    "floater_sigma_frac": Field(
        "Window taper",
        "The Gaussian taper of the window, as a fraction of its side. A "
        "measured optimum, tied to the baseline.",
        FLOATERS),
    "floater_k": Field(
        "Threshold factor",
        "A cuboid that moves more than this times the stillest cuboids' "
        "noise is floating.",
        FLOATERS),
    "floater_floor_um": Field(
        "Least threshold",
        "The threshold is never below this.",
        FLOATERS, "µm"),
    "floater_interval_s": Field(
        "Measure every",
        "How often the dish is watched again.",
        FLOATERS, "s"),
    "floater_horizon_s": Field(
        "Drift horizon",
        "How far ahead a floater's drift is allowed for, which sizes the "
        "zone kept clear around it.",
        FLOATERS, "s"),

    # -- timing ------------------------------------------------------------------
    "capture_settle_s": Field(
        "Settle before a picture",
        "Pause at the picking position before the picture a pickup is "
        "decided from.",
        TIMING, "s"),
    "verify_settle_s": Field(
        "Settle before the check",
        "Pause at the picking position before the picture that checks the "
        "pickup.",
        TIMING, "s"),
    "clip_max_frames": Field(
        "Longest clip",
        "The most frames one pickup clip keeps.",
        TIMING, "frames"),
    "homography_drift_warn_mm": Field(
        "Clip box warning distance",
        "The boxes on the clip are drawn with a map fitted at one pose; "
        "further than this from it, the log warns they may be off.",
        TIMING, "mm"),

    # -- coordinates -------------------------------------------------------------
    "deposit_z_optional": Field(
        "Deposit Z",
        "The robot's Z the tip dispenses at when the plate plan's "
        "destination is a list of coordinates rather than a plate.",
        COORDINATES, "mm"),
}


# What a field's box accepts. Wide enough for any real bench and narrow
# enough that a slipped digit - 661 for 66.1, -5 for 5 - cannot be typed.
# A stored value outside its range widens the range to it, so opening the
# dialog never changes a value by itself. Fields not here take anything the
# schema does.
BOUNDS: dict[str, tuple[float, float]] = {
    "vol": (0.5, 100.0),
    "flow_rate": (1.0, 300.0),
    "dish_bottom": (0.0, 150.0),
    "pickup_offset": (0.0, 5.0),
    "cuboid_size_threshold": (10, 5000),
    "failure_threshold": (0.05, 5.0),
    "minimum_distance": (0.0, 10.0),
    "well_offset_x": (-5.0, 5.0),
    "well_offset_y": (-5.0, 5.0),
    "deposit_offset_z": (0.0, 20.0),
    "max_batch": (1, 50),
    "wait_time_after_deposit": (0.0, 60.0),
    "lift_mm": (1.0, 60.0),
    "max_shake_retries": (0, 20),
    "max_empty_pickups": (1, 20),
    "circle_center": (0, 10000),
    "circle_radius": (10, 10000),
    "capture_settle_s": (0.0, 10.0),
    "verify_settle_s": (0.0, 10.0),
    "clip_max_frames": (10, 5000),
    "deposit_z_optional": (0.0, 150.0),
}

# A change this large to one of these is named as such when it is saved:
# 66.1 -> 150 fits the range and is still a slip, not a calibration.
BIG_CHANGE: dict[str, float] = {
    "dish_bottom": 3.0,
    "pickup_offset": 1.0,
    "deposit_offset_z": 2.0,
    "deposit_z_optional": 3.0,
    "vol": 10.0,
}

# What one press of a box's arrows adds, by what the number is: a tenth of a
# millimetre for a height or an offset the tip is placed by, a twentieth for
# the miss radius, hundredths for the shape fractions, 32 px for the
# detector's image size (it works in multiples of 32), tens for pixels and
# microns. Fields not here step by 1.
STEPS: dict[str, float] = {
    "vol": 0.5,
    "flow_rate": 5.0,
    "dish_bottom": 0.1,
    "pickup_offset": 0.1,
    "cuboid_size_threshold": 10,
    "failure_threshold": 0.05,
    "minimum_distance": 0.1,
    "well_offset_x": 0.1,
    "well_offset_y": 0.1,
    "deposit_offset_z": 0.1,
    "wait_time_after_deposit": 0.1,
    "circle_center": 10,
    "circle_radius": 10,
    "yolo_imgsz": 32,
    "yolo_conf": 0.05,
    "yolo_iou": 0.05,
    "yolo_max_det": 50,
    "aspect_ratio_window": 0.05,
    "circularity_window": 0.05,
    "min_solidity": 0.01,
    "max_radial_cv": 0.01,
    "bubble_core_r": 0.05,
    "bubble_ring_window": 0.05,
    "bubble_max_core_ratio": 0.05,
    "bubble_min_spec_ratio": 0.05,
    "floater_window_s": 0.5,
    "floater_pad_frac": 0.1,
    "floater_sigma_frac": 0.05,
    "floater_k": 0.5,
    "capture_settle_s": 0.1,
    "verify_settle_s": 0.1,
    "clip_max_frames": 50,
    "homography_drift_warn_mm": 0.1,
    "deposit_z_optional": 0.1,
}
