"""The ONNX runner gives what ultralytics gives, and the device is chosen
by what works and what is faster. A fake session stands in for ONNX
Runtime: what is tested is everything around the network."""
from __future__ import annotations

import numpy as np
import pytest

from micropick.core.vision import yolo
from micropick.core.vision.cuboids import detect_boxes
from micropick.core.vision.onnx_yolo import OnnxYolo, letterbox, nms


class _Meta:
    def __init__(self, names="{0: 'item', 1: 'other'}"):
        self.custom_metadata_map = {"task": "detect", "stride": "32",
                                    "names": names}


class _Input:
    name = "images"

    def __init__(self, shape):
        self.shape = shape


class FakeSession:
    """Answers `rows` (one per anchor: cx, cy, w, h, score per class, in
    network pixels) whatever it is given, and keeps what it was given."""

    def __init__(self, rows, *, shape=(1, 3, "h", "w"), provider="CPUExecutionProvider"):
        self.rows = np.asarray(rows, np.float32).reshape(-1, 6)
        self.shape = shape
        self.provider = provider
        self.fed = None

    def get_modelmeta(self):
        return _Meta()

    def get_inputs(self):
        return [_Input(self.shape)]

    def get_providers(self):
        return [self.provider]

    def run(self, _outputs, feed):
        self.fed = feed["images"]
        return [self.rows.T[None]]                      # (1, 4+nc, anchors)


def test_letterbox_pads_only_to_the_stride_as_a_pt_model_does():
    frame = np.zeros((3000, 4000, 3), np.uint8)
    assert letterbox(frame, 1536, rect=True).shape == (1152, 1536, 3)
    assert letterbox(frame, 1536, rect=False).shape == (1536, 1536, 3)


def test_letterbox_is_ultralytics_own():
    augment = pytest.importorskip("ultralytics.data.augment")
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (1001, 1333, 3), dtype=np.uint8)
    for rect in (True, False):
        theirs = augment.LetterBox((640, 640), auto=rect, stride=32)(image=frame)
        assert np.array_equal(letterbox(frame, 640, rect=rect), theirs)


def test_nms_drops_the_weaker_of_two_that_overlap():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], float)
    scores = np.array([0.5, 0.9, 0.7])
    assert nms(boxes, scores, 0.5).tolist() == [1, 2]
    assert nms(boxes, scores, 0.9).tolist() == [1, 2, 0]


def test_boxes_come_back_in_frame_pixels():
    # A 1000x750 frame at 640 goes in as 640x480: gain 0.64, no padding.
    session = FakeSession([[320, 240, 64, 32, 0.9, 0.0]])
    model = OnnxYolo(session)
    frame = np.zeros((750, 1000, 3), np.uint8)
    r = model.predict(frame, imgsz=640, conf=0.25)[0]
    assert session.fed.shape == (1, 3, 480, 640)
    assert np.allclose(r.boxes.xyxy.cpu().numpy(), [[450, 350, 550, 400]])
    assert r.boxes.conf.cpu().numpy().tolist() == pytest.approx([0.9])


def test_the_array_is_taken_as_bgr_and_flipped_as_ultralytics_does():
    session = FakeSession([[1, 1, 1, 1, 0.0, 0.0]])
    frame = np.zeros((64, 64, 3), np.uint8)
    frame[..., 0] = 255                                 # channel 0 only
    OnnxYolo(session).predict(frame, imgsz=64)
    assert session.fed[0, 2].max() == 1.0 and session.fed[0, 0].max() == 0.0


def test_best_class_over_conf_and_classes_do_not_suppress_each_other():
    rows = [[100, 100, 20, 20, 0.8, 0.1],               # item
            [101, 101, 20, 20, 0.1, 0.7],               # other, same place
            [102, 102, 20, 20, 0.6, 0.0],               # item, overlaps the first
            [300, 300, 20, 20, 0.2, 0.1]]               # under conf
    r = OnnxYolo(FakeSession(rows)).predict(
        np.zeros((640, 640, 3), np.uint8), imgsz=640, conf=0.25, iou=0.5)[0]
    assert r.boxes.cls.cpu().numpy().tolist() == [0.0, 1.0]
    assert r.names == {0: "item", 1: "other"}


