"""Category B: closing the window while a worker that holds no lease runs.

    conda run -n lab python scripts/audit/gui/close_with_worker.py

`MainWindow.closeEvent` waits for the robot's lease and nothing else. A
model still loading, a detection, a picture being saved or a camera being
opened hold none. This starts the program's own `app.run` path in a child
process with one such worker (a 3 s job standing in for a model load),
closes the window after 1 s and reports how the process ended.
"""
import subprocess
import sys
import textwrap

CHILD = textwrap.dedent('''
    import os, sys, time
    sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
    import _mock
    app = _mock.app()
    from PySide6.QtCore import QTimer
    from micropick.gui.app import Options
    from micropick.gui.shell import MainWindow
    from micropick.gui.workers import Worker
    window = MainWindow(Options(mock=True))
    window.show()
    worker = Worker(lambda: time.sleep(float(sys.argv[2])), what="loading the model")
    worker.start()
    QTimer.singleShot(1000, window.close)
    code = app.exec()
    print("event loop ended, worker still running:", worker.running, flush=True)
    sys.exit(code)
''')

for seconds in ("3", "0.1"):
    done = subprocess.run([sys.executable, "-c", CHILD, __file__, seconds],
                          capture_output=True, text=True, timeout=120)
    print(f"--- a worker of {seconds} s at close: exit code {done.returncode}")
    for line in (done.stdout + done.stderr).splitlines():
        if "zoxide" not in line and "qt.qpa" not in line and line.strip():
            print("   ", line[:200])
