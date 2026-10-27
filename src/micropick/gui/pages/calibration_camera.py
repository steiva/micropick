"""Robot setup's camera calibration sheet (REDESIGN §5, #10): the pixel map
of the upper camera, as the confirmed steps.

A sheet over whatever is shown, opened from Robot setup's row or a step
that needs the calibration: the live picture of the upper camera on the
left, and on the right the steps, each a card with a mark
(`widgets.step_card`), opened one after another by the operator's answer:

1. **Place the marker** on a platform -> "It is in place".
2. **Which slot is it in?** The mini deck of all twelve slots
   (`widgets.slot_chooser`), nothing chosen: a click moves the camera over
   that slot's center at once, through the jog panel's `run_job`, and the
   slot is marked once it went; grayed while the robot is busy. Before a
   tip calibration the camera's distance from the tip is the start offset
   in Settings (`AppSettings.tip_cal_start_offset`): it only has to bring
   the marker into view.
3. **Center the camera on the marker**, with the arrows, or with a click
   on the picture once a pixel map exists - before the first one a pixel
   has no direction on the deck, and a click moves nothing. The picture
   says MARKER FOUND or NO MARKER IN VIEW; "It is centered" waits for the
   marker within CENTERED_SIDES of its own side of the center, since
   `measure_scale` probes from wherever the gantry stands.
4. **Calibrate** -> "Start calibration": the sweep, a **run** - it holds
   the robot's lease from start to end, so STOP and the pill show - with a
   progress bar and "Don't touch the robot.". Stop (Esc, STOP) ends it at
   the next pose; the gantry has then moved, so the steps go back to 3.
5. **The result.** Good and Acceptable are saved at once: "Camera
   calibrated and saved." with Done, which closes the sheet. Redo is not:
   "Not accurate enough: the marker was off center." with Redo
   calibration (secondary, not the accented one) and a quiet Save anyway.

The sweep's parameters - marker side, dictionary, grid, degree - are the
bench's and set in Settings (`AppSettings.sweep_*`); the sheet does not
repeat them. A marker printed in another dictionary is not in view.

The sweep is the upper camera's - the one Robot setup names - since the
lower camera looks up at the tip and a map of it is used by nothing.

The sheet watches for the marker while it is shown and no sweep runs: the
frame on the view is passed to `gui.marker_watch` a few times a second,
and what it finds is drawn over the picture. It reads the frame the view
is already showing rather than the camera, so what is drawn belongs to the
picture it is drawn on. During the sweep, `calibrate_camera`'s `on_frame`
draws the corners being fitted instead (a signal, so the drawing happens
on the GUI thread; the frame is dropped).

The result is a verdict judged from the FitReport on four things, each
against two limits (`VERDICT_LIMITS`): the held-out error, the largest
residual, how far the recovered marker side is from the printed one, and
how much of the frame the sweep covered. The worst of the four is the
verdict. The limits are set from the first real sweep (DESIGN section 3:
24 µm held out, the side 0.4 % off, the edges of the frame reached). The
report and the verdict's line go to the log.

Nothing but the result writes the profile, and only after the fit:
`run_sweep` raises `Cancelled` before the fit is attempted, so a stopped
sweep has nothing to write.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QVBoxLayout,
                               QWidget)

from ...config.app_settings import AppSettings
from ...viz import markers
from ...workflows.calibrate_camera import Cancelled, calibrate_camera
from ..auto_camera import CameraOpener
from ..camera_moves import move_over_slot, move_to_pixel
from ..marker_watch import DICTIONARIES, MarkerWatch
from ..session import Session
from ..tasks import SETUP_TITLES
from ..theme import SPACING
from ..theme.factory import (muted_label, primary_button, quiet_button,
                             scroll_column, secondary_button)
from ..widgets.banner import Banner
from ..widgets.camera_view import ZOOM_AND_PAN, CameraView
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.overlays import SheetWindow
from ..widgets.slot_chooser import SlotChooser
from ..widgets.step_card import StepCard, wrapped
from ..workers import Worker, describe_error

__all__ = ["CameraCalibrationSheet", "DICTIONARIES", "STEPS",
           "SWEEP_DEFAULTS", "judge", "sweep_parameters"]

log = logging.getLogger(__name__)

TITLE = SETUP_TITLES["camera_calibration"]

PANEL_WIDTH = 420

# The steps, in order; `CameraCalibrationSheet.step` is one of them.
STEPS = ("place", "slot", "center", "calibrate", "result")

# What the picture says while the marker is watched for.
MARKER_FOUND = "MARKER FOUND"
NO_MARKER = "NO MARKER IN VIEW"

SAVED = "Camera calibrated and saved."
NOT_ACCURATE = "Not accurate enough: the marker was off center."
DONT_TOUCH = "Don't touch the robot."
USE_THE_ARROWS = "Use the arrows to center the marker."
CENTER_BY_CLICK = ("Click the marker on the picture or use the arrows "
                   "until it is in the middle.")
CENTER_BY_ARROWS = "Use the arrows until the marker is in the middle."

# Step 3 is done when the marker's center is within this many of its own
# sides of the frame's center: under the crosshair as the eye judges it.
CENTERED_SIDES = 2.0

# The sweep's usual settings: the marker printed for this rig and the grid
# and degree the first real sweep was judged at (DESIGN section 3). They are
# set in Settings (`AppSettings.sweep_*`); these are its defaults.
_DEFAULTS = AppSettings()
SWEEP_DEFAULTS = {"marker_side_mm": _DEFAULTS.sweep_marker_side_mm,
                  "dictionary": _DEFAULTS.sweep_dictionary,
                  "grid_n": _DEFAULTS.sweep_grid_n,
                  "degree": _DEFAULTS.sweep_degree}


def sweep_parameters(settings) -> dict:
    """The sweep's parameters as Settings has them, keyed as SWEEP_DEFAULTS.
    A dictionary name this build does not know is the default's."""
    dictionary = settings.sweep_dictionary
    if dictionary not in DICTIONARIES:
        dictionary = SWEEP_DEFAULTS["dictionary"]
    return {"marker_side_mm": settings.sweep_marker_side_mm,
            "dictionary": dictionary, "grid_n": settings.sweep_grid_n,
            "degree": settings.sweep_degree}

