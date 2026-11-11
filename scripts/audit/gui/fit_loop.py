"""Why a scroll column measures itself without end (GUI-01).

    conda run -n lab python scripts/audit/gui/fit_loop.py [--screen manual|auto:picking]

For each `_ScrollColumn` that still runs `_fit_height` two seconds after the
window is up: what each pass changed (a label's minimum height, the
column's), and which event on the inner widget asked for the next pass.
"""
import argparse
import collections
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MICROPICK_ROOT", tempfile.mkdtemp(prefix="micropick-audit-"))

from PySide6.QtCore import QEvent, QTimer                          # noqa: E402
from PySide6.QtWidgets import QLabel                               # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--screen", default="home")
args = parser.parse_args()

from micropick.gui.app import Options, create_app                  # noqa: E402
from micropick.gui.shell import MainWindow                         # noqa: E402
from micropick.gui.theme import factory                            # noqa: E402

Column = factory._ScrollColumn
recording = [False]
passes = collections.defaultdict(list)      # column -> [what a pass changed]
asked = collections.defaultdict(collections.Counter)
original_fit, original_filter = Column._fit_height, Column.eventFilter


def _fit_height(self):
    if not recording[0]:
        return original_fit(self)
    labels = {label: label.minimumHeight()
              for label in self._inner.findChildren(QLabel)}
    before = self._inner.minimumHeight()
    original_fit(self)
    changed = [f"label {label.text()[:40]!r} width {label.width()} "
               f"min {old} -> {label.minimumHeight()}"
               for label, old in labels.items()
               if label.minimumHeight() != old]
    if self._inner.minimumHeight() != before:
        changed.append(f"column min {before} -> {self._inner.minimumHeight()}")
    passes[self].append(changed)


def eventFilter(self, obj, event):
    if recording[0] and obj is self._inner and event.type() in (
            QEvent.Type.LayoutRequest, QEvent.Type.Resize, QEvent.Type.Show):
        asked[self][event.type().name] += 1
    return original_filter(self, obj, event)


Column._fit_height = _fit_height
Column.eventFilter = eventFilter

app = create_app([])
window = MainWindow(Options(mock=True))
window.resize(1700, 1000)
window.show()
window.session.probe_robot()
window.session.new_run()
if args.screen == "manual":
    window.open_task_step("manual", "work")
elif args.screen != "home":
    window.open_task_step(*args.screen.split(":"))


def where(column):
    chain, widget = [], column.parentWidget()
    while widget is not None and len(chain) < 3:
        chain.append(type(widget).__name__)
        widget = widget.parentWidget()
    return " < ".join(chain)


def report():
    recording[0] = False
    for column, seen in passes.items():
        print(f"\n{where(column)}  visible={column.isVisible()}  "
              f"inner width {column._inner.width()} (set {column._inner_width}), "
              f"{len(seen)} passes in 1 s, asked by {dict(asked[column])}")
        quiet = sum(1 for changed in seen if not changed)
        print(f"  passes that changed nothing: {quiet}")
        for changed in seen[:6]:
            print("  pass:", "; ".join(changed) or "nothing changed")
    window.close()
    app.quit()


QTimer.singleShot(2500, lambda: recording.__setitem__(0, True))
QTimer.singleShot(3500, report)
app.exec()
