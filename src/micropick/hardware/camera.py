"""Camera capture.

One camera, one grab thread. The thread runs from open to close and always
holds the newest frame, so read() in a notebook is instant and never
reinitialises the device.

Frames are handed out by reference, not copied through shared memory. At
2592x1944 a frame is 15 MB; the shared-memory route in the previous version
cost about 7.7 ms per frame in pure memcpy, roughly a quarter of a core at
30 fps. A thread costs nothing because cv2.VideoCapture.read releases the GIL
while it blocks, so the notebook is not held up either way.

Consumers subscribe to the single grab loop rather than starting loops of their
own. Two loops calling read() on one device compete for frames and each sees
only part of the stream.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from . import devices

__all__ = ["BackgroundCamera", "Recorder", "CameraManager", "CameraError",
           "ControlReport", "apply_controls"]

Transform = Callable[[np.ndarray], np.ndarray]
Annotate = Callable[[np.ndarray], np.ndarray]


class CameraError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# device controls
# ---------------------------------------------------------------------------

# Auto exposure encodings differ between DirectShow and V4L2 and between
# OpenCV builds, and a rejected value is not reported. Each candidate is tried
# and read back until one sticks.
# 0.25/0.75 is the DirectShow pair, 1/3 the V4L2 pair. Values such as 0 are
# deliberately excluded: many drivers report 0 for a property they do not
# support, which would make the read-back check pass on a control that was
# silently ignored.
_AUTO_EXPOSURE = {"manual": (0.25, 1), "auto": (0.75, 3)}

_CONTROL_PROPS = {
    "autofocus": cv2.CAP_PROP_AUTOFOCUS,
    "focus": cv2.CAP_PROP_FOCUS,
    "exposure": cv2.CAP_PROP_EXPOSURE,
    "gain": cv2.CAP_PROP_GAIN,
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION,
    "gamma": cv2.CAP_PROP_GAMMA,
    "auto_wb": cv2.CAP_PROP_AUTO_WB,
    "wb_temperature": cv2.CAP_PROP_WB_TEMPERATURE,
}

# Autofocus has to be off before a focus value means anything, and exposure
# mode before an exposure value.
_CONTROL_ORDER = ["auto_exposure", "exposure", "gain", "autofocus", "focus",
                  "auto_wb", "wb_temperature", "brightness", "contrast",
                  "saturation", "gamma"]


@dataclass
class ControlReport:
    """What actually took effect. UVC drivers accept a set() and ignore it, so
    every control is read back and compared."""

    applied: dict[str, float] = field(default_factory=dict)
    rejected: dict[str, tuple[float, float]] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected and not self.unsupported

    def __str__(self) -> str:
        lines = []
        if self.applied:
            lines.append("applied:    " + ", ".join(
                f"{k}={v:g}" for k, v in self.applied.items()))
        if self.rejected:
            lines.append("NOT applied: " + ", ".join(
                f"{k}: asked {a:g}, got {g:g}" for k, (a, g) in self.rejected.items()))
        if self.unsupported:
            lines.append("unknown control names: " + ", ".join(self.unsupported))
        return "\n".join(lines) or "no controls requested"


def _set_auto_exposure(cap, mode: str, report: ControlReport) -> None:
    candidates = _AUTO_EXPOSURE.get(str(mode).lower())
    if candidates is None:
        report.unsupported.append(f"auto_exposure={mode!r}")
        return
    for value in candidates:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, value)
        got = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        if abs(got - value) < 1e-3:
            report.applied["auto_exposure"] = value
            return
    report.rejected["auto_exposure"] = (candidates[0],
                                        cap.get(cv2.CAP_PROP_AUTO_EXPOSURE))


def apply_controls(cap, controls: dict, *, tol: float = 1e-2) -> ControlReport:
    """Set controls in a dependency-safe order and verify each one."""
    report = ControlReport()
    for name in _CONTROL_ORDER:
        if name not in controls:
            continue
        value = controls[name]
        if name == "auto_exposure":
            _set_auto_exposure(cap, value, report)
            continue
        prop = _CONTROL_PROPS[name]
        cap.set(prop, float(value))
        got = cap.get(prop)
        if abs(got - float(value)) <= max(tol, abs(float(value)) * tol):
            report.applied[name] = got
        else:
            report.rejected[name] = (float(value), got)

    for name in controls:
        if name not in _CONTROL_ORDER:
            report.unsupported.append(name)
    return report


# ---------------------------------------------------------------------------
# recorder
# ---------------------------------------------------------------------------

class Recorder:
    """Accumulates frames from a camera's grab loop.

    Owns no thread and no device: the camera pushes into it. That removes the
    duplicate read loop of the previous two recorder classes and makes it
    impossible to miss a frame the camera saw.

    transform runs on every frame the camera delivers and its output is what
    gets stored, so cropping and greyscaling happen once rather than per
    consumer. annotate runs only on stored frames, for markers that should not
    contaminate what other consumers see.

    The buffer is always bounded. At 2592x1944 greyscale a frame is 5 MB, so an
    unbounded list fills memory in under a minute.
    """

    def __init__(self, *, max_frames: int = 600,
                 transform: Transform | None = None,
                 annotate: Annotate | None = None):
        self.max_frames = int(max_frames)
        self.transform = transform
        self.annotate = annotate
        self.frames: deque = deque(maxlen=self.max_frames)
        self.timestamps: deque = deque(maxlen=self.max_frames)
        self.dropped = 0
        self._t0: float | None = None
        self._active = threading.Event()
        self._lock = threading.Lock()
        self._rois: list[tuple[int, int, int]] = []

    # -- called by the camera thread ---------------------------------------

    def _offer(self, frame: np.ndarray, stamp: float) -> None:
        if not self._active.is_set():
            return
        if self.transform is not None:
            frame = self.transform(frame)
        with self._lock:
            rois = list(self._rois)
            if len(self.frames) == self.max_frames:
                self.dropped += 1
        if rois:
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            else:
                frame = frame.copy()
            for cx, cy, half in rois:
                cv2.rectangle(frame, (cx - half, cy - half),
                              (cx + half, cy + half), (255, 255, 255), 2)
        if self.annotate is not None:
            frame = self.annotate(frame)
        with self._lock:
            self.frames.append(frame)
            self.timestamps.append(stamp - self._t0)

    # -- control ------------------------------------------------------------

    def start(self) -> "Recorder":
        with self._lock:
            self.frames.clear()
            self.timestamps.clear()
            self.dropped = 0
        self._t0 = time.monotonic()
        self._active.set()
        return self

    def stop(self) -> tuple[list, list]:
        self._active.clear()
        with self._lock:
            return list(self.frames), list(self.timestamps)

    @property
    def recording(self) -> bool:
        return self._active.is_set()

    def mark_rois(self, points, half: int = 60) -> None:
        """Draw a box around each point on recorded frames.

        A pickup visits every cuboid of its batch, so a single box would mark
        one of several. Coordinates are in the frame the recorder stores, which
        is post-transform: a cropping transform means the caller shifts them by
        the crop origin first.
        """
        with self._lock:
            self._rois = [(int(x), int(y), int(half)) for x, y in points]

    def mark_roi(self, cx: float, cy: float, half: int = 60) -> None:
        """One box, for callers that only ever have one point."""
        self.mark_rois([(cx, cy)], half)

    def clear_roi(self) -> None:
        with self._lock:
            self._rois = []

    def __enter__(self) -> "Recorder":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- output -------------------------------------------------------------

    def measured_fps(self) -> float:
        with self._lock:
            if len(self.timestamps) < 2 or self.timestamps[-1] <= 0:
                return 0.0
            return (len(self.timestamps) - 1) / self.timestamps[-1]

    def save(self, path: str, *, fps: float | None = None,
             color: bool = False) -> str:
        with self._lock:
            frames = list(self.frames)
        if not frames:
            raise CameraError("nothing recorded")
        fps = fps or self.measured_fps() or 20.0
        h, w = frames[0].shape[:2]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        ext = os.path.splitext(path)[1].lower()
        fourcc = cv2.VideoWriter_fourcc(*("avc1" if ext == ".mp4" else "XVID"))
        writer = cv2.VideoWriter(path, fourcc, fps, (w, h), isColor=color)
        if not writer.isOpened():
            raise CameraError(f"could not open a writer for {path}")
        try:
            for fr in frames:
                if color and fr.ndim == 2:
                    fr = cv2.cvtColor(fr, cv2.COLOR_GRAY2BGR)
                elif not color and fr.ndim == 3:
                    fr = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                writer.write(fr)
        finally:
            writer.release()
        return path

    def save_async(self, path: str, *, fps: float | None = None,
                   color: bool = False,
                   on_done: Callable[[str], None] | None = None) -> threading.Thread:
        """Encoding a clip takes seconds; this keeps the picking loop moving."""
        def worker():
            try:
                self.save(path, fps=fps, color=color)
            except Exception as exc:
                print(f"clip not saved: {exc}")
                return
            if on_done:
                on_done(path)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return thread

    def __repr__(self) -> str:
        return (f"<Recorder {'recording' if self.recording else 'idle'}, "
                f"{len(self.frames)}/{self.max_frames} frames"
                + (f", {self.dropped} dropped" if self.dropped else "") + ">")


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------

class BackgroundCamera:
    """A camera held open by one grab thread.

    `crop` is carried, never applied. It is the fraction of the frame worth
    looking at when this camera is shown or recorded, and read()/read_after()
    keep handing out whole sensor frames regardless: cropping what detection and
    calibration see is exactly the mistake the previous version made, and it
    moved every coordinate fitted from those frames.
    """

    def __init__(self, cap, *, label: str = "camera",
                 resolution: tuple[int, int] | None = None,
                 controls: ControlReport | None = None,
                 crop: float = 1.0, warmup: int = 5):
        self._cap = cap
        self.label = label
        self.controls = controls or ControlReport()
        self.crop = float(crop)

        actual = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                  int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        if resolution is not None and tuple(resolution) != actual:
            cap.release()
            raise CameraError(
                f"{label}: asked for {tuple(resolution)}, device gave {actual}. "
                f"Refusing to resize: a silent scale change invalidates the "
                f"pixel map."
            )
        self._resolution = actual

        # Keep the last warm-up frame rather than discarding it: without a
        # seed, read() returns False for the first few milliseconds after
        # construction, and a caller that reads immediately sees no camera.
        seed = None
        for _ in range(max(1, warmup)):
            ok, frame = cap.read()
            if ok:
                seed = frame
        if seed is None:
            cap.release()
            raise CameraError(f"{label}: no frame during warm-up")

        self._latest: np.ndarray | None = seed
        self._stamp = time.monotonic()
        self._count = 0
        self._error: str | None = None
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._sinks: list[Recorder] = []
        self._sink_lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"grab-{label}")
        self._thread.start()

    # -- grab loop ----------------------------------------------------------

    def _loop(self) -> None:
        misses = 0
        while not self._stop.is_set():
            # Stamped before the call, so the guarantee read_after gives is
            # "pulled from the driver no earlier than t".
            t_begin = time.monotonic()
            ok, frame = self._cap.read()
            if not ok:
                misses += 1
                if misses > 50:
                    with self._cv:
                        self._error = "camera stopped returning frames"
                        self._cv.notify_all()
                    break
                continue
            misses = 0

            with self._cv:
                self._latest = frame
                self._stamp = t_begin
                self._count += 1
                self._cv.notify_all()

            with self._sink_lock:
                sinks = list(self._sinks)
            for sink in sinks:
                sink._offer(frame, t_begin)

        self._cap.release()

    # -- reading ------------------------------------------------------------

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Most recent frame. May predate whatever the caller just did."""
        with self._cv:
            if self._error or self._latest is None:
                return False, None
            return True, self._latest

    def read_after(self, t: float, *, skip: int = 0,
                   timeout: float = 2.0) -> np.ndarray:
        """First frame pulled from the driver after t.

        Replaces guessing at a sleep: after a move completes, take t =
        time.monotonic() and ask for a frame stamped later than it.

        skip discards that many further frames. The stamp marks when the frame
        was pulled, not when it was exposed, so a driver that queues frames can
        still hand back one exposed slightly earlier. Measure the lag once and
        set skip from what you see.
        """
        deadline = time.monotonic() + timeout
        with self._cv:
            for _ in range(skip + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cv.wait_for(
                        lambda: self._stamp > t or self._error is not None,
                        timeout=remaining):
                    raise TimeoutError(
                        f"{self.label}: no frame after t within {timeout} s")
                if self._error:
                    raise CameraError(f"{self.label}: {self._error}")
                t = self._stamp
            return self._latest

    def snapshot(self) -> np.ndarray:
        """A private copy, for when the caller intends to draw on it."""
        ok, frame = self.read()
        if not ok:
            raise CameraError(f"{self.label}: no frame available")
        return frame.copy()

    # -- subscribers --------------------------------------------------------

    def record(self, *, max_frames: int = 600,
               transform: Transform | None = None,
               annotate: Annotate | None = None) -> Recorder:
        """Attach a recorder. Use as a context manager to record a clip."""
        rec = Recorder(max_frames=max_frames, transform=transform,
                       annotate=annotate)
        self.attach(rec)
        return rec

    def attach(self, sink: Recorder) -> Recorder:
        with self._sink_lock:
            self._sinks.append(sink)
        return sink

    def detach(self, sink: Recorder) -> None:
        with self._sink_lock:
            if sink in self._sinks:
                self._sinks.remove(sink)

    # -- state --------------------------------------------------------------

    @property
    def resolution(self) -> tuple[int, int]:
        return self._resolution

    @property
    def frame_count(self) -> int:
        with self._cv:
            return self._count

    def is_alive(self) -> bool:
        return self._thread.is_alive() and self._error is None

    def measure_fps(self, seconds: float = 1.0) -> float:
        start = self.frame_count
        time.sleep(seconds)
        return (self.frame_count - start) / seconds

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)

    def __enter__(self) -> "BackgroundCamera":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "running" if self.is_alive() else f"stopped ({self._error})"
        return (f"<BackgroundCamera {self.label!r} {self._resolution[0]}x"
                f"{self._resolution[1]} {state}, {self.frame_count} frames>")


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------

