"""A candidate fix for GUI-01, applied from outside `src/` (GUI audit).

As a pytest plugin, to run the tests with it:

    PYTHONPATH=scripts/audit/gui AUDIT_FIT_FIX=B conda run -n lab python -m pytest -q -p fit_fix tests/gui/test_nothing_shifts.py

and by `cpu_idle.py --fix A|B`. Two variants of `_ScrollColumn._fit_height`:

A  invalidate the column's layout only when a label's minimum height
   changed in this pass.
B  as now, then take back the LayoutRequest the pass itself posted to the
   inner widget (`QCoreApplication.removePostedEvents`).

Both end the loop: a pass that changes nothing asks for no other pass.
"""
import os

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QLabel

from micropick.gui.theme import factory


def _fit_labels(column) -> bool:
    changed = False
    for label in column._inner.findChildren(QLabel):
        if not label.wordWrap() or label.width() <= 0 or label.isHidden():
            continue
        wanted = factory._text_height(label, label.width())
        if wanted > 0 and wanted != label.minimumHeight():
            label.setMinimumHeight(wanted)
            changed = True
    return changed


def _fit_height_a(self) -> None:
    layout = self._inner.layout()
    if layout is None:
        return
    if _fit_labels(self):
        layout.invalidate()
    layout.activate()
    wanted = layout.heightForWidth(self._inner_width)
    if wanted < 0:
        wanted = layout.sizeHint().height()
    if wanted > 0 and wanted != self._inner.minimumHeight():
        self._inner.setMinimumHeight(wanted)


_original = factory._ScrollColumn._fit_height


def _fit_height_b(self) -> None:
    _original(self)
    QCoreApplication.removePostedEvents(self._inner,
                                        QEvent.Type.LayoutRequest.value)


def apply(variant: str) -> None:
    factory._ScrollColumn._fit_height = {"A": _fit_height_a,
                                         "B": _fit_height_b}[variant]


if os.environ.get("AUDIT_FIT_FIX"):
    apply(os.environ["AUDIT_FIT_FIX"])