# How a fit is judged: (what, unit, good, acceptable, higher is better).
# Past `acceptable` is Redo. From the first real sweep, which held out at
# 24 µm with the side 0.4 % off: twice that is still usable, more is a
# sweep to repeat.
VERDICT_LIMITS = {
    "held-out error": ("µm", 40.0, 80.0, False),
    "largest residual": ("µm", 100.0, 200.0, False),
    "marker side": ("% off", 1.0, 2.0, False),
    "frame covered": ("%", 80.0, 60.0, True),
}
GOOD, ACCEPTABLE, REDO = "Good", "Acceptable", "Redo"
_RANK = {GOOD: 0, ACCEPTABLE: 1, REDO: 2}


@dataclass(frozen=True)
class Verdict:
    level: str                       # GOOD, ACCEPTABLE or REDO
    line: str                        # the one line for the top of Result
    checks: tuple                    # (what, value, unit, level) for each


def _grade(what: str, value: float) -> str:
    _unit, good, acceptable, higher = VERDICT_LIMITS[what]
    if higher:
        return GOOD if value >= good else ACCEPTABLE if value >= acceptable \
            else REDO
    return GOOD if value <= good else ACCEPTABLE if value <= acceptable \
        else REDO


def judge(report, printed_side_mm: float | None) -> Verdict:
    """Good, Acceptable or Redo for a fit, and the line that says why.

    The marker side is judged only when both the printed and the recovered
    sides are known; the other three always are."""
    values = {"held-out error": float(report.holdout_mean_um),
              "largest residual": float(report.resid_max_um),
              "frame covered": 100.0 * float(min(report.coverage_frac))}
    if report.track_side_mm and printed_side_mm:
        values["marker side"] = (100.0 * abs(report.track_side_mm
                                             - printed_side_mm)
                                 / printed_side_mm)
    checks = tuple((what, value, VERDICT_LIMITS[what][0], _grade(what, value))
                   for what, value in values.items())
    level = max((c[3] for c in checks), key=_RANK.__getitem__)

    def said(what, value, unit):
        return f"{what} {value:.1f} {unit}" if unit != "%" else \
            f"{value:.0f} % of the frame covered"

    if level == GOOD:
        line = f"{GOOD}: " + ", ".join(said(*c[:3]) for c in checks) + "."
    else:
        worst = [c for c in checks if c[3] == level]
        parts = []
        for what, value, unit, _ in worst:
            _u, good, acceptable, higher = VERDICT_LIMITS[what]
            limit = good if level == ACCEPTABLE else acceptable
            word = ("under" if higher else "over") if level == REDO else \
                ("below" if higher else "above")
            parts.append(f"{said(what, value, unit)} ({word} "
                         f"{limit:g} {'%' if unit == '%' else unit})")
        line = f"{level}: " + "; ".join(parts) + "."
        if level == REDO:
            line += " Center the marker and sweep again."
    return Verdict(level, line, checks)


