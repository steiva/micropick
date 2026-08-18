"""Vision: frames and detections in, tables out.

The package namespace is flat — `micropick.core.vision.classify` rather than
`...vision.floaters.classify` — so the names re-exported here must not collide
across the modules underneath. They do not today, and a new export is only safe
once that has been checked rather than assumed.

Only the floater detector is re-exported, because it is the one whose callers
(the picking session, a notebook measuring the noise floor) want a handful of
names and no module qualifier. `cuboids` and `bubbles` stay behind their module
names: they are pipelines read as a whole, and `vision.detect_boxes` would say
less at the call site than `cuboids.detect_boxes`.
"""

from .floaters import (Baseline, MotionAccumulator, Verdict, Window, classify,
                       exclusion_zones, fit_baseline, make_windows,
                       merge_duplicates, soft_centroid)

__all__ = [
    "Window", "make_windows", "merge_duplicates", "soft_centroid",
    "MotionAccumulator", "Baseline", "fit_baseline", "classify", "Verdict",
    "exclusion_zones",
]
