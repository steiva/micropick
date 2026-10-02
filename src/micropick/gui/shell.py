"""The window everything else sits in.

A row of tabs along the top and a stack under it, assembled by hand rather
than from a structural framework: the navigation is a handful of fixed entries
and a framework would decide the window's shape, the page lifecycle and the
theme along with it. Those are exactly the three things this application
needs to keep.

The tabs are in two groups. On the left, the pages a session goes through in
order — profile, labware, calibration, plate plan, picking. On the right edge,
the three that are used whenever they are needed rather than in sequence:
liquid handling, manual control and the log. A column down the side used to hold them all and
cost the pages 200 px of width for seven words.

The chosen tab opens into its page, as a browser's does. The strip the tabs
sit on is qdarktheme's raised surface (a `Panel` frame, like a card); the
chosen tab has the window's own background, from qdarktheme's QWidget rule,
so it reads as the top of the page below it and the page needs no title of
its own. No colour is named here, for the reason `theme/__init__` gives.

`PAGES` is the only place the order and the names exist. The tab and the
stacked widget are built from the same row in one pass, so the entry an
operator clicks and the page that appears cannot come apart — the mistake
`workflows/jog` fixed by generating its help from its layout, in a different
shape.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QStackedWidget, QStatusBar,
                               QToolButton, QVBoxLayout, QWidget)

from .. import paths
from . import log_bridge
from .pages import (calibration, labware, liquid, log, manual, picking,
                    profile, routine, settings)
from .session import MOCK_PROFILE_NAME, Session, Tip
from .theme import SPACING
from .widgets.feed_window import FeedWindow
from .widgets.activity import ActivityIndicator
from .widgets.jog_panel import HOME_DETAIL, HOME_TITLE, JogPanel, home_robot
from .report import save_report_asking
from .workers import Worker, any_running

if TYPE_CHECKING:                       # app imports this module; annotations
    from .app import Options            # are strings, so the cycle is only a
                                        # type-checker's problem

__all__ = ["MainWindow", "StatusBar", "PAGES"]

_log = logging.getLogger(__name__)

# Which end of the tab row a page sits at.
SEQUENCE, ASIDE = "sequence", "aside"

# (attribute name, title on the tab, page class, group). The attribute is how
# the rest of the application reaches a page; the title is what is on screen.
PAGES = (
    ("profile", profile.TITLE, profile.ProfilePage, SEQUENCE),
    ("labware", labware.TITLE, labware.LabwarePage, SEQUENCE),
    ("calibration", calibration.TITLE, calibration.CalibrationPage, SEQUENCE),
    # Plate plan before Picking: the plan is what a run picks into, and an
    # operator who meets the pages in order meets them in the order the
    # work happens.
    ("routine", routine.TITLE, routine.RoutinePage, SEQUENCE),
    ("picking", picking.TITLE, picking.PickingPage, SEQUENCE),
    # Beside Manual control: moving liquid is a tool used when it is needed,
    # before picking or days after it, not a stage of the session.
    ("liquid", liquid.TITLE, liquid.LiquidHandlingPage, ASIDE),
    ("manual", manual.TITLE, manual.ManualPage, ASIDE),
    ("log", log.TITLE, log.LogPage, ASIDE),
)

# The one colour this shell owns. A tip on the pipette is the first reason
# the robot crashes into something, and the indicator that says so has to be
# seen from across the room, on every page, in either theme - which is what
# the palette cannot promise and a fixed amber can.
TIP_ON = "#f0a030"
ICON_PX = 18

# The Stop button: a fixed red for the same reason as TIP_ON.
STOP_TEXT = "Stop"
STOP_NOW_TEXT = "Stop NOW"
STOP_STYLE = ("QToolButton { background: #c62828; color: white; "
              "font-weight: bold; border-radius: 4px; padding: 2px 10px; }"
              "QToolButton:pressed { background: #8e0000; }")
# After a first Stop, how long a second one halts the robot at once.
STOP_AGAIN_S = 10

# What to do after the robot was stopped at once, in order.
HALTED_STEPS = (
    "1. Make sure nothing is in the robot's way, then go to the Profile "
    "page and press New robot session + home. The robot lifts the tip and "
    "goes to its home position.\n\n"
    "2. Load the labware again on the Robot & Deck page: the new session "
    "starts with an empty deck.\n\n"
    "3. If the tip holds liquid or cuboids, put them back where they came "
    "from (Liquid handling or Manual control) before anything else.\n\n"
    "4. Plate plans keep their progress: a picking run carries on from the "
    "well it was at.")


class NavTab(QWidget):
    """One page's tab: a title that is clicked.

    A plain widget with a styled background rather than a QToolButton,
    because qdarktheme pins every tool button's background to transparent
    and the chosen tab has to carry the window's. `selected` is a dynamic
    property for the stylesheet to match on.
    """

    clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navTab")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The mouse chooses pages; the keyboard drives the robot.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label = QLabel(title, self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.label)
        self.setProperty("selected", False)

    def text(self) -> str:
        return self.label.text()

    def isChecked(self) -> bool:
        return bool(self.property("selected"))

    def setChecked(self, on: bool) -> None:
        if self.isChecked() == bool(on):
            return
        self.setProperty("selected", bool(on))
        # A dynamic property is not watched: the style is reapplied by hand.
        for widget in (self, self.label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatusBar(QStatusBar):
    """Profile, robot, cameras, the tip, Home and the lights, permanently
    visible.

    Permanent widgets rather than `showMessage`: a transient message is
    replaced by the next one, and these answer "what is this application
    currently attached to", which is never a transient question.

    The tip indicator shows the robot's record, not this application's: it
    is set from `Session.tip_changed`, which is re-read from the run's command
    log after connect and after every tip command. Three states, and the
    third is not the second: no tip, a tip on, and *could not tell*.

    Home robot position is here so it is one click from every page, not
    only from the pages with a jog panel. Like the lights button it is not
    connected here; the window decides when the robot may be homed.
    """

    # A click on a camera button; carries the label.
    camera_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._profile = QLabel()
        self._robot = QLabel()

        # One button per camera of the profile, a window with its feed on
        # click. From here rather than from a page, because a camera is
        # opened on the Profile page and looked at everywhere else.
        self._cameras = QWidget()
        self._camera_row = QHBoxLayout(self._cameras)
        self._camera_row.setContentsMargins(SPACING, 0, SPACING, 0)
        self._camera_row.setSpacing(SPACING // 2)
        self._camera_buttons: dict[str, QToolButton] = {}
        self._open_cameras: list[str] = []

        # The deck hazard: labware in a module slot without the module's
        # offset, which is a crash on the first well move. Hidden until
        # there is one; then amber, on every page, until it is fixed.
        self._deck_icon = QLabel()
        self._deck = QLabel()
        self._deck_box = QWidget()
        deck_row = QHBoxLayout(self._deck_box)
        deck_row.setContentsMargins(SPACING, 0, SPACING, 0)
        deck_row.setSpacing(SPACING // 2)
        deck_row.addWidget(self._deck_icon)
        deck_row.addWidget(self._deck)
        self._deck_box.hide()

        self._tip_icon = QLabel()
        self._tip = QLabel()
        tip = QWidget()
        row = QHBoxLayout(tip)
        row.setContentsMargins(SPACING, 0, SPACING, 0)
        row.setSpacing(SPACING // 2)
        row.addWidget(self._tip_icon)
        row.addWidget(self._tip)

        # The gear: this computer's settings, a page with no tab.
        self.settings = QToolButton()
        self.settings.setAutoRaise(True)
        self.settings.setIcon(qta.icon("mdi6.cog-outline"))
        self.settings.setToolTip("Settings: the robot's address, and where "
                                 "outputs are saved")
        self.settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Stop, on every page: red and labelled, so it is found without
        # looking for it. What it does is the window's (`MainWindow._stop`).
        self.stop = QToolButton()
        self.stop.setText(STOP_TEXT)
        self.stop.setIcon(qta.icon("mdi6.stop-circle", color="white"))
        self.stop.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.stop.setStyleSheet(STOP_STYLE)
        self.stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop.setToolTip(
            "Stop what the robot is doing (Esc). Press once: everything stops "
            "after the current move. Press again while it is still moving: "
            "the robot stops at once, and its session has to be started "
            "again.")

        self.home = QToolButton()
        self.home.setAutoRaise(True)
        self.home.setText("Home robot position")
        self.home.setIcon(qta.icon("mdi6.home-outline"))
        self.home.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # The keys belong to the robot's axes; a focused button would take
        # Space.
        self.home.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Checkable, so the bulb's own state is the robot's last answer and a
        # click asks for the other one. Not connected to anything here: the
        # window wires it to the session through a worker.
        self.lights = QToolButton()
        self.lights.setCheckable(True)
        self.lights.setAutoRaise(True)
        self.lights.setToolTip("Rail lights")

        # What is running, on the left; see `widgets.activity`.
        self.activity = ActivityIndicator()
        self.addWidget(self.activity, 1)

        for widget in (self._profile, self._robot, self._cameras,
                       self._deck_box, tip, self.stop, self.home, self.lights,
                       self.settings):
            self.addPermanentWidget(widget)
        self.show_profile(None)
        self.show_robot("not connected")
        self.show_cameras([])
        self.show_tip(None)
        self.show_lights(None)
        self.show_home(False)

    def show_home(self, connected: bool, busy: bool = False) -> None:
        self.home.setEnabled(connected and not busy)
        self.home.setToolTip(
            "Send the gantry to its home position on all axes. Asks first."
            if connected else "Home robot position: not connected")

    def show_profile(self, name: str | None) -> None:
        self._profile.setText(f"profile: {name or 'none'}")

    def show_robot(self, state: str) -> None:
        self._robot.setText(f"robot: {state}")

    def set_camera_labels(self, labels: list[str]) -> None:
        """The profile's cameras, one button each. Called on profile change."""
        for button in self._camera_buttons.values():
            self._camera_row.removeWidget(button)
            button.deleteLater()
        self._camera_buttons = {}
        for label in labels:
            button = QToolButton()
            button.setAutoRaise(True)
            button.setText(label)
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.clicked.connect(lambda _c=False, name=label:
                                   self.camera_clicked.emit(name))
            self._camera_row.addWidget(button)
            self._camera_buttons[label] = button
        self.show_cameras(self._open_cameras)

    def show_cameras(self, open_labels: list[str]) -> None:
        """Which of them are open: the icon says, the click opens either way."""
        self._open_cameras = list(open_labels)
        for label, button in self._camera_buttons.items():
            is_open = label in open_labels
            button.setIcon(qta.icon("mdi6.camera", color=TIP_ON) if is_open
                           else qta.icon("mdi6.camera-outline"))
            button.setToolTip(f"{label}: open - click to show its feed" if is_open
                              else f"{label}: closed - click to open it and show "
                                   f"its feed")

    def show_deck_problems(self, problems: list) -> None:
        """Slots holding labware without their module's offset; [] hides."""
        if not problems:
            self._deck_box.hide()
            return
        slots = ", ".join(p.slot for p in problems)
        self._deck_icon.setPixmap(
            qta.icon("mdi6.alert", color=TIP_ON).pixmap(ICON_PX, ICON_PX))
        self._deck.setText(f"<b style='color:{TIP_ON}'>DECK: slot{'s' if len(problems) > 1 else ''} "
                           f"{slots} without module offset</b>")
        hint = ("\n".join(p.describe() for p in problems)
                + "\nFix it on the Robot & Deck page before any well move.")
        self._deck_icon.setToolTip(hint)
        self._deck.setToolTip(hint)
        self._deck_box.show()

    def show_tip(self, tip: Tip | None) -> None:
        """None: no run, so there is nothing to say. Otherwise the record."""
        if tip is None:
            self._tip_icon.clear()
            self._tip.setText("")
            self._tip_icon.setToolTip("")
            return
        if tip.attached:
            icon = qta.icon("mdi6.eyedropper", color=TIP_ON)
            self._tip.setText(f"<b style='color:{TIP_ON}'>{tip.describe()}</b>")
            hint = "The robot reports a tip on the pipette. Every move is " \
                   "that much lower than it looks."
        elif tip.attached is None:
            icon = qta.icon("mdi6.help-circle-outline", color=TIP_ON)
            self._tip.setText(f"<b style='color:{TIP_ON}'>tip: unknown</b>")
            hint = "The robot's tip state could not be read. Connect again " \
                   "on the Profile page before moving anything."
        else:
            icon = qta.icon("mdi6.eyedropper-off")
            self._tip.setText("no tip")
            hint = "The robot reports no tip on the pipette."
        self._tip_icon.setPixmap(icon.pixmap(ICON_PX, ICON_PX))
        self._tip_icon.setToolTip(hint)
        self._tip.setToolTip(hint)

    def show_lights(self, on: bool | None) -> None:
        """None: not connected, or not readable; the button is then off."""
        self.lights.setEnabled(on is not None)
        self.lights.blockSignals(True)
        self.lights.setChecked(bool(on))
        self.lights.blockSignals(False)
        if on is None:
            self.lights.setIcon(qta.icon("mdi6.lightbulb-outline"))
            self.lights.setToolTip("Rail lights: not connected")
        elif on:
            self.lights.setIcon(qta.icon("mdi6.lightbulb-on", color=TIP_ON))
            self.lights.setToolTip("Rail lights are on. Click to switch off.")
        else:
            self.lights.setIcon(qta.icon("mdi6.lightbulb-off-outline"))
            self.lights.setToolTip("Rail lights are off. Click to switch on.")