# How often the sheet looks for the marker. Three times a second is faster than
# a hand moves a marker and a twentieth of what the detection costs, so the
# grab loop and the view keep the rest.
WATCH_MS = 330


class CameraCalibrationSheet(SheetWindow):
    """`open_sheet()`; × closes it, a sweep going on carries on (the pill
    leads back). `step` is the step open now, one of STEPS."""

    # Emitted from the sweep's thread with the corners it just tracked, or
    # None for a pose where the marker was not seen. A signal, so the
    # drawing happens on the thread that owns the widget.
    pose_tracked = Signal(object)

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, TITLE, buttons=False)
        self.session = session
        self.step = STEPS[0]
        self._worker: Worker | None = None
        self._sweep_used = dict(SWEEP_DEFAULTS)  # what the last sweep ran with
        self._watch = MarkerWatch()
        self._watch_worker: Worker | None = None
        self._sighting = None
        self._verdict: Verdict | None = None
        self._result = None                  # (pmap, report, sweep)
        self._saved = False                  # the result, in the profile
        self._save_error = ""
        # How the last sweep ended, when not with a result.
        self._outcome = ""
        self.marker_state = ""               # what the picture says of it
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self.body_widget)
        # No Home and no Retract here: homing halfway through centering the
        # marker throws away the pose the sweep is about to be planned around.
        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False, collapsed=("positions",),
                            parent=self.body_widget)
        self.jog.show_position_on(self.view)
        self.view.set_mouse_help(ZOOM_AND_PAN)
        self.view.set_click_to_move("Click   center the camera there",
                                    self._mapped)
        self.view.clicked.connect(self._clicked)

        column = QWidget(self.body_widget)
        cards = QVBoxLayout(column)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(SPACING)
        self.cards = {}
        for name, build in (("place", self._place_card),
                            ("slot", self._slot_card),
                            ("center", self._center_card),
                            ("calibrate", self._calibrate_card),
                            ("result", self._result_card)):
            card = build(len(self.cards) + 1)
            self.cards[name] = card
            cards.addWidget(card.box)
            if name == "center":
                cards.addWidget(self.jog)
        cards.addStretch(1)

        self.body.addWidget(
            FeedRow(self.view, scroll_column(column, PANEL_WIDTH)), 1)

        self.pose_tracked.connect(self._draw_tracked)
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(WATCH_MS)
        self._watch_timer.timeout.connect(self._watch_tick)

        session.camera_opened.connect(self._refresh_camera)
        session.camera_closed.connect(self._refresh_camera)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.robot_busy_changed.connect(lambda _h: self._refresh())
        session.settings_changed.connect(lambda _s: self._refresh())
        # The window's Stop: the sweep stops at its next pose.
        session.stop_requested.connect(self._stop)
        self._refresh()

    # -- construction --------------------------------------------------------

    def _place_card(self, number: int) -> StepCard:
        step = StepCard(self.body_widget, number, "Place the marker")
        step.add(wrapped("Put the calibration marker on a platform."))
        self.placed_button = primary_button("It is in place", self)
        self.placed_button.clicked.connect(lambda: self._go("slot"))
        step.add(self.placed_button)
        return step

    def _slot_card(self, number: int) -> StepCard:
        step = StepCard(self.body_widget, number, "Which slot is it in?")
        step.add(wrapped("Click the slot. The camera moves there."))
        self.slots = SlotChooser(self)
        self.slots.slot_clicked.connect(self._move)
        step.add(self.slots)
        return step

    def _center_card(self, number: int) -> StepCard:
        step = StepCard(self.body_widget, number,
                        "Center the camera on the marker")
        # Says a click only once there is a pixel map for it to go through.
        self.center_how = wrapped()
        step.add(self.center_how)
        self.center_state = muted_label()
        step.add(self.center_state)
        self.centered_button = primary_button("It is centered", self)
        self.centered_button.clicked.connect(lambda: self._go("calibrate"))
        step.add(self.centered_button)
        return step

    def _calibrate_card(self, number: int) -> StepCard:
        step = StepCard(self.body_widget, number, "Calibrate")
        step.add(wrapped("The robot moves the camera over the marker by "
                         "itself. About 2 min."))
        # What stops Start, or how the last sweep ended.
        self.camera_note = muted_label()
        self.camera_note.setWordWrap(True)
        step.add(self.camera_note)
        self.start_button = primary_button("Start calibration", self)
        self.start_button.clicked.connect(self._start)
        step.add(self.start_button)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        step.add(self.progress)
        self.notice = QLabel(DONT_TOUCH, self)
        self.notice.setObjectName("warnText")
        step.add(self.notice)
        return step

    def _result_card(self, number: int) -> StepCard:
        step = StepCard(self.body_widget, number, "Result")
        self.saved_banner = Banner(SAVED, tone="ok", parent=self)
        step.add(self.saved_banner)
        self.poor_banner = Banner(NOT_ACCURATE, tone="warn", parent=self)
        step.add(self.poor_banner)
        row = QHBoxLayout()
        # Closes the sheet as × does; only after a result that was saved.
        self.result_done_button = primary_button("Done", self)
        self.result_done_button.clicked.connect(self._cancel)
        self.redo_button = secondary_button("Redo calibration", self)
        self.redo_button.clicked.connect(self._redo)
        self.save_anyway_button = quiet_button("Save anyway", self)
        self.save_anyway_button.clicked.connect(self._save)
        row.addWidget(self.result_done_button)
        row.addWidget(self.redo_button)
        row.addWidget(self.save_anyway_button)
        row.addStretch(1)
        step.add_layout(row)
        return step

    # -- opening and closing -------------------------------------------------

    def open_sheet(self) -> None:
        """From step 1, unless a sweep is going on: then as it is."""
        if not self._running():
            self._outcome = ""
            self._go(STEPS[0])
        self.open()
        self.opener.ensure(self.session.upper_camera_label)
        self._refresh_camera()
        self._watch_timer.start()

    def discard(self) -> None:
        """Closed by going elsewhere; a sweep carries on."""
        if not self.isHidden():
            self.hide()

    def hideEvent(self, event) -> None:
        """A sheet nobody is looking at does not detect. (The cover hides
        itself while it is built, before the timer exists.)"""
        timer = getattr(self, "_watch_timer", None)
        if timer is not None:
            timer.stop()
        super().hideEvent(event)

    def run_activity(self) -> str | None:
        """For the top bar's pill: the sweep neither pauses nor waits."""
        return None

    # -- the steps -----------------------------------------------------------

    def _go(self, step: str) -> None:
        """Open `step`; going back to an earlier one forgets the result."""
        self.step = step
        if STEPS.index(step) < STEPS.index("result"):
            self._result = self._verdict = None
            self._saved, self._save_error = False, ""
        if STEPS.index(step) <= STEPS.index("slot"):
            self.slots.set_marked(None)
        self._refresh()

    def _move(self, slot: int) -> None:
        """Step 2: the camera over the center of the slot clicked."""

        def moved(said):
            self.slots.set_marked(slot)
            self._go("center")
            return said

        move_over_slot(self.session, self.jog, slot, then=moved)

    def _mapped(self) -> bool:
        """Whether a click on the picture can move the robot: only through
        the profile's pixel map, so not before the first camera
        calibration."""
        profile = self.session.profile
        return profile is not None and profile.pixel_map is not None

    def _clicked(self, u: float, v: float) -> None:
        """Step 3 by a click: the camera to the point clicked, through the
        pixel map there is. Without one a pixel has no direction on the
        deck, and the arrows do it."""
        if self._running() or STEPS.index(self.step) < STEPS.index("center"):
            return
        move_to_pixel(self.session, self.jog, u, v, no_map=USE_THE_ARROWS)

    def _redo(self) -> None:
        self._go("center")

    # -- watching for the marker ---------------------------------------------

    def _watch_tick(self) -> None:
        """One look, if there is something to look at and nothing in flight."""
        if self.isHidden() or self._running():
            return
        if self._watch_worker is not None and self._watch_worker.running:
            return
        frame = self.view.held_frame
        if frame is None:
            return
        worker = Worker(self._watch.look, frame, self._parameters()["dictionary"],
                        what="looking for the marker")
        self._watch_worker = worker
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and these touch the view.
        worker.finished.connect(self._watch_done)
        worker.failed.connect(self._watch_failed)
        worker.start()

    def _watch_done(self, sighting) -> None:
        if self.sender() is not self._watch_worker:
            return
        self._watch_worker = None
        if self._running():
            return
        self._sighting = sighting
        self.view.set_overlay_items(sighting.items())
        self._refresh()

    def _watch_failed(self, reason: str) -> None:
        if self.sender() is not self._watch_worker:
            return
        self._watch_worker = None
        self._sighting = None
        self.view.set_overlay_items([])
        log.error("looking for the marker: %s", reason)
        self._refresh()

    def _parameters(self) -> dict:
        return sweep_parameters(self.session.settings)

    def _seen(self) -> bool:
        """The marker in the picture, in the sweep's dictionary."""
        return self._sighting is not None and self._sighting.as_asked

    def _centered(self) -> bool:
        """Within CENTERED_SIDES of its own side of the center."""
        sighting = self._sighting
        if not self._seen() or sighting.offset_px is None \
                or not sighting.side_px:
            return False
        return (float(np.linalg.norm(sighting.offset_px))
                <= CENTERED_SIDES * sighting.side_px)

    # -- the sweep -----------------------------------------------------------

    def _camera(self):
        label = self.session.upper_camera_label
        return self.session.camera(label) if label else None

    def _running(self) -> bool:
        return self._worker is not None and self._worker.running

    def _problem(self) -> str:
        """What stops the sweep from starting, as a sentence, or ""."""
        if self.session.robot is None:
            return "Robot not connected."
        if self.session.upper_camera_label is None:
            return "Choose the upper camera first."
        if self._camera() is None:
            return "Upper camera not open."
        if not self._seen():
            return "Marker not in view."
        if not self._centered():
            return "Marker not centered."
        return ""

    def _start(self) -> None:
        camera = self._camera()
        if self._problem() or self._running():
            return
        # Taken once: a change in Settings while it runs is for the next.
        params = self._sweep_used = self._parameters()
        detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(
                DICTIONARIES[params["dictionary"]]),
            cv2.aruco.DetectorParameters())
        # The robot for the whole sweep (`Session.robot_lease`): a run.
        lease = self.session.robot_lease.claim("camera calibration sweep",
                                               run=True)
        if lease is None:
            self._outcome = self.session.robot_lease.refusal
            self._refresh()
            return
        self._outcome = ""
        self.progress.setRange(0, 0)         # indeterminate until the first pose
        self.view.set_overlay_items([])
        log.info("camera calibration: grid %d, degree %d, marker %g mm, %s",
                 params["grid_n"], params["degree"],
                 params["marker_side_mm"], params["dictionary"])
        # calibrate_camera declares on_progress, cancel and log, so the
        # worker passes all three. on_frame is named outright.
        worker = Worker(calibrate_camera, self.session.robot, camera, detector,
                        marker_side_mm=params["marker_side_mm"],
                        grid_n=params["grid_n"], degree=params["degree"],
                        on_frame=self._on_pose,
                        what="calibrating the camera", lease=lease)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.message.connect(self._on_message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.start()
        self._refresh()

    def _on_pose(self, frame, corners, index, total) -> None:
        """The sweep's own hook, on the sweep's own thread. Emits and
        returns; the frame is not carried across."""
        self.pose_tracked.emit(None if corners is None else np.asarray(corners))

    def _draw_tracked(self, corners) -> None:
        if corners is None:
            self.view.set_overlay_items([])
            return
        marker_id = getattr(self._sighting, "marker_id", None)
        self.view.set_overlay_items(
            markers.items(corners, marker_id,
                          label=f"tracked  ·  {markers.orientation(corners)}"))

    def _stop(self) -> None:
        if not self._running():
            return
        # request_cancel is called, never connected: the worker's own event
        # loop is blocked inside the sweep. The workflow sees the event
        # between poses, so the gantry finishes the move it is making first.
        self._worker.request_cancel()
        log.info("camera calibration: stopping at the next pose")

    def _on_progress(self, done: int, total: int) -> None:
        if self.progress.maximum() != total:
            self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _on_message(self, text: str) -> None:
        log.info("camera calibration: %s", text)

    def _on_finished(self, result) -> None:
        self._worker = None
        self.view.set_overlay_items([])
        _pmap, report, _sweep = result
        verdict = judge(report, self._sweep_used["marker_side_mm"])
        log.info("camera calibration fitted:\n%s", report)
        log.info("camera calibration verdict: %s", verdict.line)
        self._go("result")
        self._result, self._verdict = result, verdict
        self._saved, self._save_error = False, ""
        if verdict.level != REDO:
            self._save()
        self._refresh()
        # The sweep moved the gantry with the panel hidden.
        self.jog.refresh_position()

    def _on_failed(self, reason: str) -> None:
        """A sweep that ended early, stopped or otherwise. The gantry is
        left at the pose it reached, so the marker is centered again."""
        self._worker = None
        self.view.set_overlay_items([])
        stopped = isinstance(getattr(self.sender(), "error", None), Cancelled)
        log.info("camera calibration %s: %s",
                 "stopped" if stopped else "failed", reason)
        self._outcome = ("Calibration stopped. Center the marker again."
                         if stopped else f"Calibration failed: {reason} "
                         f"Center the marker again.")
        self._go("center")
        self.jog.refresh_position()

    def _save(self) -> None:
        """The map into the profile; the previous one is archived."""
        profile = self.session.profile
        if self._result is None or profile is None:
            return
        pmap, _report, _sweep = self._result
        config = pmap.to_config()
        try:
            profile.calibration.pixel_map = config
            profile.save_calibration()
        except Exception as exc:                     # noqa: BLE001
            log.error("pixel map not saved: %s", exc)
            self._save_error = describe_error(exc)
            self._refresh()
            return
        log.info("pixel map written to %s (degree %d, %s poses, %dx%d)",
                 profile.path, config.degree, config.n_poses,
                 config.image_size[0], config.image_size[1])
        self._saved, self._save_error = True, ""
        self._refresh()
        self.session.profile_changed.emit(profile)

    # -- display -------------------------------------------------------------

    def _refresh_camera(self, _label: str = "") -> None:
        """The upper camera on the picture while the sheet is shown."""
        camera = None if self.isHidden() else self._camera()
        if camera is not self.view.camera:
            self.view.set_camera(camera)
            # Last frame's marker belongs to last frame's camera.
            self._sighting = None
            self.view.set_overlay_items([])
        self._refresh()

    def _refresh(self) -> None:
        running = self._running()
        index = STEPS.index(self.step)
        free = not self.session.robot_lease.busy
        connected = self.session.robot is not None

        self.jog.setVisible(not running)
        self.placed_button.setEnabled(not running)
        self.slots.setEnabled(index >= 1 and not running and free
                              and connected)
        self.slots.setToolTip(
            "" if free or running else
            f"Robot busy: {self.session.robot_lease.holder}.")
        self.centered_button.setEnabled(index >= 2 and not running
                                        and self._centered())
        problem = self._problem()
        self.start_button.setEnabled(index >= 3 and not running and free
                                     and not problem)
        self.start_button.setVisible(not running)
        self.progress.setVisible(running)
        self.notice.setVisible(running)
        self.camera_note.setText("\n".join(
            line for line in (self._outcome, "" if running else problem)
            if line))
        self._show_marker(running)
        profile = self.session.profile
        self.center_how.setText(
            CENTER_BY_CLICK if profile is not None
            and profile.pixel_map is not None else CENTER_BY_ARROWS)
        self._show_result()
        self._show_marks(index, running)

    def _show_marker(self, running: bool) -> None:
        """MARKER FOUND or NO MARKER IN VIEW on the picture, and how far it
        is from the middle, while it is watched for."""
        sighting = self._sighting
        watching = (not running and self.view.camera is not None
                    and sighting is not None)
        self.marker_state = ("" if not watching else
                             MARKER_FOUND if self._seen() else NO_MARKER)
        self.view.set_status([self.marker_state] if self.marker_state else [])
        if self._centered():
            self.center_state.setText("In the middle.")
        elif self._seen():
            dx, dy = sighting.offset_px
            self.center_state.setText(
                f"{abs(dx):.0f} px {'right' if dx > 0 else 'left'}, "
                f"{abs(dy):.0f} px {'down' if dy > 0 else 'up'} of the "
                f"middle.")
        else:
            self.center_state.setText("")

    def _show_result(self) -> None:
        """Saved with Done, or why not with Redo calibration and Save
        anyway."""
        verdict = self._verdict
        unsaved = verdict is not None and not self._saved
        self.saved_banner.setVisible(self._saved)
        self.result_done_button.setVisible(self._saved)
        if unsaved:
            self.poor_banner.set_text(
                self._save_error or NOT_ACCURATE)
        self.poor_banner.setVisible(unsaved)
        self.redo_button.setVisible(unsaved)
        self.save_anyway_button.setVisible(unsaved)

    def _show_marks(self, index: int, running: bool) -> None:
        """Done before the step open now, to do there, waiting after."""
        for i, (name, card) in enumerate(self.cards.items()):
            if i < index or (name == "result" and self._saved):
                card.set_state(OK)
            elif i == index:
                card.set_state(TODO)
            else:
                card.set_state(BLOCKED, f"First: step {index + 1}.")
