"""The window everything else sits in (REDESIGN §4).

The TopBar across the top, and under it one stack of screens: Home, and
the pages that are a task's steps or a Robot setup item, and Robot setup
itself (`Home › Robot setup`, its rows' items under it). While a step is
shown, the StepBar is over it and the Footer (Back, Next) under it; Home,
Settings and a setup item have neither.

A **task** is a fixed order of steps (`gui.tasks`); the existing pages are
the steps' content, `STEP_PAGES` says which. One page can be several
steps - Picking is the Dish bottom step and the Picking step, Liquid
handling is all three of Liquid's own - and Deck, Tip and Dish bottom are
the same page and the same state in every task.

Whether a step is done, and the first need it has not got, are read from
the session each time (`tasks.step_state`): nothing records a visit. A
step whose need is not met is shown dimmed and disabled, under one banner
naming that need with a button that goes there: an earlier step, Home for
the robot, or the page a Robot setup item is set on (`SETUP_PAGES`) - the
breadcrumb then reads `Home › <task> › <item>`, and its crumb goes back.

A task opens on its first step not done, so one switched to after another
starts past what they share. Next is enabled on a step that is done or
optional; Deck is confirmed by its Next (`Session.confirm`), and so, until
Dish bottom has its own Save and Use it, is Dish bottom.

The window as before: Esc and STOP stop (two presses hold), closing waits
for the robot to be free, Park is what Home robot position was, Light
switches the rail lights.

A run is seen from every screen: while one has the robot, the top bar's
pill says what it is doing (`RUNS`; "Paused" and "Waiting for you" from
its page's `run_activity`) and STOP shows; Home's tiles are dimmed. Where
the run was started - the task and step, or the page - is kept, and a
click on the pill goes back there.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import _version as version
from .. import paths
from . import log_bridge, tasks
from .pages import (calibration_camera, calibration_pipette, camera_sheet,
                    home, labware, liquid, manual, picking, platforms_sheet,
                    position_sheet, robot_setup, routine, settings)
from .robot_monitor import RobotMonitor
from .session import MOCK_PROFILE_NAME, Session
from .widgets.banner import Banner
from .widgets.footer import Footer
from .widgets.jog_panel import HOME_DETAIL, HOME_TITLE, JogPanel, home_robot
from .widgets.overlays import DimCover, LockOverlay
from .widgets.step_bar import StepBar
from .widgets.toast import Toast
from .widgets.top_bar import TopBar
from .report import save_report_asking
from .workers import Worker, any_running

if TYPE_CHECKING:                       # app imports this module; annotations
    from .app import Options            # are strings, so the cycle is only a
                                        # type-checker's problem

__all__ = ["MainWindow", "PAGES", "SETUP_PAGES", "STEP_PAGES"]

_log = logging.getLogger(__name__)

# (attribute name, title in the breadcrumb, page class). The attribute is
# how the rest of the application reaches a page.
PAGES = (
    ("labware", labware.TITLE, labware.LabwarePage),
    ("tip_calibration", calibration_pipette.TITLE,
     calibration_pipette.PipetteCalibration),
    ("routine", routine.TITLE, routine.RoutinePage),
    ("picking", picking.TITLE, picking.PickingPage),
    ("liquid", liquid.TITLE, liquid.LiquidHandlingPage),
    ("manual", manual.TITLE, manual.ManualPage),
    ("robot_setup", robot_setup.TITLE, robot_setup.RobotSetupPage),
)

# The page each step shows.
STEP_PAGES = {
    "deck": "labware", "tip": "tip_calibration", "plan": "routine",
    "dish_bottom": "picking", "picking": "picking", "work": "manual",
    "well_center": "liquid", "liquid_plan": "liquid", "run": "liquid",
}

# The sheet each Robot setup item is set in (`SHEETS`).
CAMERA_SHEET = "camera_sheet"
CALIBRATION_SHEET = "calibration_sheet"
PLATFORMS_SHEET = "platforms_sheet"
POSITION_SHEET = "position_sheet"
SHEETS = (CAMERA_SHEET, CALIBRATION_SHEET, PLATFORMS_SHEET, POSITION_SHEET)
SETUP_PAGES = {
    "upper_camera": CAMERA_SHEET,
    "camera_calibration": CALIBRATION_SHEET,
    "lower_camera": CAMERA_SHEET,
    "platforms": PLATFORMS_SHEET,
    "calibration_position": POSITION_SHEET,
    "dish_position": POSITION_SHEET,
    "shake_position": POSITION_SHEET,
}

# The runs, by what their lease says it holds the robot for: the page the
# run is on, and what the top bar's pill says while it goes on.
RUNS = {
    "picking run": ("picking", "Picking"),
    "liquid run": ("liquid", "Handling liquid"),
    "camera calibration sweep": (CALIBRATION_SHEET,
                                 "Calibrating the camera"),
    "pipette calibration": ("tip_calibration", "Calibrating the tip"),
}

# The pill for a run's page's `run_activity`: its text and tone.
RUN_ACTIVITY = {
    "paused": ("Paused", "paused"),
    "waiting": ("Waiting for you", "waiting"),
}

# Steps their own Next confirms for the robot session.
CONFIRMED_BY_NEXT = ("deck", "dish_bottom")

# How often the steps' marks are read again: a position saved on a page has
# no signal of its own.
REFRESH_EVERY_MS = 1500

# After a first Stop, how long a second one holds the robot.
STOP_AGAIN_S = 10
# How often a held robot is looked at to be let go.
RELEASE_POLL_MS = 200
# How long closing the window waits for the robot to be free.
CLOSE_WAIT_S = 10

HELD_DETAIL = (
    "The robot finishes the command it is doing and takes no other until "
    "every job using it has stopped. The robot session, the labware and "
    "the tip are kept.")


class MainWindow(QMainWindow):
    def __init__(self, options: Options, parent: QWidget | None = None):
        super().__init__(parent)
        self.options = options
        # The version in the title: on a screenshot, which build it was.
        self.setWindowTitle(f"micropick {version.describe()}")
        self.setMinimumSize(1280, 800)
        self.resize(1400, 900)

        self.session = Session(options, parent=self)
        session = self.session

        self.top = TopBar()
        self.step_bar = StepBar()
        self.banner = Banner(tone="warn")
        self.footer = Footer()

        self.stack = QStackedWidget()
        self.home = home.HomePage(session)
        self.stack.addWidget(self.home)
        self.pages: dict[str, QWidget] = {"home": self.home}
        self._titles: dict[str, str] = {}
        for name, title, page_class in PAGES:
            page = page_class(session)
            self.pages[name] = page
            self._titles[name] = title
            setattr(self, f"{name}_page", page)
            self.stack.addWidget(page)
        # The stack in a holder, so the cover of a dimmed step lies over
        # the page and not over the banner that says why.
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.stack)
        self.dim = DimCover(holder)
        # Settings over whatever is shown, the Log one of its sections.
        self.settings_sheet = settings.SettingsSheet(session, holder)
        self.settings_page = self.settings_sheet.form
        self.log_page = self.settings_sheet.log
        self.settings_sheet.canceled.connect(self._sheet_closed)
        # Which attached camera is the upper and which the lower.
        self.camera_sheet = camera_sheet.CameraSheet(session, holder)
        self.camera_sheet.canceled.connect(self._sheet_closed)
        self.camera_sheet.done.connect(self._sheet_closed)
        # The camera calibration, its sweep a run.
        self.calibration_sheet = calibration_camera.CameraCalibrationSheet(
            session, holder)
        self.calibration_sheet.canceled.connect(self._sheet_closed)
        # What stands in each slot.
        self.platforms_sheet = platforms_sheet.PlatformsSheet(session, holder)
        self.platforms_sheet.canceled.connect(self._sheet_closed)
        self.platforms_sheet.done.connect(self._sheet_closed)
        # The three stored positions.
        self.position_sheet = position_sheet.PositionSheet(session, holder)
        self.position_sheet.canceled.connect(self._sheet_closed)
        self.position_sheet.done.connect(self._sheet_closed)
        # The Robot setup sheets, by their name in SETUP_PAGES and RUNS.
        self.sheets = {CAMERA_SHEET: self.camera_sheet,
                       CALIBRATION_SHEET: self.calibration_sheet,
                       PLATFORMS_SHEET: self.platforms_sheet,
                       POSITION_SHEET: self.position_sheet}
        self._titles[CALIBRATION_SHEET] = calibration_camera.TITLE
        # The breadcrumb under the sheet, given back when it closes.
        self._under_sheet: list[str] = []
        # Over the page, under the top bar: Light and Unlock stay at hand.
        self.lock_overlay = LockOverlay(holder)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top)
        layout.addWidget(self.step_bar)
        # The banner with its margins, hidden whole when there is no need.
        self._banner_row = QWidget()
        banner_row = QVBoxLayout(self._banner_row)
        banner_row.setContentsMargins(16, 12, 16, 0)
        banner_row.addWidget(self.banner)
        layout.addWidget(self._banner_row)
        layout.addWidget(holder, 1)
        layout.addWidget(self.footer)
        self.setCentralWidget(central)
        self.toast = Toast(self)

        # Where the window is: a task and its step, and the page shown.
        self.task: str | None = None
        # The page shown is a Robot setup item opened from Robot setup:
        # its first crumb goes back there.
        self._from_setup = False
        self.step: str | None = None
        # The task's step to go back to from a page out of the steps.
        self._task_step: str | None = None
        self._page = "home"
        self._need: tasks.Need | None = None
        # What the footer was last given (`_refresh`).
        self._navigation: dict | None = None
        # The run that has the robot and where it was started: (its
        # lease's holder, the task, the step), the last two None when it
        # was started out of a task's steps.
        self._run: tuple[str, str | None, str | None] | None = None

        # Looks at the robot every few seconds; started by `app.run`.
        self.monitor = RobotMonitor(session, self)
        self.monitor.failed.connect(self.home.show_error)
        self.home.task_chosen.connect(self.open_task)
        self.home.robot_setup_requested.connect(self.open_robot_setup)
        self.robot_setup_page.item_chosen.connect(
            lambda item: self.open_setup_item(item, from_setup=True))
        self.robot_setup_page.said.connect(self.say)
        self.camera_sheet.said.connect(self.say)
        self.platforms_sheet.said.connect(self.say)
        self.position_sheet.said.connect(self.say)
        self.step_bar.clicked.connect(
            lambda i: self.show_step(tasks.steps(self.task)[i]))
        self.footer.back_clicked.connect(self._back)
        self.footer.next_clicked.connect(self._next)
        self.top.home_clicked.connect(self.open_home)
        self.top.crumb_clicked.connect(self._crumb)
        self.top.settings_clicked.connect(self.open_settings)
        self.top.light_toggled.connect(self._toggle_lights)
        self.top.park_clicked.connect(self._park)
        self.top.stop_clicked.connect(self._stop)
        self.top.lock_toggled.connect(self._set_locked)
        self.top.status_clicked.connect(self.go_to_run)
        session.run_activity_changed.connect(self._show_run)
        self.lock_overlay.unlock_clicked.connect(
            lambda: self._set_locked(False))
        session.lock_changed.connect(self._show_lock)
        session.page_requested.connect(self.show_page)

        for signal in (session.profile_changed, session.robot_state_changed,
                       session.labware_changed, session.tip_changed,
                       session.routine_changed, session.settings_changed,
                       session.confirmations_changed,
                       session.robot_busy_changed):
            signal.connect(self._refresh)
        session.lights_changed.connect(self.top.set_light)
        self._lights_worker: Worker | None = None
        self._park_worker: Worker | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_EVERY_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        # Stop: the top bar's STOP and Esc, window-wide. No page binds Esc
        # itself: two shortcuts on one key in one window cancel each other
        # out and neither fires.
        self._stop_key = QShortcut(QKeySequence("Esc"), self)
        self._stop_key.setContext(Qt.ShortcutContext.WindowShortcut)
        self._stop_key.activated.connect(self._stop)
        self._stop_again_until = 0.0
        # A close is waiting for the robot (`closeEvent`).
        self._closing = False
        self._stop_reset = QTimer(self)
        self._stop_reset.setSingleShot(True)
        self._stop_reset.timeout.connect(self._disarm_stop)
        # The second Stop holds the robot (`_hold`); this lets it go once
        # nothing that uses it runs any more.
        self._release_timer = QTimer(self)
        self._release_timer.setInterval(RELEASE_POLL_MS)
        self._release_timer.timeout.connect(self._release_when_idle)

        # Installed before anything is loaded, so a failure during start-up
        # lands in the log rather than nowhere. Queued across threads by Qt,
        # which is what lets a worker log without touching the widget.
        self.log_bridge = log_bridge.install()
        self.log_bridge.record.connect(self.log_page.append)
        # And to a file per day, for when the window is closed and someone
        # asks what happened (`gui.report`).
        self.log_file = log_bridge.install_file(paths.logs_dir())

        self.open_home()
        self._start()

    # -- lifecycle -----------------------------------------------------------

    def _start(self) -> None:
        """Load the profile: the one the command line named, the mock's,
        or the last one used. The robot is connected by the monitor
        (`RobotMonitor`), once `app.run` starts it."""
        name = self.options.profile or (MOCK_PROFILE_NAME
                                        if self.options.mock else None)
        try:
            if name is None:
                self.session.load_last_profile()
            else:
                self.session.load_profile(name)
        except Exception as exc:                     # noqa: BLE001
            # Shown in full: a legacy profile's message is the instructions.
            _log.error("could not load the profile: %s", exc)
            self.home.show_error(str(exc))

    def closeEvent(self, event) -> None:
        """Nothing is left running: cameras hold grab threads, and the robot
        holds an axis that should be parked before the window disappears.

        While a run has the robot, closing asks first, and on yes stops it.
        A short job - a step, a Go to, Park - is waited for. Either way the
        window closes only once the robot is free: closing under a worker
        still moving it would take the robot session and the cameras from
        under it (the pipette calibration hears Stop only in its touch-up),
        and a retract sent into a move still going is the overlap the
        robot's lease is for."""
        if self._closing:
            # Another close while this one waits: this one decides.
            event.ignore()
            return
        self._closing = True
        try:
            self._close(event)
        finally:
            self._closing = False

    def _close(self, event) -> None:
        lease = self.session.robot_lease
        if lease.holding_run:
            answer = QMessageBox.question(
                self, "Close micropick",
                f"A run is in progress ({lease.holder}). Stop it and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.session.request_stop()
        if lease.busy:
            _log.info("closing: waiting for %s", lease.holder)
            deadline = time.monotonic() + CLOSE_WAIT_S
            while lease.busy and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
        if lease.busy:
            _log.warning("not closing: %s still has the robot", lease.holder)
            self.say(f"Not closed: the {lease.holder} is still stopping. "
                     f"Close again once it has.")
            event.ignore()
            return
        self.monitor.stop()
        self.session.shutdown()
        super().closeEvent(event)

    def say(self, text: str) -> None:
        """The answer to an action, as a toast."""
        self.toast.show_text(text)

    # -- where the window is --------------------------------------------------

    def current_page(self) -> QWidget:
        return self.stack.currentWidget()

    def _show(self, name: str, path: list[str], *, in_task: bool) -> None:
        page = self.pages[name]
        # A page dimmed as one step is not dimmed as anything else.
        page.setEnabled(True)
        self.dim.hide()
        self._close_sheets()
        self.stack.setCurrentWidget(page)
        self._page = name
        self._from_setup = False
        self.top.set_path(path)
        for widget in (self.step_bar, self.footer):
            widget.setVisible(in_task)
        if not in_task:
            self.step = None
            self._banner_row.hide()
            self._need = None

    def open_home(self) -> None:
        self.task = None
        self._show("home", [], in_task=False)
        self.home.refresh()

    def open_robot_setup(self) -> None:
        """Robot setup, out of any task: `Home › Robot setup`."""
        self.task = None
        self._show("robot_setup", [robot_setup.TITLE], in_task=False)

    def _set_task(self, task: str) -> None:
        self.task = task
        self.step_bar.set_steps([tasks.title(s) for s in tasks.steps(task)])

    def open_task(self, task: str) -> None:
        """On its first step not done; the last when all are."""
        self._set_task(task)
        steps = tasks.steps(task)
        first = next((s for s in steps
                      if not tasks.step_state(self.session, task, s).done),
                     steps[-1])
        self.show_step(first)

    def show_step(self, step: str) -> None:
        # Liquid's Tip step has no calibration.
        self.tip_calibration_page.set_task(self.task)
        self._show(STEP_PAGES[step], [tasks.TASKS[self.task][0]],
                   in_task=True)
        self.step = self._task_step = step
        self._need = None
        self._navigation = None
        self._refresh()

    def open_setup_item(self, item: str, *, from_setup: bool = False) -> None:
        """The page a Robot setup item is set on, out of the steps; its
        crumb back is Robot setup's when it was opened there (`from_setup`),
        else the task's when one is open. A camera is chosen in its sheet,
        over Robot setup or the step that needs it."""
        if SETUP_PAGES[item] in SHEETS:
            if from_setup and self._page != "robot_setup":
                self.open_robot_setup()
            self._under(tasks.SETUP_TITLES[item])
            if SETUP_PAGES[item] == CAMERA_SHEET:
                self.camera_sheet.open_for(item.removesuffix("_camera"))
            elif SETUP_PAGES[item] == POSITION_SHEET:
                self.position_sheet.open_for(item)
            else:
                self.sheets[SETUP_PAGES[item]].open_sheet()
            # The page under it is disabled: its keys are the sheet's now.
            self.stack.setEnabled(False)
            self.lock_overlay.raise_over()
            return
        path = [tasks.SETUP_TITLES[item]]
        if from_setup:
            path.insert(0, robot_setup.TITLE)
        elif self.task is not None:
            path.insert(0, tasks.TASKS[self.task][0])
        self._show(SETUP_PAGES[item], path, in_task=False)
        self._from_setup = from_setup

    def show_page(self, name: str) -> None:
        """A page asked for by name (`Session.page_requested`): a step of
        the open task when it is one, Home for the profile and the
        checklist that were there, else the page on its own."""
        if name in ("home", "profile"):
            self.open_home()
            return
        if name in ("settings", "log"):
            self.open_settings("log" if name == "log" else "general")
            return
        if SETUP_PAGES.get(name) in SHEETS:
            self.open_setup_item(name)
            return
        if self.task is not None:
            # The step shown when it is that page (Picking is two steps),
            # else the first that is.
            step = (self._task_step
                    if STEP_PAGES.get(self._task_step) == name else
                    next((s for s in tasks.steps(self.task)
                          if STEP_PAGES[s] == name), None))
            if step is not None:
                self.show_step(step)
                return
        path = [self._titles[name]]
        if self.task is not None:
            path.insert(0, tasks.TASKS[self.task][0])
        self._show(name, path, in_task=False)

    def open_settings(self, section: str = "general") -> None:
        """The Settings sheet over the screen shown, which stays as it is;
        the breadcrumb gains "Settings"."""
        if self.settings_sheet.isHidden():
            self._under(settings.TITLE)
        # Over the lock too: the settings and the log are not the robot's,
        # and Advanced's buttons are refused under it.
        self.settings_sheet.open_on(section)

    def _under(self, crumb: str) -> None:
        """A sheet is opened over the screen shown: the one shown closes,
        and the breadcrumb, kept to give back, gains `crumb`."""
        self._close_sheets()
        self._under_sheet = list(self.top.path)
        self.top.set_path([*self._under_sheet, crumb])

    def _sheet_closed(self) -> None:
        self.top.set_path(self._under_sheet)
        self.stack.setEnabled(not self.session.locked)

    def _close_sheets(self) -> bool:
        """Close the sheet shown, saving nothing; False when none was."""
        shown = [sheet for sheet in (self.settings_sheet, *self.sheets.values())
                 if not sheet.isHidden()]
        for sheet in shown:
            sheet.discard()
        if shown:
            self._sheet_closed()
        return bool(shown)

    def _crumb(self, index: int) -> None:
        """A crumb between Home and the last: the task. Under a sheet, any
        crumb closes it first; the one of the screen under it does only
        that."""
        if self._close_sheets():
            if index >= len(self._under_sheet) - 1:
                return
        if self._from_setup:
            self.open_robot_setup()
        elif self.task is not None:
            self.show_step(self._task_step)

    # -- steps ----------------------------------------------------------------

    def _refresh(self, _arg=None) -> None:
        """The marks, the banner and the footer, from the session now."""
        self._show_park()
        self._show_run()
        self._show_lock_button()
        if self.task is None or self.step is None:
            return
        steps = tasks.steps(self.task)
        states = [tasks.step_state(self.session, self.task, s)
                  for s in steps]
        index = steps.index(self.step)
        self.step_bar.set_state(index, [s.done for s in states])
        state = states[index]
        need = state.need
        if need != self._need:
            self._need = need
            self.banner.clear_actions()
            if need is not None:
                self.banner.set_text(need.text)
                go = self.banner.add_action(self._need_button(need))
                go.clicked.connect(lambda _=False, n=need: self.go_to(n))
        self._banner_row.setVisible(need is not None)
        page = self.pages[STEP_PAGES[self.step]]
        page.setEnabled(need is None)
        if need is None:
            self.dim.hide()
        elif not self.dim.isVisible():
            self.dim.open()
            self.lock_overlay.raise_over()
        confirmable = (self.step in CONFIRMED_BY_NEXT and need is None
                       and not (self.step == "deck"
                                and self.session.deck_problems()))
        # Only on a change: it takes the buttons out and puts them back.
        navigation = dict(next=index < len(steps) - 1,
                          next_enabled=state.done or state.optional
                          or confirmable)
        if navigation != self._navigation:
            self._navigation = navigation
            self.footer.set_navigation(**navigation)

    def _need_button(self, need: tasks.Need) -> str:
        if need.kind == "robot":
            return "Go to Home"
        if need.kind == "step":
            return f"Go to {tasks.title(need.key)}"
        return f"Go to {tasks.SETUP_TITLES[need.key]}"

    def go_to(self, need: tasks.Need) -> None:
        """Where the banner's button goes: the thing to do first."""
        if need.kind == "robot":
            self.open_home()
        elif need.kind == "step":
            self.show_step(need.key)
        else:
            self.open_setup_item(need.key)

    def _back(self) -> None:
        steps = tasks.steps(self.task)
        index = steps.index(self.step)
        if index == 0:
            self.open_home()
        else:
            self.show_step(steps[index - 1])

    def _next(self) -> None:
        steps = tasks.steps(self.task)
        if self.step in CONFIRMED_BY_NEXT:
            self.session.confirm(self.step)
        index = steps.index(self.step)
        if index < len(steps) - 1:
            self.show_step(steps[index + 1])

    # -- a run, seen from every screen ---------------------------------------

    def _show_run(self, _arg=None) -> None:
        """The pill, STOP and Home's tiles, from the robot's lease now."""
        lease = self.session.robot_lease
        holder = lease.holder if lease.holding_run else None
        if holder is None:
            self._run = None
        elif self._run is None or self._run[0] != holder:
            self._run = (holder, self.task, self.step)
        self.top.set_stop_visible(holder is not None)
        if holder is None:
            self.top.set_status(None)
            self.home.show_run(None)
            return
        page, text = RUNS.get(holder, (None, "Running"))
        owner = self.pages.get(page) or self.sheets.get(page)
        activity = owner.run_activity() if owner is not None else None
        self.top.set_status(*RUN_ACTIVITY.get(activity, (text, "busy")))
        _holder, task, _step = self._run
        self.home.show_run(
            tasks.TASKS[task][0] if task is not None else
            self._titles[page] if page is not None else "the run")

    def go_to_run(self) -> None:
        """The pill's click: back to where the run was started."""
        if self._run is None:
            return
        holder, task, step = self._run
        page = RUNS.get(holder, (None, ""))[0]
        if task is not None and self.task != task:
            self._set_task(task)
        item = next((i for i, p in SETUP_PAGES.items() if p == page), None)
        if task is not None and step is not None:
            self.show_step(step)
            if page in SHEETS:
                self.open_setup_item(item)        # over the step it was on
        elif item is not None and page not in STEP_PAGES.values():
            # A sheet over Robot setup, where it lives.
            self.open_setup_item(item, from_setup=page in SHEETS)
        elif page is not None:
            self.show_page(page)

    # -- the top bar's tools --------------------------------------------------

    def _robot_busy(self) -> bool:
        """Something has the robot (`Session.robot_lease`): a jog step, a
        run, a labware load, on any page."""
        return self.session.robot_lease.busy

    def _show_park(self) -> None:
        connected = self.session.robot is not None
        holder = self.session.robot_lease.holder
        locked = self.session.locked
        self.top.set_park_enabled(
            connected and holder is None and not locked,
            "Robot not connected." if not connected else
            "Robot locked." if locked else
            f"Robot busy: {holder}." if holder is not None else "")

    def _show_lock_button(self) -> None:
        run = self.session.robot_lease.holding_run
        holder = self.session.robot_lease.holder
        self.top.set_lock(self.session.locked, allowed=not run,
                          reason=f"Robot busy: {holder}." if run else "")

    def _set_locked(self, on: bool) -> None:
        """Lock or Unlock. The session refuses a lock while a run has the
        robot; the button then goes back to what the session says."""
        if not self.session.set_locked(on):
            self.say(f"Robot busy: {self.session.robot_lease.holder}.")
        self._show_lock(self.session.locked)

    def _show_lock(self, locked: bool) -> None:
        """The overlay over the page, and the page's keys and clicks off
        under it: the session refuses the moves, the page need not try."""
        self.lock_overlay.set_locked(locked)
        if not self.settings_sheet.isHidden():
            self.settings_sheet.raise_()
        self.stack.setEnabled(not locked and all(
            sheet.isHidden() for sheet in self.sheets.values()))
        self._show_lock_button()
        self._show_park()

    def _toggle_lights(self, _checked: bool) -> None:
        """The click flips the lights from the state the robot reports, not
        the one the button shows: had a workflow switched them since the
        last read, asking for the button's state would do nothing on the
        first click. The robot's answer, through lights_changed, is what the
        button settles on."""
        if self._lights_worker is not None and self._lights_worker.running:
            self.top.set_light(self.session.lights)
            return
        if self.session.robot is None:
            self.top.set_light(None)
            return
        worker = Worker(self.session.toggle_lights,
                        what="switching the lights")
        self._lights_worker = worker
        worker.finished.connect(self._lights_done)
        worker.failed.connect(self._lights_failed)
        worker.start()

    def _lights_done(self, _result=None) -> None:
        self._lights_worker = None
        self.top.set_light(self.session.lights)

    def _lights_failed(self, reason: str) -> None:
        self._lights_worker = None
        _log.error("lights: %s", reason)
        self.top.set_light(self.session.lights)

    def _park(self) -> None:
        """Park: all axes home, the head to the back right. Through the
        shown page's jog panel when it has one, so the keys and the panel's
        buttons are held while it moves, as for every other command on such
        a page; on its own worker otherwise."""
        robot = self.session.robot
        if robot is None:
            return
        if self._robot_busy():
            self.say("Robot busy.")
            return
        answer = QMessageBox.question(
            self, HOME_TITLE, HOME_DETAIL,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        # `==`, not `is`: see JogPanel._confirm.
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._robot_busy():                       # changed while asking
            self.say("Robot busy.")
            return
        panels = [panel for panel in
                  self.current_page().findChildren(JogPanel)
                  if panel.isVisible()]
        if panels and panels[0].run_job(lambda _log: home_robot(robot)):
            return
        lease = self.session.robot_lease.claim("Park")
        if lease is None:
            self.say(self.session.robot_lease.refusal)
            return
        worker = Worker(home_robot, robot, what="parking the robot",
                        lease=lease)
        self._park_worker = worker
        worker.finished.connect(self._park_done)
        worker.failed.connect(self._park_failed)
        _log.info("parking from the top bar")
        self.top.set_parking(True)
        worker.start()
        self._show_park()

    def _park_done(self, _result=None) -> None:
        self._park_worker = None
        self.top.set_parking(False)
        self._show_park()
        self.say("Parked.")
        # The panels on other pages still show the pose from before.
        for panel in self.findChildren(JogPanel):
            panel.refresh_position()

    def _park_failed(self, reason: str) -> None:
        self._park_worker = None
        self.top.set_parking(False)
        self._show_park()
        _log.error("park: %s", reason)
        self.say(f"Park failed: {reason}")

    # -- stop -----------------------------------------------------------------

    def _stop(self) -> None:
        """First press: every page stops at its next safe point. A second
        press within STOP_AGAIN_S while something still runs: the robot is
        held - no command after the one it is doing - whether or not a job
        listens (`Session.hold_robot`). The robot session is kept."""
        now = time.monotonic()
        if now < self._stop_again_until and any_running():
            self._hold()
            return
        self.session.request_stop()
        if not any_running():
            self.say("Stop: nothing is moving.")
            return
        self._stop_again_until = now + STOP_AGAIN_S
        self._stop_reset.start(STOP_AGAIN_S * 1000)
        self.say("Stopping after the current move. Press Stop again to "
                 "hold the robot.")

    def _disarm_stop(self) -> None:
        self._stop_again_until = 0.0

    def _hold(self) -> None:
        self._stop_reset.stop()
        self._disarm_stop()
        if self.session._api is None:
            return
        self.session.request_stop()
        self.session.hold_robot()
        self._release_timer.start()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Robot held")
        box.setText("Robot held.")
        box.setInformativeText(HELD_DETAIL)
        report = box.addButton("Save report for help…",
                               QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is report:
            save_report_asking(self.session, self)

    def _release_when_idle(self) -> None:
        """Let the robot go once no job that may use it runs: a job cut
        short still has its way out (a retract) refused until then."""
        if self._robot_busy():
            return
        self._release_timer.stop()
        self.session.release_robot()
        self.say("Robot released.")