def test_max_det_and_the_tip_detectors_way_of_reading_boxes():
    rows = [[50 + 40 * i, 50, 20, 20, 0.9 - 0.01 * i, 0.0] for i in range(10)]
    r = OnnxYolo(FakeSession(rows)).predict(
        np.zeros((640, 640, 3), np.uint8), imgsz=640, max_det=3)[0]
    found = [(float(b.xyxy[0][0]), int(b.cls[0]), float(b.conf[0]))
             for b in r.boxes]
    assert [f[0] for f in found] == [40, 80, 120]


def test_detect_boxes_runs_it():
    from micropick.config.schema import PickingConfig
    model = OnnxYolo(FakeSession([[320, 320, 20, 20, 0.9, 0.0]]))
    boxes, confs = detect_boxes(model, np.zeros((640, 640, 3), np.uint8),
                                PickingConfig(yolo_imgsz=640))
    assert boxes.shape == (1, 4) and confs.shape == (1,)


def test_a_fixed_size_export_refuses_another_size():
    model = OnnxYolo(FakeSession([], shape=(1, 3, 640, 640)))
    with pytest.raises(ValueError, match="exported at 640x640"):
        model.predict(np.zeros((480, 640, 3), np.uint8), imgsz=1536)


# -- choosing the device -----------------------------------------------------

@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    """onnxruntime with the GPUs asked for, sessions that fail or are slow
    as asked for, and an empty .onnx beside an empty .pt."""
    ort = pytest.importorskip("onnxruntime")
    behaviour = {}

    def set_up(gpus, *, fails=(), ms=None):
        monkeypatch.setattr(ort, "get_available_providers",
                            lambda: [*gpus, "CPUExecutionProvider"])
        behaviour.update(fails=set(fails), ms=ms or {})

    def session(path, provider):
        if provider in behaviour["fails"]:
            raise RuntimeError(f"{provider} broke\nsecond line")
        return FakeSession([], provider=provider)

    monkeypatch.setattr(yolo, "_session", session)
    monkeypatch.setattr(yolo, "_timed", lambda model, imgsz:
                        behaviour["ms"].get(model.provider, 100.0))
    monkeypatch.setattr(yolo, "_CHOSEN", {})
    (tmp_path / "m.pt").write_bytes(b"")
    (tmp_path / "m.onnx").write_bytes(b"")
    return set_up, tmp_path / "m.pt"


def test_cpu_only_is_not_measured(fake_runtime):
    set_up, path = fake_runtime
    set_up([])
    loaded = yolo.load_detector(path, device="auto")
    assert loaded.device == "CPU" and loaded.runtime == "ONNX Runtime"
    assert loaded.timings == {}


def test_auto_takes_the_faster(fake_runtime):
    set_up, path = fake_runtime
    set_up(["DmlExecutionProvider"],
           ms={"DmlExecutionProvider": 300.0, "CPUExecutionProvider": 200.0})
    loaded = yolo.load_detector(path, device="auto")
    assert loaded.device == "CPU"
    assert loaded.timings == {"GPU (DirectML)": 300.0, "CPU": 200.0}
    yolo._CHOSEN.clear()
    set_up(["DmlExecutionProvider"],
           ms={"DmlExecutionProvider": 50.0, "CPUExecutionProvider": 200.0})
    assert yolo.load_detector(path, device="auto").device == "GPU (DirectML)"


def test_a_gpu_that_fails_is_left_out_and_said(fake_runtime):
    set_up, path = fake_runtime
    set_up(["DmlExecutionProvider"], fails=["DmlExecutionProvider"])
    for device in ("auto", "gpu"):
        loaded = yolo.load_detector(path, device=device)
        assert loaded.device == "CPU"
        assert loaded.notes == [("GPU (DirectML) left out: "
                                 "DmlExecutionProvider broke")]


def test_cpu_never_tries_a_gpu(fake_runtime):
    set_up, path = fake_runtime
    set_up(["DmlExecutionProvider"], fails=["DmlExecutionProvider"])
    loaded = yolo.load_detector(path, device="cpu")
    assert loaded.device == "CPU" and loaded.notes == []


def test_gpu_asked_for_with_none_says_so(fake_runtime):
    set_up, path = fake_runtime
    set_up([])
    loaded = yolo.load_detector(path, device="gpu")
    assert loaded.device == "CPU"
    assert "no GPU" in loaded.notes[0]


def test_nothing_to_run_it_on_is_an_error(fake_runtime):
    set_up, path = fake_runtime
    set_up([], fails=["CPUExecutionProvider"])
    with pytest.raises(RuntimeError, match="would not run on any device"):
        yolo.load_detector(path, device="cpu")
