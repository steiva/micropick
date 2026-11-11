"""CPU used by the mock GUI doing nothing, under the real event loop (GUI-01).

    conda run -n lab python scripts/audit/gui/cpu_idle.py [--seconds 10] [--count] [--no-robot]

No instrumentation unless `--count`: the MainWindow under `--mock`, Home on
screen, `app.exec()` for N seconds, then the process's CPU time over the
wall time (all threads) and the GUI thread's own. `--count` also counts
`_ScrollColumn._fit_height` per column and names the page each column is on.
`--screen manual` measures Manual pick's Run instead of Home; `--fix A` or
`--fix B` applies a candidate fix first (`fit_fix.py`).
"""
import argparse
import collections
import os
import resource
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MICROPICK_ROOT", tempfile.mkdtemp(prefix="micropick-audit-"))

from PySide6.QtCore import QTimer                                  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=float, default=10.0)
parser.add_argument("--count", action="store_true")
parser.add_argument("--no-robot", action="store_true")
parser.add_argument("--screen", default="home")
parser.add_argument("--fix", default="", help="A or B: see fit_fix.py")
args = parser.parse_args()

from micropick.gui.app import Options, create_app                  # noqa: E402
from micropick.gui.shell import MainWindow                         # noqa: E402
from micropick.gui.theme import factory                            # noqa: E402

if args.fix:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fit_fix
    fit_fix.apply(args.fix)

fits = collections.Counter()
if args.count:
    original = factory._ScrollColumn._fit_height

    def _fit_height(self):
        fits[self] += 1
        return original(self)
    factory._ScrollColumn._fit_height = _fit_height

app = create_app([])
window = MainWindow(Options(mock=True))
window.resize(1700, 1000)
window.show()
if not args.no_robot:
    window.session.probe_robot()
    window.session.new_run()
if args.screen == "manual":
    window.open_task_step("manual", "work")
elif args.screen != "home":
    task, step = args.screen.split(":")
    window.open_task_step(task, step)

marks = {}


def begin():
    fits.clear()
    marks["wall"] = time.perf_counter()
    marks["cpu"] = time.process_time()
    marks["thread"] = time.thread_time()
    QTimer.singleShot(int(args.seconds * 1000), end)


def end():
    wall = time.perf_counter() - marks["wall"]
    cpu = time.process_time() - marks["cpu"]
    thread = time.thread_time() - marks["thread"]
    print(f"screen {args.screen}, platform {app.platformName()}, "
          f"{wall:.1f} s: process CPU {100 * cpu / wall:.0f} % of one core, "
          f"GUI thread {100 * thread / wall:.0f} %")
    if args.count:
        def page_of(column):
            chain, widget = [], column.parentWidget()
            while widget is not None and len(chain) < 4:
                chain.append(type(widget).__name__)
                widget = widget.parentWidget()
            return " < ".join(chain)
        total = sum(fits.values())
        print(f"_fit_height: {total / wall:.0f} calls/s in {len(fits)} columns")
        for column, n in fits.most_common():
            print(f"  {n / wall:8.0f}/s  {page_of(column)}  "
                  f"visible={column.isVisible()}")
    window.close()
    app.quit()


QTimer.singleShot(3000, begin)           # cameras open, the first pose read
app.exec()
