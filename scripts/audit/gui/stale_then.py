"""GUI-03: a job started before the last job's end is delivered takes that
job's `then` and drops its answer.

    conda run -n lab python scripts/audit/gui/stale_then.py

`JogPanel._busy()` is "the worker's thread runs", and that turns False
before `_job_done` is delivered on the GUI thread. A key or a click handled
in between starts a new job; `_job_done` of the first is then ignored
(`_claim`), and the first job's `then` is still in `_then` when the second
ends. Here the order is forced: job A (with a `then`) is waited for on its
thread without running the event loop, then an arrow step is asked for.
"""
import _mock

app = _mock.app()
session = _mock.session(speed_mm_s=0.0)
from micropick.gui.widgets.jog_panel import JogPanel               # noqa: E402

panel = JogPanel(session)
_mock.until(lambda: not panel.job_pending)

seen = []
assert panel.run_job(lambda log: "answer of job A", moves=False,
                     then=lambda result: seen.append(result) or result)
panel._worker.wait()                 # A's thread has ended; nothing delivered
print("after A's thread ended: busy =", panel.busy,
      " job_pending =", panel.job_pending)
panel.step("x", +1)                  # what an arrow key does
print("an arrow step was accepted:", panel._worker is not None
      and panel._worker.what == "moving the robot")
_mock.until(lambda: not panel.job_pending)
print("A's then was called with:", [type(r).__name__ if not isinstance(r, str)
                                    else r for r in seen])
print("expected: ['answer of job A']")
session.shutdown()
