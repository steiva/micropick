"""The clip writer: frames go to the file as they come, not into memory."""
from __future__ import annotations

import time

import cv2
import numpy as np

from micropick.hardware import camera as camera_module
from micropick.hardware.camera import ClipWriter


def _feed(writer: ClipWriter, n: int, *, size=(64, 48), fps=30.0) -> None:
    t = time.monotonic()
    for i in range(n):
        frame = np.full((size[1], size[0], 3), (i * 7) % 255, np.uint8)
        writer._offer(frame, t + i / fps)


def _feed_paced(writer: ClipWriter, n: int) -> None:
    """In tens, slower than the writer encodes, so none are dropped."""
    for start in range(0, n, 10):
        _feed(writer, min(10, n - start))
        time.sleep(0.05)


def _frames_in(path) -> int:
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def test_a_clip_is_written_as_it_is_recorded(tmp_path):
    path = tmp_path / "A1.mp4"
    writer = ClipWriter()
    writer.start(path)
    _feed_paced(writer, 40)
    assert writer.recording
    writer.stop()
    assert not writer.recording
    assert writer.wait(10)
    assert _frames_in(path) == 40


def test_frames_wait_in_a_short_queue_never_in_a_list(tmp_path, monkeypatch):
    """The grab thread never blocks: a full queue drops the frame."""
    monkeypatch.setattr(camera_module, "_CLIP_QUEUE_FRAMES", 5)
    monkeypatch.setattr(camera_module, "_video_writer",
                        lambda *a, **k: (_ for _ in ()).throw(
                            camera_module.CameraError("no writer")))
    writer = ClipWriter()
    writer.start(tmp_path / "slow.mp4")
    clip = writer._clip
    assert clip.frames.maxsize == 5
    started = time.monotonic()
    _feed(writer, 500)
    assert time.monotonic() - started < 1.0
    assert clip.frames.qsize() <= 5
    writer.abort()
    assert writer.wait(5)


def test_a_clip_is_cut_at_max_frames(tmp_path):
    path = tmp_path / "long.mp4"
    writer = ClipWriter(max_frames=25)
    writer.start(path)
    _feed_paced(writer, 40)
    writer.stop()
    assert writer.wait(10)
    assert _frames_in(path) == 25


def test_an_aborted_clip_leaves_no_file(tmp_path):
    path = tmp_path / "dropped.mp4"
    writer = ClipWriter()
    writer.start(path)
    _feed(writer, 30)
    time.sleep(0.3)                        # the file is open by now
    writer.abort()
    assert writer.wait(10)
    assert not path.exists()


def test_a_short_clip_is_still_written(tmp_path):
    """Fewer frames than the rate is measured from."""
    path = tmp_path / "short.mp4"
    writer = ClipWriter()
    writer.start(path)
    _feed(writer, 3)
    writer.stop()
    assert writer.wait(10)
    assert _frames_in(path) == 3


def test_rois_are_drawn_on_the_clip_not_on_the_camera_frame(tmp_path):
    path = tmp_path / "boxed.mp4"
    writer = ClipWriter()
    writer.mark_rois([(32, 24)], half=10)
    frame = np.zeros((48, 64, 3), np.uint8)
    writer.start(path)
    for i in range(12):
        writer._offer(frame, time.monotonic() + i / 30)
    writer.stop()
    assert writer.wait(10)
    assert not frame.any()                 # what other consumers see is untouched
    cap = cv2.VideoCapture(str(path))
    ok, shown = cap.read()
    cap.release()
    assert ok and shown[14, 32].mean() > 128   # the box's top edge
