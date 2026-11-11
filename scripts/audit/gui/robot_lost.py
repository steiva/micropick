"""Category F: the robot switched off, and switched on again, under the GUI.

    conda run -n lab python scripts/audit/gui/robot_lost.py

The mock robot's power (`MockRobot.power_off`, `power_on`), with the
connection monitor running as in the program. Printed: what a jog step
answers while it is off, what Home and the session say after the monitor's
next look, and the state once it is on again (a restarted robot has no
session: labware, tip and confirmations are forgotten).
"""
import _mock

app = _mock.app()
window = _mock.window(speed_mm_s=0.0)
session, robot = window.session, window.session.robot
from micropick.gui import robot_monitor                            # noqa: E402
from micropick.gui.widgets.toast import toast_of                   # noqa: E402

robot_monitor.LOOK_EVERY_MS = 300
window.monitor._timer.setInterval(300)
window.monitor.start()
window.open_task_step("manual", "work")
_mock.until(lambda: _mock.robot_free(window))
panel = window.manual_page.jog
print("connected:", session.robot_state, "| tip:", session.tip.describe())

robot.power_off()
panel.step("x", +1)
_mock.until(lambda: not panel.job_pending)
print("a jog step while off ->", repr(toast_of(window).text()))
_mock.spin(1.5)
print("after a look: state", repr(session.robot_state), "| robot kept:",
      session.robot is not None, "| pad enabled:", panel._enabled,
      "| step", window.step, "| banner:", getattr(window._need, "text", None))
panel.step("x", +1)
_mock.until(lambda: not panel.job_pending)
print("a second jog step ->", repr(toast_of(window).text()))

robot.power_on()
_mock.spin(1.5)
print("on again: state", repr(session.robot_state), "| robot:",
      session.robot is not None, "| needs home:", session.needs_home,
      "| tip:", session.tip.describe(), "| banner:",
      getattr(window._need, "text", None))
window.close()