class MainWindow(QMainWindow):
    def __init__(self, options: Options, parent: QWidget | None = None):
        super().__init__(parent)
        self.options = options
        self.setWindowTitle("micropick")
        self.resize(1400, 900)

        self.stack = QStackedWidget()
        self.tabs: dict[str, NavTab] = {}

        self.session = Session(options, parent=self)

        # The strip: a Panel frame, so qdarktheme gives it the raised
        # surface; the tabs sit on its bottom edge and the page starts
        # directly under it.
        self.strip = QFrame()
        self.strip.setObjectName("tabStrip")
        self.strip.setFrameShape(QFrame.Shape.Panel)
        tab_row = QHBoxLayout(self.strip)
        tab_row.setContentsMargins(SPACING, SPACING, SPACING, 0)
        tab_row.setSpacing(2)
        aside: list[NavTab] = []
        self.pages: dict[str, QWidget] = {}
        for name, title, page_class, group in PAGES:
            page = page_class(self.session)
            self.pages[name] = page
            setattr(self, f"{name}_page", page)
            self.stack.addWidget(page)
            tab = NavTab(title)
            tab.clicked.connect(lambda name=name: self.show_page(name))
            self.tabs[name] = tab
            if group == ASIDE:
                aside.append(tab)
            else:
                tab_row.addWidget(tab, 0, Qt.AlignmentFlag.AlignBottom)
        tab_row.addStretch(1)
        for tab in aside:
            tab_row.addWidget(tab, 0, Qt.AlignmentFlag.AlignBottom)
        # Settings: in the stack, opened by the gear, with no tab.
        self.settings_page = settings.SettingsPage(self.session)
        self.pages["settings"] = self.settings_page
        self.stack.addWidget(self.settings_page)

        self.show_page(PAGES[0][0])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.strip)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.status = StatusBar()
        self.setStatusBar(self.status)

        self.session.profile_changed.connect(self._on_profile_changed)
        self.session.robot_state_changed.connect(self._show_robot)
        self.session.settings_changed.connect(self._show_robot)
        self.session.camera_opened.connect(self._show_cameras)
        self.session.camera_closed.connect(self._show_cameras)
        self.session.tip_changed.connect(self._show_tip)
        self.session.labware_changed.connect(self._show_deck_problems)
        self.session.profile_changed.connect(self._show_deck_problems)
        self.session.robot_state_changed.connect(self._show_deck_problems)
        self.session.lights_changed.connect(self.status.show_lights)
        self.status.lights.clicked.connect(self._toggle_lights)
        self._lights_worker: Worker | None = None
        self.status.home.clicked.connect(self._home_robot)
        self.session.robot_state_changed.connect(self._show_home)
        self._home_worker: Worker | None = None
        # Stop: the button and Esc, window-wide. The pages that bound Esc to
        # their own stop leave it to this one: two shortcuts on one key in
        # one window cancel each other out and neither fires.
        self.status.stop.clicked.connect(self._stop)
        self._stop_key = QShortcut(QKeySequence("Esc"), self)
        self._stop_key.setContext(Qt.ShortcutContext.WindowShortcut)
        self._stop_key.activated.connect(self._stop)
        self._stop_again_until = 0.0
        self._stop_reset = QTimer(self)
        self._stop_reset.setSingleShot(True)
        self._stop_reset.timeout.connect(self._disarm_stop)
        self._halt_worker: Worker | None = None
        self.status.camera_clicked.connect(self._show_feed)
        self._feeds: dict[str, FeedWindow] = {}
        self._feed_workers: dict[str, Worker] = {}
        self._show_robot()
        self.status.settings.clicked.connect(
            lambda: self.show_page("settings"))

        # Installed before anything is loaded, so a failure during start-up
        # lands in the log rather than nowhere. Queued across threads by Qt,
        # which is what lets a worker log without touching the widget.
        self.log_bridge = log_bridge.install()
        self.log_bridge.record.connect(self.log_page.append)
        # And to a file per day, for when the window is closed and someone
        # asks what happened (`gui.report`).
        self.log_file = log_bridge.install_file(paths.logs_dir())

        self._start()

    # -- lifecycle -----------------------------------------------------------

    def _start(self) -> None:
        """Load whatever the command line named. Nothing else runs by itself.

        Connecting the robot and opening cameras stay deliberate acts even in
        mock mode: an application that reaches for hardware because it was
        launched is one that does it at the wrong moment eventually.
        """
        name = self.options.profile or (MOCK_PROFILE_NAME
                                        if self.options.mock else None)
        if name is None:
            return
        index = self.profile_page.chooser.findText(name)
        if index < 0:
            _log.error("no profile %r to load at start-up", name)
            return
        self.profile_page.chooser.setCurrentIndex(index)
        self.profile_page.load_selected()

    def closeEvent(self, event) -> None:
        """Nothing is left running: cameras hold grab threads, and the robot
        holds an axis that should be parked before the window disappears."""
        self.session.shutdown()
        super().closeEvent(event)

    # -- display -------------------------------------------------------------

    def _on_profile_changed(self, profile) -> None:
        self.status.show_profile(profile.name if profile is not None else None)
        self.status.set_camera_labels(
            sorted(profile.cameras) if profile is not None else [])

    def _show_cameras(self, label: str) -> None:
        self.status.show_cameras(self.session.open_cameras)
        # A camera that was closed on the Profile page has no feed to show.
        camera = self.session.camera(label)
        window = self._feeds.get(label)
        if window is not None:
            if camera is None:
                window.hide()
            window.view.set_camera(camera)

    # -- feed windows --------------------------------------------------------

    def _show_feed(self, label: str) -> None:
        """The feed in its window; the camera opened first if it is not."""
        camera = self.session.camera(label)
        if camera is not None:
            self._feed(label).show_camera(camera)
            return
        if label in self._feed_workers and self._feed_workers[label].running:
            return
        worker = Worker(self.session.open_camera, label,
                        what=f"opening camera {label!r}")
        worker.label = label
        self._feed_workers[label] = worker
        # Bound methods, not lambdas: Qt queues them onto this thread, and
        # a lambda would build the window on the worker's.
        worker.finished.connect(self._feed_opened)
        worker.failed.connect(self._feed_failed)
        _log.info("opening camera %r for its feed window", label)
        worker.start()

    def _feed_opened(self, camera) -> None:
        label = self.sender().label
        self._feed_workers.pop(label, None)
        self._feed(label).show_camera(camera)

    def _feed_failed(self, reason: str) -> None:
        label = self.sender().label
        self._feed_workers.pop(label, None)
        _log.error("could not open camera %r: %s", label, reason)

    def _feed(self, label: str) -> FeedWindow:
        window = self._feeds.get(label)
        if window is None:
            window = FeedWindow(label, self)
            self._feeds[label] = window
        return window

    def _show_robot(self, _arg=None) -> None:
        """The robot's state, and where Connect goes while it is not
        connected: the address is the first thing to check when it fails."""
        state = self.session.robot_state
        if not self.session.mock and self.session.robot is None:
            state += f" ({self.session.robot_address})"
        self.status.show_robot(state)

    def _show_deck_problems(self, _arg=None) -> None:
        self.status.show_deck_problems(self.session.deck_problems())

    def _show_tip(self, tip) -> None:
        self.status.show_tip(tip if self.session.robot is not None else None)

    def _toggle_lights(self, _checked: bool) -> None:
        """The click flips the lights from the state the robot reports, not
        the one the button shows: had a workflow switched them since the
        last read, asking for the button's state would do nothing on the
        first click. The robot's answer, through lights_changed, is what the
        button settles on."""
        if self._lights_worker is not None and self._lights_worker.running:
            self.status.show_lights(self.session.lights)
            return
        if self.session.robot is None:
            return
        self.status.lights.setEnabled(False)
        worker = Worker(self.session.toggle_lights,
                        what="switching the lights")
        self._lights_worker = worker
        worker.finished.connect(self._lights_done)
        worker.failed.connect(self._lights_failed)
        worker.start()

    def _lights_done(self, _result=None) -> None:
        self._lights_worker = None
        self.status.show_lights(self.session.lights)

    def _lights_failed(self, reason: str) -> None:
        self._lights_worker = None
        _log.error("lights: %s", reason)
        self.status.show_lights(self.session.lights)

    # -- home ----------------------------------------------------------------

    def _show_home(self, _state=None) -> None:
        busy = self._home_worker is not None and self._home_worker.running
        self.status.show_home(self.session.robot is not None, busy)

    def _robot_busy(self) -> bool:
        """Anything that may be talking to the robot: a jog panel's job on
        any page, or any worker - a picking run, a sweep, a labware load.
        Coarse on purpose: a camera opening also counts, and waiting for it
        costs seconds, where homing through a run costs the run."""
        return (any(panel.busy for panel in self.findChildren(JogPanel))
                or any_running())

    # -- stop ----------------------------------------------------------------

    def _stop(self) -> None:
        """First press: every page stops at its next safe point. A second
        press within STOP_AGAIN_S while something still runs: the robot is
        stopped at once (`Session.halt_robot`)."""
        now = time.monotonic()
        if now < self._stop_again_until and any_running():
            self._halt()
            return
        self.session.request_stop()
        if not any_running():
            self.status.showMessage("Stop: nothing is moving.", 4000)
            return
        self._stop_again_until = now + STOP_AGAIN_S
        self.status.stop.setText(STOP_NOW_TEXT)
        self._stop_reset.start(STOP_AGAIN_S * 1000)
        self.status.showMessage(
            "Stopping after the current move. Press Stop again to stop the "
            "robot at once (its session then has to be started again).",
            STOP_AGAIN_S * 1000)

    def _disarm_stop(self) -> None:
        self._stop_again_until = 0.0
        self.status.stop.setText(STOP_TEXT)

    def _halt(self) -> None:
        self._stop_reset.stop()
        self._disarm_stop()
        if self.session._api is None or (self._halt_worker is not None
                                         and self._halt_worker.running):
            return
        worker = Worker(self.session.halt_robot, what="stopping the robot")
        self._halt_worker = worker
        worker.finished.connect(self._halted)
        worker.failed.connect(self._halt_failed)
        worker.start()

    def _halted(self, _result=None) -> None:
        self._halt_worker = None
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Robot stopped")
        box.setText("The robot was stopped where it is.")
        box.setInformativeText(HALTED_STEPS)
        report = box.addButton("Save report for help…",
                               QMessageBox.ButtonRole.ActionRole)
        profile = box.addButton("Go to Profile",
                                QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is report:
            save_report_asking(self.session, self)
        elif box.clickedButton() is profile:
            self.show_page("profile")

    def _halt_failed(self, reason: str) -> None:
        self._halt_worker = None
        _log.error("halt: %s", reason)
        QMessageBox.critical(
            self, "Stop", f"The robot could not be stopped from here: {reason}"
                          f"\n\nUse the robot's own power switch if it is "
                          f"still moving.")

    def _home_robot(self) -> None:
        """Home from the status bar. Through the shown page's jog panel when
        it has one, so the keys and the panel's buttons are held while it
        moves, as for every other command on such a page; on its own worker
        otherwise."""
        robot = self.session.robot
        if robot is None:
            return
        if self._robot_busy():
            self.status.showMessage("Not now: the robot is busy. Home it when "
                                    "it is done.", 6000)
            return
        answer = QMessageBox.question(
            self, HOME_TITLE, HOME_DETAIL,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        # `==`, not `is`: see JogPanel._confirm.
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._robot_busy():                       # changed while asking
            self.status.showMessage("Not now: the robot is busy.", 6000)
            return
        panels = [panel for panel in
                  self.stack.currentWidget().findChildren(JogPanel)
                  if panel.isVisible()]
        if panels and panels[0].run_job(lambda _log: home_robot(robot)):
            return
        worker = Worker(home_robot, robot, what="homing the robot")
        self._home_worker = worker
        worker.finished.connect(self._home_done)
        worker.failed.connect(self._home_failed)
        _log.info("homing from the status bar")
        worker.start()
        self._show_home()

    def _home_done(self, _result=None) -> None:
        self._home_worker = None
        self._show_home()
        self.status.showMessage("Homed.", 4000)
        # The panels on other pages still show the pose from before.
        for panel in self.findChildren(JogPanel):
            panel.refresh_position()

    def _home_failed(self, reason: str) -> None:
        self._home_worker = None
        self._show_home()
        _log.error("home: %s", reason)
        self.status.showMessage(f"Home failed: {reason}", 8000)

    def show_page(self, name: str) -> None:
        """Bring a page to the front by name, keeping the tabs in step."""
        for other, tab in self.tabs.items():
            tab.setChecked(other == name)
        self.stack.setCurrentWidget(self.pages[name])
