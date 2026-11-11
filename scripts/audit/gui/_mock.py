"""Shared by the GUI audit's repro scripts: a mock session or window in a
scratch root (no profile of the user's is read or written, no robot, no
camera)."""
import os
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MICROPICK_ROOT", tempfile.mkdtemp(prefix="micropick-audit-"))

from PySide6.QtWidgets import QApplication                        # noqa: E402


def app():
    return QApplication.instance() or QApplication([])


def until(condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while True:
        QApplication.processEvents()
        if condition():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.005)


def spin(seconds):
    until(lambda: False, seconds)


def session(speed_mm_s=None):
    from micropick.gui.app import Options
    from micropick.gui.session import Session
    from micropick.hardware.mock import MockRobot
    if speed_mm_s is not None:
        MockRobot.SPEED_MM_S = speed_mm_s
    made = Session(Options(mock=True))
    made.load_profile("mock")
    made.probe_robot()
    made.new_run()
    return made


def window(speed_mm_s=None, labware=True, tip=True):
    from micropick.config.labware import resolve_definition
    from micropick.gui.app import Options
    from micropick.gui.shell import MainWindow
    from micropick.hardware.mock import MockRobot
    if speed_mm_s is not None:
        MockRobot.SPEED_MM_S = speed_mm_s
    made = MainWindow(Options(mock=True))
    made.resize(1700, 1000)
    made.show()
    made.session.probe_robot()
    made.session.new_run()
    if labware:
        made.session.load_labware(
            resolve_definition("corning_96_wellplate_360ul_flat"), 5)
        made.session.load_labware(
            resolve_definition("opentrons_96_tiprack_300ul"), 1)
        if tip:
            made.session.pick_up_tip(1, "A1")
            calibrate(made.session)
    until(lambda: robot_free(made), 10)
    return made


def robot_free(made):
    from micropick.gui.widgets.jog_panel import JogPanel
    return (not made.session.robot_lease.busy
            and not any(p.job_pending for p in made.findChildren(JogPanel)))


def calibrate(session, width=2592, height=1944, mm_per_px=0.01):
    """A linear toy pixel map over the whole frame and a tip calibration
    measured on the tip that is on, in this robot session: what the tests
    use (`tests/gui/test_manual_page.py::_calibrate`,
    `test_shell.py::_tip_calibrated`)."""
    from micropick.config.schema import PipetteOffset
    from micropick.config.schema import PixelMap as PixelMapConfig
    s = width / 2
    profile = session.profile
    profile.calibration.pixel_map = PixelMapConfig(
        degree=1, cu=width / 2, cv=height / 2, s=s,
        coef=[[0.0, mm_per_px * s], [mm_per_px * s, 0.0]], zero=[0.0, 0.0],
        ref=[width / 2, height / 2],
        bounds=[100.0, 100.0, width - 100.0, height - 100.0],
        image_size=[width, height], sweep_z=0.0)
    profile.calibration.pipette_offset = PipetteOffset(
        dx=10.0, dy=-5.0, robot_session=session.run_state.run_id,
        tip_pickup=session.tip.pickup)
    session.profile_changed.emit(session.profile)
