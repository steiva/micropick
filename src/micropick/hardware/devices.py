"""Enumerating attached cameras.

Indices shuffle between reboots and between USB ports; device names do not. So
the rest of the package addresses cameras by name and resolves the index here,
at open time.

The Windows backend needs pygrabber, which is an optional dependency. Importing
this module on a machine without it, or on Linux or macOS, must not fail: the
core package has to stay importable for tests and for offline work.
"""

from __future__ import annotations

import glob
import platform
import re
import subprocess
from dataclasses import dataclass

__all__ = ["Device", "Mode", "list_devices", "find_device", "device_modes",
           "DeviceError", "DeviceNotFound", "AmbiguousDevice", "backend_name"]


class DeviceError(RuntimeError):
    """Enumeration failed or a requested device could not be resolved."""


class DeviceNotFound(DeviceError):
    pass


class AmbiguousDevice(DeviceError):
    """Several attached devices carry the same name."""


@dataclass(frozen=True)
class Device:
    index: int
    name: str

    def __str__(self) -> str:
        return f"[{self.index}] {self.name}"


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------

def _windows_devices() -> list[Device]:
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError as exc:
        raise DeviceError(
            "listing cameras on Windows needs pygrabber: "
            "pip install -e \".[hardware]\""
        ) from exc
    return [Device(i, n) for i, n in enumerate(FilterGraph().get_input_devices())]


def _linux_devices() -> list[Device]:
    """Reads sysfs directly so v4l-utils is not required."""
    out = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*")):
        idx = int(re.search(r"video(\d+)$", path).group(1))
        try:
            with open(f"{path}/name", encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        out.append(Device(idx, name))
    return out


def _macos_devices() -> list[Device]:
    try:
        raw = subprocess.run(["system_profiler", "SPCameraDataType"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeviceError(f"could not enumerate cameras: {exc}") from exc
    names = re.findall(r"^\s{4}(\S.*?):\s*$", raw, flags=re.MULTILINE)
    return [Device(i, n.strip()) for i, n in enumerate(names)]


_BACKENDS = {"Windows": _windows_devices,
             "Linux": _linux_devices,
             "Darwin": _macos_devices}


def backend_name() -> str:
    return platform.system()


def list_devices() -> list[Device]:
    """All attached cameras, in index order."""
    backend = _BACKENDS.get(platform.system())
    if backend is None:
        raise DeviceError(f"no camera enumeration backend for {platform.system()}")
    return backend()


def find_device(name: str, *, devices: list[Device] | None = None,
                exact: bool = False) -> Device:
    """Resolve a device name to an index.

    Falls back to a case-insensitive substring match when nothing matches
    exactly, because vendors sometimes append a suffix after a driver update.
    Several matches is an error rather than a guess: opening the wrong camera
    produces plausible frames and a silently wrong calibration.
    """
    devices = list_devices() if devices is None else devices
    if not devices:
        raise DeviceNotFound("no cameras attached")

    hits = [d for d in devices if d.name == name]
    if not hits and not exact:
        needle = name.casefold()
        hits = [d for d in devices if needle in d.name.casefold()]

    if not hits:
        listing = "\n  ".join(str(d) for d in devices)
        raise DeviceNotFound(f"no camera named {name!r}. Attached:\n  {listing}")
    if len(hits) > 1:
        listing = "\n  ".join(str(d) for d in hits)
        raise AmbiguousDevice(
            f"{len(hits)} cameras match {name!r}, refusing to guess:\n  {listing}"
        )
    return hits[0]


@dataclass(frozen=True)
class Mode:
    """One format a device offers: size, pixel format, its top frame rate."""

    width: int
    height: int
    fourcc: str
    fps: float

    def __str__(self) -> str:
        return f"{self.width}x{self.height} {self.fourcc} {self.fps:.0f} fps"


def device_modes(index: int) -> list[Mode]:
    """What the device at `index` offers, largest first. Windows only (the
    DirectShow formats, through pygrabber); elsewhere, or when asking
    fails, an empty list - the modes are information, never a reason not
    to open a camera."""
    if platform.system() != "Windows":
        return []
    try:
        from pygrabber.dshow_graph import FilterGraph
        graph = FilterGraph()
        graph.add_video_input_device(index)
        formats = graph.get_input_device().get_formats()
    except Exception:                                # noqa: BLE001
        return []
    modes = set()
    for f in formats:
        # pygrabber's min/max are the frame intervals' ends, so in frames a
        # second the larger of the two is the top rate.
        fps = max(float(f.get("min_framerate") or 0),
                  float(f.get("max_framerate") or 0))
        modes.add(Mode(int(f["width"]), int(f["height"]),
                       str(f.get("media_type_str", "")), round(fps, 1)))
    return sorted(modes, key=lambda m: (m.width * m.height, m.fps),
                  reverse=True)
