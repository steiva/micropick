"""Test suite audit: what the robot's jobs were doing when a test failed.

    PYTHONPATH=scripts/audit python -m pytest -p hang_dump ...

Most flaky failures here are a wait that ran out (`assert _until(...)`,
"assert False"), which says nothing of what was waited for. When a test's
call fails, this adds a section to its report, taken before teardown:

- every JogPanel alive: its worker's job (`what`), whether its thread still
  runs, whether its end was delivered (`job_pending`), its pose known;
- the robot lease of every Session alive: who holds it;
- every Worker still in `workers.base._RUNNING`;
- the Python stack of every thread but the main one;
- the last 60 events of the jobs' lives, in order: a panel starting a job
  (`_run`), a worker's thread entering and leaving `run`, a lease claimed
  and let go, a job's end delivered to its panel and whether the panel took
  it as its own (`_claim`). Recorded by wrapping those methods when the
  plugin loads; nothing in `src/` is edited.
"""

from __future__ import annotations

import gc
import sys
import threading
import traceback

import time
from collections import deque

import pytest

_events: deque = deque(maxlen=60)
_t0 = time.monotonic()


def _note(text: str) -> None:
    _events.append(f"{time.monotonic() - _t0:9.4f} "
                   f"{threading.current_thread().name[:10]:10} {text}")


def _wrap(cls, name, before=None, after=None):
    original = getattr(cls, name)

    def wrapped(self, *args, **kwargs):
        if before:
            _note(before(self, *args, **kwargs))
        result = original(self, *args, **kwargs)
        if after:
            _note(after(self, result, *args, **kwargs))
        return result

    wrapped.__name__ = name
    setattr(cls, name, wrapped)


def pytest_configure(config):
    try:
        from micropick.gui.widgets.jog_panel import JogPanel
        from micropick.gui.workers.base import Worker
        from micropick.hardware.robot_lease import RobotLease
    except ImportError:
        return
    wid = lambda w: f"{getattr(w, 'what', None)!r}@{id(w) % 10000}"
    _wrap(Worker, "run", before=lambda w: f"run enter {wid(w)}",
          after=lambda w, r: f"run leave {wid(w)}")
    _wrap(Worker, "start", before=lambda w: f"start {wid(w)}")
    _wrap(JogPanel, "_run",
          after=lambda p, r, worker, **k: f"panel@{id(p) % 10000} _run "
          f"{wid(worker)} started={r} robot_work={k.get('robot_work', True)}")
    _wrap(JogPanel, "_claim",
          before=lambda p: f"panel@{id(p) % 10000} end delivered from "
          f"{wid(p.sender())}; panel's worker is {wid(p._worker)}")
    _wrap(RobotLease, "claim",
          after=lambda l, r, what, **k: f"lease claim {what!r} -> "
          f"{'held' if r is not None else 'REFUSED'}")
    _wrap(RobotLease, "_release", before=lambda l, lease: f"lease release {lease.what!r}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    _events.clear()
    return (yield)


def _state() -> str:
    lines = []
    try:
        from micropick.gui.session import Session
        from micropick.gui.widgets.jog_panel import JogPanel
        from micropick.gui.workers import base
    except ImportError as exc:
        return f"(no gui: {exc})"
    for obj in gc.get_objects():
        try:
            if isinstance(obj, JogPanel):
                worker = obj._worker
                lines.append(
                    f"JogPanel {hex(id(obj))} visible={obj.isVisible()} "
                    f"busy={obj.busy} job_pending={obj.job_pending} "
                    f"pose={'known' if obj.pose is not None else None} "
                    f"worker={getattr(worker, 'what', None)!r} "
                    f"running={getattr(worker, 'running', None)} "
                    f"lease_released={getattr(getattr(worker, 'lease', None), 'released', None)}")
            elif isinstance(obj, Session):
                lease = obj.robot_lease
                lines.append(f"Session {hex(id(obj))} lease holder="
                             f"{lease.holder!r} locked={lease.locked}")
        except RuntimeError:
            pass
    for worker in list(base._RUNNING):
        lines.append(f"Worker in _RUNNING: what={worker.what!r} "
                     f"running={worker.running} background={worker.background}")
    frames = sys._current_frames()
    for thread in threading.enumerate():
        if thread is threading.main_thread():
            continue
        frame = frames.get(thread.ident)
        lines.append(f"-- thread {thread.name}")
        if frame is not None:
            lines.extend(l.rstrip() for l in traceback.format_stack(frame)[-6:])
    # Qt threads are not Python threads: their frames have no Thread object.
    known = {t.ident for t in threading.enumerate()}
    for ident, frame in frames.items():
        if ident not in known:
            lines.append(f"-- thread {ident} (not a threading.Thread: a QThread)")
            lines.extend(l.rstrip() for l in traceback.format_stack(frame)[-8:])
    lines.append("-- the jobs' last events (seconds, thread, event)")
    lines.extend(_events)
    return "\n".join(lines)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call" and report.failed:
        report.sections.append(("robot jobs at the failure", _state()))
    return report
