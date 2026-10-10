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

Z_CALIBRATION = ("Set on the Picking page (Set the dish bottom).")

# In MAIN's order, which is the order the dialog shows them in.
FIELDS: dict[str, Field] = {
    # -- main -----------------------------------------------------------------
    "vol": Field(
        "Volume per cuboid",
        "Volume aspirated per cuboid, and dispensed with it.",
        MAIN, "µl"),
    "flow_rate": Field(
        "Flow rate",
        "Aspirate and dispense rate for pickup, return and deposit.",
        MAIN, "µl/s"),
    "dish_bottom": Field(
        "Dish bottom (Z)",
        "Z with the tip touching the dish bottom. Pickup heights are "
        "measured from it. " + Z_CALIBRATION,
        MAIN, "mm",
        trouble="Cuboids are not picked up although the tip goes to them: "
                "the tip probably stops above them - calibrate Z again. The "
                "tip scrapes the dish or comes back blocked: it is set too "
                "low."),
    "pickup_offset": Field(
        "Pickup height above the bottom",
        "Tip height above the dish bottom during pickup.",
        MAIN, "mm",
        trouble="Cuboids stay in the dish: lower it (or calibrate Z first). "
                "The tip touches the dish or draws debris: raise it."),
    "cuboid_size_threshold": Field(
        "Cuboid size window (µm)",
        "Diameter range of cuboids that are picked.",
        MAIN),
    "failure_threshold": Field(
        "Miss radius",
        "A cuboid found within this distance of a picked one after the "
        "pickup counts as a miss.",
        MAIN, "mm"),
    "minimum_distance": Field(
        "Minimum spacing",
        "Cuboids with a neighbour closer than this are not picked.",
        MAIN, "mm"),
    "one_by_one": Field(
        "One cuboid per trip",
        "On: one cuboid per trip. Off: up to 'Most cuboids per trip'.",
        MAIN),
    "well_offset_x": Field(
        "Well offset X",
        "Deposit offset from the well centre (measured centre if set), "
        "along X.",
        MAIN, "mm"),
    "well_offset_y": Field(
        "Well offset Y",
        "As Well offset X, along Y.",
        MAIN, "mm"),
    "deposit_offset_z": Field(
        "Deposit height above the well bottom",
        "Tip height above the well bottom during deposit.",
        MAIN, "mm",
        trouble="The cuboid stays in the tip after the deposit: it is too "
                "small - raise it. A drop stays hanging on the tip: the tip "
                "did not reach the liquid - lower it."),

    # -- run ------------------------------------------------------------------
    "miss_policy": Field(
        "When some of a trip are missed",
        "Deliver what was caught: picked cuboids go to the well, the rest "
        "of the volume back to the dish. Return everything: any miss returns "
        "the whole trip.",
        RUN),
    "max_batch": Field(
        "Most cuboids per trip",
        "Cuboids aspirated before going to the plate.",
        RUN),
    "wait_time_after_deposit": Field(
        "Wait after the deposit",
        "Time in the well after dispensing.",
        RUN, "s"),
    "lift_mm": Field(
        "Lift after drawing",
        "Tip rise after each aspirate and before each descent.",
        RUN, "mm"),
    "max_shake_retries": Field(
        "Shakes before asking",
        "Consecutive shakes without isolated cuboids before the run "
        "pauses.",
        RUN),
    "max_empty_pickups": Field(
        "Empty pickups before asking",
        "Consecutive empty pickups before the run pauses.",
        RUN),
    "stuck_retry": Field(
        "Retry a missed cuboid (experimental)",
        "Retry a missed cuboid once, lower and faster. Missed again: "
        "marked stuck until the next shake.",
        RUN),
    "retry_pickup_offset": Field(
        "Retry: pickup height above the bottom",
        "Tip height above the dish bottom on retry.",
        RUN, "mm"),
    "retry_flow_rate": Field(
        "Retry: flow rate",
        "Aspirate rate on retry.",
        RUN, "µl/s"),

    # -- the dish in the picture -------------------------------------------------
    "circle_center": Field(
        "Dish centre (px)",
        "Working circle centre in the upper camera picture.",
        PICTURE),
    "circle_radius": Field(
        "Dish radius",
        "Working circle radius. Cuboids outside are not picked.",
        PICTURE, "px"),

    # -- detection -------------------------------------------------------------
    "model_file": Field(
        "Model weights",
        "Detector weights in ml_models/ (chosen in Settings).",
        DETECTION),
    "yolo_imgsz": Field(
        "Image size",
        "Detector input size; must match training.",
        DETECTION, "px"),
    "yolo_conf": Field(
        "Confidence",
        "Minimum detector confidence.",
        DETECTION),
    "yolo_iou": Field(
        "Overlap (IoU)",
        "Maximum overlap of two kept boxes.",
        DETECTION),
    "yolo_max_det": Field(
        "Most detections",
        "Maximum boxes per picture.",
        DETECTION),

    # -- shape -------------------------------------------------------------------
    "otsu_pad": Field(
        "Contour margin",
        "Margin around each box for outline detection.",
        SHAPE, "px"),
    "otsu_open_k": Field(
        "Opening size",
        "Morphological opening kernel; removes thin bridges.",
        SHAPE, "px"),
    "aspect_ratio_window": Field(
        "Aspect ratio window",
        "Length over width of the outline's rectangle.",
        SHAPE),
    "circularity_window": Field(
        "Circularity window",
        "Outline roundness; 1 is a circle.",
        SHAPE),
    "min_solidity": Field(
        "Least solidity",
        "Outline area over convex hull area.",
        SHAPE),
    "max_radial_cv": Field(
        "Most radial spread",
        "Spread of outline distance from the centre.",
        SHAPE),

    # -- bubbles ---------------------------------------------------------------
    "bubble_filter_enabled": Field(
        "Reject bubbles",
        "Reject bubbles by dark core and glint.",
        BUBBLES),
    "bubble_core_r": Field(
        "Core radius",
        "Centre region, fraction of the radius.",
        BUBBLES),
    "bubble_ring_window": Field(
        "Rim window",
        "Reference ring, fractions of the radius.",
        BUBBLES),
    "bubble_max_core_ratio": Field(
        "Most core brightness",
        "Core over rim brightness; below it the core counts as dark.",
        BUBBLES),
    "bubble_min_spec_ratio": Field(
        "Least glint",
        "Brightest spot over rim; above it counts as glint.",
        BUBBLES),
    "bubble_min_area_px": Field(
        "Least core area",
        "Smaller cores are not judged.",
        BUBBLES, "px"),
    "bubble_require_both": Field(
        "Both signs needed",
        "On: reject only with both a dark core and a glint. Off: either.",
        BUBBLES),

    # -- floaters ----------------------------------------------------------------
    "floater_mode": Field(
        "Floating cuboids",
        "Detect drifting cuboids. Measure: record only. Measure and avoid: "
        "also skip them. Needs a floater baseline.",
        FLOATERS),
    "floater_window_s": Field(
        "Watch for",
        "Duration of one measurement.",
        FLOATERS, "s"),
    "floater_fps": Field(
        "Frames a second",
        "Frame rate during a measurement.",
        FLOATERS),
    "floater_pad_frac": Field(
        "Window size",
        "Per-cuboid window, multiple of its size. Must match the "
        "baseline.",
        FLOATERS),
    "floater_sigma_frac": Field(
        "Window taper",
        "Gaussian taper, fraction of the window side. Must match the "
        "baseline.",
        FLOATERS),
    "floater_k": Field(
        "Threshold factor",
        "Motion threshold, multiple of the baseline noise.",
        FLOATERS),
    "floater_floor_um": Field(
        "Least threshold",
        "Minimum motion threshold.",
        FLOATERS, "µm"),
    "floater_interval_s": Field(
        "Measure every",
        "Interval between measurements.",
        FLOATERS, "s"),
    "floater_horizon_s": Field(
        "Drift horizon",
        "Drift time allowed for; sets the exclusion zone.",
        FLOATERS, "s"),

    # -- timing ------------------------------------------------------------------
    "capture_settle_s": Field(
        "Settle before a picture",
        "Pause at the picking position before imaging.",
        TIMING, "s"),
    "verify_settle_s": Field(
        "Settle before the check",
        "Pause at the picking position before the check image.",
        TIMING, "s"),
    "clip_max_frames": Field(
        "Longest clip",
        "Maximum frames per pickup clip.",
        TIMING, "frames"),
    "homography_drift_warn_mm": Field(
        "Clip box warning distance",
        "Log a warning when the gantry is further than this from the pose "
        "the clip map was fitted at.",
        TIMING, "mm"),

    # -- coordinates -------------------------------------------------------------
    "deposit_z_optional": Field(
        "Deposit Z",
        "Dispense Z when the destination is a list of coordinates.",
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
    "retry_pickup_offset": (0.0, 5.0),
    "retry_flow_rate": (1.0, 300.0),
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
    "retry_pickup_offset": 0.1,
    "retry_flow_rate": 5.0,
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
