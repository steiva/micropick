"""The packaged application's entry point (PyInstaller runs this file).

`micropick.gui.__main__` uses relative imports, so it cannot be the script
itself; this imports it as part of the package. Two things a windowed build
needs first: a windowed exe has no console, so `sys.stdout` and
`sys.stderr` are None and a library that writes to them directly fails -
they go to the null device, the log file being where output is read; and
`freeze_support`, without which anything that starts a process re-runs the
whole application in it.
"""

import multiprocessing
import os
import sys


def _quiet_streams() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _quiet_streams()
    # A lab computer is not where ultralytics installs packages on import.
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from micropick.gui.__main__ import main
    sys.exit(main())
