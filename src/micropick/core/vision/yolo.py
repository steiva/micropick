"""Loading a YOLO model, and choosing what runs it.

ONNX first
----------
`load_detector` takes the weights a profile names (`name.pt`) and runs the
ONNX export beside them (`name.onnx`, made by `micropick.export_models`)
through ONNX Runtime when there is one: the same boxes (`onnx_yolo`), and
faster on a CPU than PyTorch. Without an export, or without onnxruntime,
it is the `.pt` through ultralytics as before. A profile may name the
`.onnx` itself, for an installation with no PyTorch at all.

Which device: Auto, CPU or GPU
------------------------------
The computer's setting (`AppSettings.inference_device`). A GPU is any that
ONNX Runtime can reach: DirectML on Windows (any DirectX 12 card, NVIDIA,
AMD or Intel, built into the processor or not), CUDA with the CUDA build,
Core ML on a Mac. "GPU" takes the first that works, "CPU" never tries one.

"Auto" measures. A GPU is not always the faster - the graphics built into
a processor can lose to its own cores on a 1536-pixel input - and a table
of processors would be out of date before it was useful. So every device
that works runs the model on a blank frame of the size it will be given,
and the fastest is used: seconds, once per model per start of the
application, and none at all on a computer with only a CPU. A device that
fails to start or to run is left out and named in the log, so a broken
driver costs speed, never the run.

`import ultralytics` calls `cv2.setNumThreads(0)` for the whole process - a
guard for PyTorch's DataLoader workers in training, which nothing here does.
After it every cv2 call runs on one core: reducing a 4000x3000 frame for the
screen went from 7 ms to 35 ms, and to 70 ms while a model was predicting,
on the GUI thread, for every frame. So the import goes through `load_yolo`,
and OpenCV is given back what it had.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

__all__ = ["load_yolo", "load_detector", "Loaded", "DEVICES", "LOADED",
           "gpus"]

log = logging.getLogger(__name__)

DEVICES = ("auto", "cpu", "gpu")

_CPU = "CPUExecutionProvider"
# The order a "GPU" setting tries them in.
_GPUS = {"CUDAExecutionProvider": "GPU (CUDA)",
         "DmlExecutionProvider": "GPU (DirectML)",
         "CoreMLExecutionProvider": "GPU (Core ML)"}
# Runs on a blank frame for Auto's measurement, after one to warm up.
_TIMED_RUNS = 2

# The last load of each weights file, by its name: what the Settings page
# shows. Written by whichever thread loads; read for display only.
LOADED: dict[str, Loaded] = {}
# Auto's choice, kept for the life of the process: (path, mtime, imgsz,
# providers) -> provider.
_CHOSEN: dict[tuple, str] = {}


@dataclass
class Loaded:
    """A model ready for `predict`, and what runs it."""

    model: object
    runtime: str                      # "ONNX Runtime" or "PyTorch"
    device: str                       # "CPU", "GPU (DirectML)", ...
    timings: dict[str, float] = field(default_factory=dict)   # device -> ms
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        text = f"{self.device}, {self.runtime}"
        if self.timings:
            text += " (" + ", ".join(f"{d} {ms:.0f} ms"
                                     for d, ms in self.timings.items()) + ")"
        return text


def load_yolo(path):
    """`ultralytics.YOLO(path)`, with cv2's thread count as it was before the
    import. Seconds on a first call: run it in a worker. Raises ImportError
    without ultralytics."""
    threads = cv2.getNumThreads()
    try:
        from ultralytics import YOLO
    finally:
        cv2.setNumThreads(threads)
    return YOLO(str(path))


def load_detector(path, *, device: str = "auto", imgsz: int = 640) -> Loaded:
    """The model at `path` on the device the setting asks for. Blocking,
    seconds: run it in a worker. Raises FileNotFoundError without the
    weights and ImportError without anything to run them with."""
    if device not in DEVICES:
        raise ValueError(f"device {device!r} is not one of {DEVICES}")
    path = Path(path)
    export = path if path.suffix == ".onnx" else path.with_suffix(".onnx")
    if export.is_file() and _have_onnxruntime():
        loaded = _load_onnx(export, device, int(imgsz))
    elif path.suffix == ".onnx":
        if not path.is_file():
            raise FileNotFoundError(f"no weights at {path}")
        raise ImportError("onnxruntime is not installed, and it is what "
                          f"runs {path.name}")
    else:
        if not path.is_file():
            raise FileNotFoundError(f"no weights at {path}")
        loaded = _load_torch(path, device)
    LOADED[path.name] = loaded
    log.info("%s on %s", path.name, loaded)
    for note in loaded.notes:
        log.warning("%s: %s", path.name, note)
    return loaded


def gpus() -> list[str]:
    """The GPUs ONNX Runtime offers on this computer, by name ("GPU
    (DirectML)"); empty without one or without onnxruntime. Offered is not
    working: a load finds that out."""
    if not _have_onnxruntime():
        return []
    import onnxruntime as ort
    available = ort.get_available_providers()
    return [label for provider, label in _GPUS.items() if provider in available]


def _have_onnxruntime() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _load_torch(path: Path, device: str) -> Loaded:
    """The `.pt` through ultralytics, which takes CUDA when PyTorch has it."""
    model = load_yolo(path)
    import torch
    gpu = torch.cuda.is_available() and device != "cpu"
    model.overrides["device"] = "0" if gpu else "cpu"
    notes = [(f"no {path.with_suffix('.onnx').name} beside it: PyTorch, "
              f"which is slower on a CPU")]
    if device == "gpu" and not gpu:
        notes.append("no GPU that PyTorch can use: the CPU instead")
    return Loaded(model, "PyTorch", "GPU (CUDA)" if gpu else "CPU",
                  notes=notes)


def _label(provider: str) -> str:
    return _GPUS.get(provider, "CPU")


def _session(path: Path, provider: str):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # A device that fails is said once, in our log, not by ONNX Runtime too.
    options.log_severity_level = 4
    if provider == "DmlExecutionProvider":
        # What DirectML requires of a session.
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    providers = [provider] + ([_CPU] if provider != _CPU else [])
    session = ort.InferenceSession(str(path), options, providers=providers)
    if session.get_providers()[0] != provider:
        # ONNX Runtime falls back to the CPU quietly when a device will not
        # start; that is not the device asked for.
        raise RuntimeError(f"{_label(provider)} did not start")
    return session


def _blank(imgsz: int) -> np.ndarray:
    """A frame of the shape the cameras give (4:3), letterboxed as a real
    one would be."""
    return np.zeros((imgsz * 3 // 4, imgsz, 3), np.uint8)


def _timed(model, imgsz: int) -> float:
    """Milliseconds a frame, the fastest of a few after a warm-up run."""
    frame = _blank(imgsz)
    model.predict(frame, imgsz=imgsz)
    best = float("inf")
    for _ in range(_TIMED_RUNS):
        start = time.perf_counter()
        model.predict(frame, imgsz=imgsz)
        best = min(best, time.perf_counter() - start)
    return best * 1000.0


def _load_onnx(path: Path, device: str, imgsz: int) -> Loaded:
    import onnxruntime as ort

    from .onnx_yolo import OnnxYolo
    available = ort.get_available_providers()
    gpus = [p for p in _GPUS if p in available]
    candidates = {"cpu": [_CPU], "gpu": gpus + [_CPU],
                  "auto": gpus + [_CPU]}[device]
    key = (str(path), path.stat().st_mtime, imgsz, tuple(available))
    if device == "auto" and key in _CHOSEN:
        candidates = [_CHOSEN[key]]

    notes: list[str] = []
    if device == "gpu" and not gpus:
        notes.append("no GPU that ONNX Runtime can use: the CPU instead")
    measure = device == "auto" and len(candidates) > 1
    timings: dict[str, float] = {}
    chosen = None
    for provider in candidates:
        try:
            model = OnnxYolo(_session(path, provider), path=str(path))
            # Run once whatever the setting: a device that starts and then
            # fails on the first frame is found here, not in a run.
            if measure:
                timings[_label(provider)] = _timed(model, imgsz)
            else:
                model.predict(_blank(imgsz), imgsz=imgsz)
        except Exception as exc:                          # noqa: BLE001
            reason = (str(exc).splitlines() or [type(exc).__name__])[0]
            notes.append(f"{_label(provider)} left out: {reason[:160]}")
            continue
        if not measure:
            chosen = (provider, model)
            break
        if chosen is None or timings[_label(provider)] < timings[_label(chosen[0])]:
            chosen = (provider, model)
    if chosen is None:
        raise RuntimeError(f"{path.name} would not run on any device: "
                           + "; ".join(notes))
    provider, model = chosen
    if device == "auto":
        _CHOSEN[key] = provider
    return Loaded(model, "ONNX Runtime", _label(provider), timings, notes)
