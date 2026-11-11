"""Test suite audit: make the races lose on purpose.

    PYTHONPATH=scripts/audit AUDIT_SLOW=read|start|end AUDIT_SLOW_MS=30 \
        python -m pytest -p slow_mock -n 4 tests/gui

A test that only passes because the mock robot answers within the same
millisecond is a test that fails on a loaded machine. Each mode stretches
one such window, so the tests that depend on it fail every time instead of
once in twenty runs:

- read:  `MockRobot.get_position` takes AUDIT_SLOW_MS. A jog panel reads
         the pose when it is made, shown or the tip changes, and refuses a
         job meanwhile: a test that clicks without waiting for the panel
         fails.
- start: a `Worker`'s thread waits AUDIT_SLOW_MS before its job. A test
         that takes a job for done without waiting fails.
- end:   a `Worker`'s thread waits AUDIT_SLOW_MS between letting the
         robot's lease go and saying it finished. A test that waits for the
         lease and then asserts what the job shows fails.

The mock and the worker are wrapped when the plugin loads; nothing in
`src/` or `tests/` is edited. A failure here is a suspect, to be read:
a test may also assert a timing on purpose.
"""

import os
import time

MODE = os.environ.get("AUDIT_SLOW", "")
SECONDS = float(os.environ.get("AUDIT_SLOW_MS", "30")) / 1000.0


def pytest_configure(config):
    from micropick.gui.workers.base import Worker
    from micropick.hardware.mock import MockRobot
    if MODE == "read":
        read = MockRobot.get_position

        def get_position(self, *args, **kwargs):
            time.sleep(SECONDS)
            return read(self, *args, **kwargs)

        MockRobot.get_position = get_position
    elif MODE == "start":
        run = Worker.run

        def slow_run(self):
            time.sleep(SECONDS)
            run(self)

        # PySide finds a slot's thread through the method's name on its
        # object: under another name `run` is called on the GUI thread.
        slow_run.__name__ = "run"
        Worker.run = slow_run
    elif MODE == "end":
        release = Worker._release

        def slow_release(self):
            release(self)
            time.sleep(SECONDS)

        Worker._release = slow_release
    elif MODE:
        raise SystemExit(f"AUDIT_SLOW={MODE!r}: read, start or end")
