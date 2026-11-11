"""What one camera frame costs the GUI thread (GUI audit, A).

    conda run -n lab python scripts/audit/gui/view_cost.py

A `CameraView` of 1200x900 fed by a stand-in camera that hands out a new
array at each read, as a real camera does: the upper camera's 2592x1944,
the lower camera's 4000x3000 whole and at its view crop 0.5, and the clips'
2000x1500. Timed over 60 frames each: `_tick` (read, crop, resize, QImage)
and one paint (`grab()`, offscreen raster), with the status plate, the
mouse row and a step line as on a page. Then the same with 650 contours as
the overlay (a full dish), first paint and later paints.
"""
import time

import numpy as np

import _mock

app = _mock.app()

from micropick.gui.widgets.camera_view import CameraView           # noqa: E402


class Feed:
    label = "upper"
    lost = None

    def __init__(self, width, height, crop=1.0):
        rng = np.random.default_rng(0)
        self.frames = [rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
                       for _ in range(3)]
        self.crop = crop
        self.resolution = (width, height)
        self.n = 0

    def read(self):
        self.n += 1
        return True, self.frames[self.n % 3].copy()   # a new array, as a grab is


def measure(width, height, crop=1.0, contours=0):
    view = CameraView()
    view.resize(1200, 900)
    view.set_position(["X 100.00  Y 100.00  Z 100.00"])
    view.set_step_line(view, "STEP · 1 mm", "#ffffff")
    view.set_status(["23 cuboids found · 9 in size"])
    feed = Feed(width, height, crop)
    view.set_camera(feed)
    view.show()
    app.processEvents()
    if contours:
        import pandas as pd
        from micropick.viz import overlays
        rng = np.random.default_rng(1)
        rows = []
        for _ in range(contours):
            cx, cy = rng.uniform(200, width - 200), rng.uniform(200, height - 200)
            angle = np.linspace(0, 2 * np.pi, 24)
            points = np.stack([cx + 20 * np.cos(angle), cy + 20 * np.sin(angle)], 1)
            rows.append({"contour": points.astype(np.int32).reshape(-1, 1, 2),
                         "cX": cx, "cY": cy})
        view.set_overlay_items(overlays.items((height, width, 3),
                                              cuboid_df=pd.DataFrame(rows)))
    copy = tick = paint = 0.0
    frames = 60
    first_paint = None
    for i in range(frames):
        start = time.perf_counter()
        feed.frames[0].copy()
        copy += time.perf_counter() - start
        start = time.perf_counter()
        view._tick()
        tick += time.perf_counter() - start
        start = time.perf_counter()
        view.grab()
        spent = time.perf_counter() - start
        if first_paint is None:
            first_paint = spent
        else:
            paint += spent
    view.hide()
    view.deleteLater()
    tick_ms = 1000 * (tick - copy) / frames
    paint_ms = 1000 * paint / (frames - 1)
    return tick_ms, paint_ms, 1000 * first_paint


print("| frame | view crop | overlay | `_tick` ms | paint ms | first paint ms | at 30 fps, % of the GUI thread |")
print("|---|---|---|---:|---:|---:|---:|")
for width, height, crop, contours in ((2592, 1944, 1.0, 0), (2592, 1944, 1.0, 650),
                                      (4000, 3000, 1.0, 0), (4000, 3000, 0.5, 0),
                                      (2000, 1500, 0.5, 0)):
    try:
        tick, paint, first = measure(width, height, crop, contours)
    except Exception as exc:                                     # noqa: BLE001
        print(f"| {width}x{height} | {crop} | {contours} | failed: {exc} |")
        continue
    print(f"| {width}x{height} | {crop:g} | {contours or 'none'} | {tick:.1f} | "
          f"{paint:.1f} | {first:.1f} | {(tick + paint) * 30 / 10:.0f} |")
