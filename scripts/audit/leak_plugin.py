"""Test suite audit: what a test leaves alive for the next one.

    PYTHONPATH=scripts/audit AUDIT_LEAK_OUT=leaks.csv \
        python -m pytest -p leak_plugin -p no:cacheprovider tests/gui

After each test (its fixtures torn down, the conftest's deferred deletes
delivered) one CSV row: the Python threads alive and their names, the Qt
widgets and top-level widgets still known to the application, the QObjects
and running QTimers reachable from Python, the root logger's handlers, and
the test's wall time. A count that only grows from row to row is a leak;
the delta columns say which test added to it.

Serial runs only (one file per process; under xdist the workers would
write over each other). Nothing is collected by force unless
AUDIT_LEAK_GC=1, so the numbers are what the next test really meets.
"""

from __future__ import annotations

import csv
import gc
import logging
import os
import threading
import time

import pytest

_rows: list[dict] = []
_last: dict = {}


def _counts() -> dict:
    counts = {"threads": threading.active_count(),
              "thread_names": " ".join(sorted(
                  t.name for t in threading.enumerate()
                  if t is not threading.main_thread())),
              "log_handlers": len(logging.getLogger().handlers),
              "widgets": 0, "top_levels": 0, "qobjects": 0, "timers": 0,
              "qthreads": 0, "pool_threads": 0}
    try:
        from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return counts
    app = QApplication.instance()
    if app is None:
        return counts
    if os.environ.get("AUDIT_LEAK_GC"):
        gc.collect()
    counts["widgets"] = len(app.allWidgets())
    counts["top_levels"] = len(app.topLevelWidgets())
    counts["pool_threads"] = QThreadPool.globalInstance().activeThreadCount()
    for obj in gc.get_objects():
        try:
            if isinstance(obj, QObject):
                counts["qobjects"] += 1
                if isinstance(obj, QTimer) and obj.isActive():
                    counts["timers"] += 1
                elif isinstance(obj, QThread) and obj.isRunning():
                    counts["qthreads"] += 1
        except (RuntimeError, ReferenceError):   # the C++ object is gone
            pass
    return counts


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _last
    if not _last:
        _last = _counts()
    start = time.perf_counter()
    result = yield
    seconds = time.perf_counter() - start
    now = _counts()
    row = {"test": item.nodeid, "seconds": f"{seconds:.3f}"}
    for key, value in now.items():
        row[key] = value
        if isinstance(value, int):
            row[f"d_{key}"] = value - _last[key]
    _rows.append(row)
    _last = now
    return result


def pytest_sessionfinish(session):
    out = os.environ.get("AUDIT_LEAK_OUT")
    if not out or not _rows:
        return
    fields = ["test", "seconds"] + [k for k in _rows[0] if k not in ("test", "seconds", "thread_names")] + ["thread_names"]
    with open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(_rows)
