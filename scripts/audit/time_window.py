"""Test suite audit: what a MainWindow costs a test, phase by phase.

    QT_QPA_PLATFORM=offscreen MICROPICK_ROOT=<empty folder> \
        conda run -n lab python scripts/audit/time_window.py [--themed] [--profile]

Builds the mock MainWindow five times as the `window` fixtures do and
prints the seconds of each phase (the first build pays the imports).
`--themed` applies the program's theme first, as the `themed` fixture
does. `--profile` prints the 25 most expensive calls of one build.
"""

import cProfile
import pstats
import sys
import time

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from micropick.gui.app import Options
from micropick.gui.shell import MainWindow
from micropick.hardware.mock import MockRobot

MockRobot.SPEED_MM_S = 0.0
app = QApplication.instance() or QApplication([])
if "--themed" in sys.argv:
    from micropick.gui import theme
    theme.apply_theme(app)


def once(profile=False):
    marks = [("start", time.perf_counter())]

    def mark(name):
        marks.append((name, time.perf_counter()))

    profiler = cProfile.Profile() if profile else None
    if profiler:
        profiler.enable()
    w = MainWindow(Options(mock=True))
    if profiler:
        profiler.disable()
    mark("MainWindow()")
    w.session.probe_robot()
    w.session.new_run()
    mark("probe + new_run")
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    mark("show")
    w.open_task("liquid")
    w.show_step("liquid_plan")
    app.processEvents()
    mark("open a task step")
    w.hide()
    w.session.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    mark("teardown")
    if profiler:
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
    return [(name, t - marks[i][1]) for i, (name, t) in enumerate(marks[1:])]


for i in range(5):
    phases = once()
    print(f"build {i}: " + "  ".join(f"{n} {s:.3f}" for n, s in phases)
          + f"  total {sum(s for _, s in phases):.3f}")
if "--profile" in sys.argv:
    once(profile=True)