class CameraManager:
    """Opens cameras by label using the profile's camera section.

    Indices are never stored: they move between reboots and USB ports. The
    label maps to a device name, and the name is resolved to an index at open
    time.
    """

    def __init__(self, cameras: dict, *, backend: int | None = None):
        self.cameras = cameras
        self.backend = backend
        self._open: dict[str, BackgroundCamera] = {}

    @classmethod
    def from_profile(cls, profile, **kwargs) -> "CameraManager":
        return cls({k: v.model_dump() if hasattr(v, "model_dump") else v
                    for k, v in (profile.cameras or {}).items()}, **kwargs)

    def labels(self) -> list[str]:
        return sorted(self.cameras)

    def spec(self, label: str) -> dict:
        try:
            return self.cameras[label]
        except KeyError:
            raise CameraError(
                f"no camera labelled {label!r} in the profile; "
                f"known: {', '.join(self.labels()) or 'none'}"
            ) from None

    def open(self, label: str, *, resolution: tuple[int, int] | None = None,
                controls: dict | None = None, reuse: bool = True,
                verbose: bool = True) -> BackgroundCamera:
        spec = self.spec(label)
        wanted = tuple(resolution) if resolution else tuple(spec["default_resolution"])

        existing = self._open.get(label)
        if existing is not None and existing.is_alive():
            if not reuse:
                self.close(label)
            elif existing.resolution == wanted:
                return existing
            else:
                # Reopening rather than returning the old one: silently handing
                # back a camera at the wrong resolution would misplace every
                # coordinate derived from it, with nothing to show for it.
                if verbose:
                    print(f"{label}: reopening {existing.resolution} -> {wanted}")
                self.close(label)

        device = devices.find_device(spec["device_name"])
        allowed = [tuple(r) for r in spec.get("resolutions", [])]
        if allowed and wanted not in allowed:
            raise CameraError(
                f"{label}: resolution {wanted} is not in the profile's list "
                f"{allowed}. Add it there if the camera supports it."
            )

        cap = (cv2.VideoCapture(device.index, self.backend) if self.backend
               else cv2.VideoCapture(device.index))
        if not cap.isOpened():
            raise CameraError(f"{label}: could not open {device}")

        # fourcc before size: on DirectShow the pixel format constrains which
        # resolutions the device will report as available.
        fourcc = spec.get("fourcc")
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, wanted[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, wanted[1])
        if spec.get("fps"):
            cap.set(cv2.CAP_PROP_FPS, spec["fps"])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        merged = dict(spec.get("controls") or {})
        merged.update(controls or {})
        report = apply_controls(cap, merged) if merged else ControlReport()

        cam = BackgroundCamera(cap, label=label, resolution=wanted,
                               controls=report,
                               crop=float(spec.get("crop", 1.0)))
        self._open[label] = cam

        if verbose:
            print(f"{label}: {device}  {wanted[0]}x{wanted[1]}"
                  + (f"  view crop {cam.crop:g}" if cam.crop != 1.0 else ""))
            if merged:
                print(f"  {report}".replace("\n", "\n  "))
        return cam

    def close(self, label: str) -> None:
        cam = self._open.pop(label, None)
        if cam is not None:
            cam.close()

    def close_all(self) -> None:
        for label in list(self._open):
            self.close(label)

    def __enter__(self) -> "CameraManager":
        return self

    def __exit__(self, *exc) -> None:
        self.close_all()
