"""Looking for the calibration marker on a live frame, and saying what it found.

`MarkerTracker` in `workflows/calibrate_camera` is the sweep's detector: it
waits for the marker to hold still and answers with corners or nothing. This
is the other half, for the step before the sweep — the operator is putting
the marker down and needs to see that it is seen.

Nothing here decides anything. It reports, and the reporting is the point,
because the ways a marker goes undetected look identical on screen:

- **face down, or printed through the paper.** The image is then mirrored,
  and a mirrored marker is in no dictionary at all. This is the one an
  operator cannot see by eye, and the reason this module exists.
- **the wrong dictionary.** A 4X4 marker under a 6X6 detector is not found,
  and the sweep would fail at its first pose with "marker not detected at
  the starting pose". So every dictionary is tried, not only the chosen
  one, and a marker found under another is named rather than left as a
  silence.
- **out of frame, out of focus, too dark.** Nothing distinguishes these
  here, and the message says so rather than guessing.

One detection on a 2592x1944 frame is about 16 ms and all four dictionaries
about 60 ms, so a look is a `Worker`'s job at a few hertz, never the GUI
thread's at thirty.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..viz import markers

__all__ = ["MarkerWatch", "Sighting", "DICTIONARIES"]

# The dictionary the marker was printed from. Offered rather than assumed:
# nothing in the profile records it, `MarkerScene` renders 6X6_250, and a
# detector built on the wrong dictionary finds nothing at all - which looks
# exactly like bad lighting.
DICTIONARIES = {
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_5X5_250": cv2.aruco.DICT_5X5_250,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}


@dataclass(frozen=True)
class Sighting:
    """What one look at one frame found."""

    dictionary: str | None = None            # the one it was found in
    marker_id: int | None = None
    corners: np.ndarray | None = None        # (4, 2), frame pixels
    others: tuple[int, ...] = ()             # further ids in the same frame
    wanted: str = ""                         # the dictionary asked for
    frame_shape: tuple[int, int] | None = None

    @property
    def found(self) -> bool:
        return self.corners is not None

    @property
    def as_asked(self) -> bool:
        """Found, and in the dictionary the sweep is set to use."""
        return self.found and self.dictionary == self.wanted

    @property
    def centre(self) -> np.ndarray | None:
        if self.corners is None:
            return None
        return self.corners.mean(axis=0)

    @property
    def offset_px(self) -> np.ndarray | None:
        """From the frame's centre, which is what step 1 centres on."""
        if self.corners is None or self.frame_shape is None:
            return None
        height, width = self.frame_shape[:2]
        return self.centre - np.array([width / 2.0, height / 2.0])

    @property
    def side_px(self) -> float | None:
        if self.corners is None:
            return None
        c = self.corners
        return float(max(np.linalg.norm(c[i] - c[(i + 1) % 4])
                         for i in range(4)))

    def items(self) -> list:
        """Overlay primitives for the camera view, or nothing."""
        if self.corners is None:
            return []
        return markers.items(self.corners, self.marker_id)

    def describe(self) -> str:
        """One or two sentences for the operator, never a log line."""
        if not self.found:
            return (
                f"No marker in any of {', '.join(DICTIONARIES)}.\n"
                f"A marker lying face down or printed through the paper is "
                f"mirrored, and a mirrored marker is in no dictionary - that "
                f"is the case this cannot tell you any other way. Otherwise "
                f"it is out of frame, out of focus, or too dark.")
        offset = self.offset_px
        where = (f"{np.linalg.norm(offset):.0f} px from the frame centre"
                 if offset is not None else "")
        text = (f"Marker {self.marker_id} found, "
                f"{markers.orientation(self.corners)}, {where}.")
        if not self.as_asked:
            text += (f"\nIt is a {self.dictionary} marker, and the sweep is "
                     f"set to {self.wanted}. Change the dictionary on step 2 "
                     f"or the sweep will not find it either.")
        if self.others:
            text += (f"\nAlso in frame: {', '.join(str(i) for i in self.others)}. "
                     f"The sweep tracks the one nearest the centre.")
        return text


class MarkerWatch:
    """Holds one detector per dictionary and looks with them. Blocking."""

    def __init__(self, dictionaries: dict | None = None):
        self._names = list(dictionaries or DICTIONARIES)
        self._detectors: dict[str, cv2.aruco.ArucoDetector] = {}

    def detector(self, name: str) -> cv2.aruco.ArucoDetector:
        """Built once per dictionary and kept: construction is not free and
        the sweep asks for the same one on every pose."""
        if name not in self._detectors:
            self._detectors[name] = cv2.aruco.ArucoDetector(
                cv2.aruco.getPredefinedDictionary(DICTIONARIES[name]),
                cv2.aruco.DetectorParameters())
        return self._detectors[name]

    def look(self, frame, wanted: str) -> Sighting:
        """The chosen dictionary first, then the rest. Blocking; use a Worker.

        The rest are tried only when the chosen one finds nothing, so the
        ordinary case costs one detection and the case worth explaining
        costs four.
        """
        order = [wanted] + [n for n in self._names if n != wanted]
        shape = tuple(frame.shape[:2])
        for name in order:
            corners, ids, _ = self.detector(name).detectMarkers(frame)
            if ids is None or len(corners) == 0:
                continue
            ids = [int(i) for i in np.asarray(ids).ravel()]
            # The one nearest the centre, which is what MarkerTracker adopts
            # when it is not given an id, so the two agree about which
            # marker is "the" marker.
            centre = np.array([shape[1] / 2.0, shape[0] / 2.0])
            distances = [np.linalg.norm(c.reshape(4, 2).mean(0) - centre)
                         for c in corners]
            best = int(np.argmin(distances))
            return Sighting(
                dictionary=name, marker_id=ids[best],
                corners=corners[best].reshape(4, 2).astype(float),
                others=tuple(i for k, i in enumerate(ids) if k != best),
                wanted=wanted, frame_shape=shape)
        return Sighting(wanted=wanted, frame_shape=shape)
