"""Which capture backend opens a camera.

On Windows the index comes from DirectShow's list (pygrabber), so a camera
whose spec names no backend is opened through DirectShow: OpenCV 5's own
choice, Media Foundation, numbers the devices otherwise and opened another
camera (2026-11-09).
"""

import cv2
import pytest

from micropick.hardware import camera, devices
from micropick.hardware.camera import CameraManager


class _Cap:
    def __init__(self, index, backend=None):
        self.index, self.backend = index, backend

    def isOpened(self):
        return True

    def set(self, *_):
        return True


@pytest.fixture
def opened(monkeypatch):
    calls = []

    def capture(index, backend=None):
        calls.append(backend)
        return _Cap(index, backend)

    monkeypatch.setattr(camera.cv2, "VideoCapture", capture)
    monkeypatch.setattr(devices, "find_device",
                        lambda name: devices.Device(2, name))
    return calls


def _spec(**kw):
    return {"device_name": "20MP U3 Camera", "default_resolution": [64, 48],
            "resolutions": [[64, 48]], "fourcc": None, **kw}


@pytest.mark.parametrize("platform, expected", [
    ("win32", cv2.CAP_DSHOW), ("linux", None)])
def test_no_backend_named_is_directshow_on_windows(
        opened, monkeypatch, platform, expected):
    monkeypatch.setattr(camera.sys, "platform", platform)
    CameraManager({"up": _spec()})._capture("up", _spec(), (64, 48), {})
    assert opened == [expected]


def test_a_named_backend_is_kept(opened, monkeypatch):
    monkeypatch.setattr(camera.sys, "platform", "win32")
    spec = _spec(backend="msmf")
    CameraManager({"up": spec})._capture("up", spec, (64, 48), {})
    assert opened == [cv2.CAP_MSMF]
