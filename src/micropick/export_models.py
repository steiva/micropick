"""`python -m micropick.export_models`: an ONNX export beside every `.pt`.

What `core.vision.yolo` runs instead of the `.pt` when it is there: through
ONNX Runtime, on the CPU faster than PyTorch, and on any GPU DirectML can
reach. Made on a development computer, which has ultralytics, onnx and
onnxslim (the `ml` and `dev` extras); a build ships the `.onnx` files with
the `.pt` ones, and `packaging/build.py` runs this first.

The input size is left open (`dynamic`), so the network takes the frame
letterboxed only to the next multiple of 32 as the `.pt` does - a 4:3 frame
at 1536 is 1536x1152, not a padded square - and a profile's `yolo_imgsz` or
tip `imgsz` can change without exporting again. Opset 17, which DirectML
runs.

Each export is checked before it is kept: the same frames through the `.pt`
and the `.onnx` must give the same boxes. An export that does not is
deleted and said, and the `.pt` goes on being used as before.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

from . import paths

OPSET = 17
# Boxes from the two may differ by float rounding, not by a pixel.
TOLERANCE_PX = 0.5


def _frames(imgsz: int) -> list[np.ndarray]:
    """Noise and a dish-like picture, 4:3, as RGB the way the callers pass
    them. Noise gives a model something to answer at a low threshold."""
    rng = np.random.default_rng(0)
    w, h = imgsz * 2, imgsz * 3 // 2
    noise = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    dish = np.full((h, w, 3), 40, np.uint8)
    for _ in range(40):
        x, y = int(rng.integers(50, w - 50)), int(rng.integers(50, h - 50))
        dish[y - 12:y + 12, x - 12:x + 12] = 220
    return [noise, dish]


def _same_boxes(pt, onnx, imgsz: int) -> str:
    """"" when the two agree on every test frame, else what differs."""
    for frame in _frames(imgsz):
        kw = {"imgsz": imgsz, "conf": 0.01, "iou": 0.7, "max_det": 600,
              "verbose": False}
        a = pt.predict(frame, **kw)[0].boxes.xyxy.cpu().numpy()
        b = onnx.predict(frame, **kw)[0].boxes.xyxy.cpu().numpy()
        if a.shape != b.shape:
            return f"{len(a)} boxes from the .pt, {len(b)} from the .onnx"
        if len(a):
            worst = np.abs(a[:, None, :] - b[None, :, :]).max(-1).min(1).max()
            if worst > TOLERANCE_PX:
                return f"boxes differ by up to {worst:.2f} px"
    return ""


def export(path: Path, *, force: bool = False) -> str:
    """Export one `.pt`; returns what happened, in a line."""
    target = path.with_suffix(".onnx")
    if (not force and target.is_file()
            and target.stat().st_mtime >= path.stat().st_mtime):
        return f"{target.name}: up to date"
    import onnxruntime as ort

    from .core.vision.onnx_yolo import OnnxYolo
    from .core.vision.yolo import load_yolo
    model = load_yolo(path)
    imgsz = int((getattr(model, "ckpt", None) or {}).get(
        "train_args", {}).get("imgsz", 640))
    made = Path(model.export(format="onnx", imgsz=imgsz, dynamic=True,
                             simplify=True, opset=OPSET))
    if made != target:
        made.replace(target)
    onnx = OnnxYolo(ort.InferenceSession(
        str(target), providers=["CPUExecutionProvider"]), path=str(target))
    problem = _same_boxes(load_yolo(path), onnx, imgsz)
    if problem:
        target.unlink()
        return f"{target.name}: not kept, {problem}"
    return f"{target.name}: exported and checked"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m micropick.export_models",
        description="Export every .pt in ml_models/ (or the files named) "
                    "to ONNX beside it, checked against the .pt.")
    parser.add_argument("weights", nargs="*", type=Path,
                        help="the .pt files; default: all in ml_models/")
    parser.add_argument("--force", action="store_true",
                        help="export again even when the .onnx is newer")
    args = parser.parse_args(argv)
    # ultralytics installs what it thinks is missing unless told not to.
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    weights = args.weights or sorted(paths.ml_models_dir().glob("*.pt"))
    if not weights:
        print(f"no .pt files in {paths.ml_models_dir()}")
        return 1
    failed = 0
    for path in weights:
        try:
            line = export(path, force=args.force)
        except Exception as exc:                          # noqa: BLE001
            line = f"{path.name}: not exported, {exc}"
        failed += "not " in line
        print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
