"""GUI-04: the first Stop does not end a short job between two of its moves.

    conda run -n lab python scripts/audit/gui/stop_in_job.py

The Picking page's Shake the dish, outside a run: the tip to the shake
position, the stir's strokes, the tip back over the dish (`PickingPage.
_shake`), one `JogPanel.run_job`. Stop is pressed (`MainWindow._stop`, what
Esc and STOP call) during the first move, on a mock that moves at 100 mm/s.
Counted: the moves the mock robot made after the press.
"""
import numpy as np

import _mock

app = _mock.app()
window = _mock.window(speed_mm_s=100.0)
session, robot = window.session, window.session.robot
from micropick.gui.widgets.toast import toast_of                   # noqa: E402

session.remember("shake", np.array([150.0, 150.0, 60.0]))
session.remember("observe", np.array([200.0, 200.0, 100.0]))
window.open_task_step("auto", "picking")
page = window.picking_page
page.setEnabled(True)                # the step's needs are not this check's
_mock.until(lambda: _mock.robot_free(window))
before = robot.moves
page._shake()
assert session.robot_lease.busy, "the shake did not start"
_mock.until(lambda: robot.moves > before, 20)        # the first move is made
at_stop = robot.moves
window._stop()
print("toast after Stop:", repr(toast_of(window).text()))
_mock.until(lambda: _mock.robot_free(window), 120)
print(f"moves made before Stop: {at_stop - before}; after Stop: "
      f"{robot.moves - at_stop}")
print("last toast:", repr(toast_of(window).text()))
window.close()
