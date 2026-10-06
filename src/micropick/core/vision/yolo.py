"""Loading a YOLO model without taking OpenCV's threads away.

`import ultralytics` calls `cv2.setNumThreads(0)` for the whole process - a
guard for PyTorch's DataLoader workers in training, which nothing here does.
After it every cv2 call runs on one core: reducing a 4000x3000 frame for the
screen went from 7 ms to 35 ms, and to 70 ms while a model was predicting,
on the GUI thread, for every frame. The window stayed up but everything in it
that moves - the activity bar first - crawled, from the first Analyse or
pipette calibration until the application was closed.

So the import goes through here, and OpenCV is given back what it had.
"""

from __future__ import annotations

import cv2

__all__ = ["load_yolo"]


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
