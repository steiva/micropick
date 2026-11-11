"""Test suite audit: the conftest's camera clean-up can meet a thread that
is listed but not started yet.

    conda run -n lab python scripts/audit/limbo_join.py

`threading.enumerate()` lists a thread from the moment `start()` is called
(it is "in limbo" until the new thread sets `_started`), and `join()` on
it then raises "cannot join thread before it is started". The GUI
conftest's teardown walks `threading.enumerate()` for "grab-" threads and
calls `camera.close()`, which joins: a camera a page is still opening on a
worker as the test ends is such a thread. This shows the window with plain
threads: one thread starts short-lived "grab-x" threads, the main thread
joins whatever it sees listed.
"""

import threading
import time

stop = threading.Event()


def starter():
    while not stop.is_set():
        threading.Thread(target=lambda: None, name="grab-x", daemon=True).start()


threading.Thread(target=starter, daemon=True).start()
seen = raised = 0
end = time.monotonic() + 5
while time.monotonic() < end:
    for thread in threading.enumerate():
        if thread.name == "grab-x":
            seen += 1
            try:
                thread.join(timeout=0.1)
            except RuntimeError as exc:
                raised += 1
                message = str(exc)
stop.set()
print(f"joined {seen} listed threads; {raised} raised"
      + (f": {message}" if raised else ""))
