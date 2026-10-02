"""`micropick-gui --self-test`: does this installation work, without a robot.

A build is a gigabyte of libraries collected by a tool that cannot see
everything a library loads by name, and the first sign of a missing piece
used to be a detector that would not load at the bench. This loads what a
run loads - every model in ml_models/ through ultralytics and torch, a stock
and a custom labware definition, an mp4 written and read back - and says
what passed. No window and no robot: it can be run on a lab computer right
after unzipping. The result goes to the log file and the exit code (0 when
everything passed); a packaged build has no console to print to.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import traceback
from pathlib import Path

__all__ = ["run_self_test"]

log = logging.getLogger(__name__)


def _models() -> str:
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    import numpy as np
    from ultralytics import YOLO

    from . import paths
    weights = sorted(paths.ml_models_dir().glob("*.pt"))
    if not weights:
        raise RuntimeError(f"no weights in {paths.ml_models_dir()}")
    frame = np.zeros((480, 640, 3), np.uint8)
    for path in weights:
        YOLO(str(path)).predict(frame, imgsz=640, verbose=False)
    return ", ".join(p.name for p in weights)


def _labware() -> str:
    from .config.labware import local_definitions, resolve_definition
    stock = resolve_definition("corning_96_wellplate_360ul_flat")
    custom = sorted(local_definitions())
    return f"stock {stock.load_name}; custom: {', '.join(custom) or 'none'}"


def _video() -> str:
    import cv2
    import numpy as np

    from .hardware.camera import Recorder
    recorder = Recorder(max_frames=20)
    recorder.start()
    for i in range(10):
        recorder._offer(np.full((240, 240, 3), i * 20, np.uint8),
                        time.monotonic())
    recorder.stop()
    path = Path(tempfile.mkdtemp()) / "selftest.mp4"
    recorder.save(str(path), fps=10, color=True)
    frames = int(cv2.VideoCapture(str(path)).get(cv2.CAP_PROP_FRAME_COUNT))
    if frames != 10:
        raise RuntimeError(f"wrote 10 frames, read back {frames}")
    return f"{frames} frames"


CHECKS = (("detector models", _models), ("labware", _labware),
          ("pickup clip video", _video))


def run_self_test() -> int:
    """Run every check, log each result, return 0 if all passed."""
    from . import paths
    from ._version import describe
    from .gui import log_bridge
    logging.getLogger().setLevel(logging.INFO)
    log_file = log_bridge.install_file(paths.logs_dir())
    log.info("self-test of micropick %s, data in %s", describe(), paths.root())
    failed = 0
    for name, check in CHECKS:
        try:
            log.info("self-test %s: ok (%s)", name, check())
        except Exception:                            # noqa: BLE001
            failed += 1
            log.error("self-test %s: FAILED\n%s", name, traceback.format_exc())
    log.info("self-test: %s", "all passed" if not failed
             else f"{failed} of {len(CHECKS)} failed")
    print(f"self-test: {'all passed' if not failed else f'{failed} failed'}; "
          f"see {log_file}")
    return 0 if not failed else 1
