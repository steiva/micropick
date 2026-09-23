"""The window everything else sits in.

A row of tabs along the top and a stack under it, assembled by hand rather
than from a structural framework: the navigation is a handful of fixed entries
and a framework would decide the window's shape, the page lifecycle and the
theme along with it. Those are exactly the three things this application
needs to keep.

The tabs are in two groups. On the left, the pages a session goes through in
order — profile, labware, calibration, routine, picking. On the right edge,
the two that are used whenever they are needed rather than in sequence:
manual control and the log. A column down the side used to hold them all and
cost the pages 200 px of width for seven words.

`PAGES` is the only place the order and the names exist. The tab and the
stacked widget are built from the same row in one pass, so the entry an
operator clicks and the page that appears cannot come apart — the mistake
`workflows/jog` fixed by generating its help from its layout, in a different
shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMainWindow,
                               QStackedWidget, QStatusBar, QToolButton,
                               QVBoxLayout, QWidget)

from . import log_bridge
from .pages import (calibration, labware, log, manual, picking, profile,
                    routine)
from .session import MOCK_PROFILE_NAME, Session, Tip
from .theme import SPACING
from .widgets.feed_window import FeedWindow
from .workers import Worker

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
    # Routine before Picking: the plan is what a run picks into, and an
    # operator who meets the pages in order meets them in the order the
    # work happens.
    ("routine", routine.TITLE, routine.RoutinePage, SEQUENCE),
    ("picking", picking.TITLE, picking.PickingPage, SEQUENCE),
    ("manual", manual.TITLE, manual.ManualPage, ASIDE),
    ("log", log.TITLE, log.LogPage, ASIDE),
)

# The one colour this shell owns. A tip on the pipette is the first reason
# the robot crashes into something, and the indicator that says so has to be
# seen from across the room, on every page, in either theme - which is what
# the palette cannot promise and a fixed amber can.
TIP_ON = "#f0a030"
ICON_PX = 18


class StatusBar(QStatusBar):
    """Profile, robot, cameras, the tip and the lights, permanently visible.

    Permanent widgets rather than `showMessage`: a transient message is
    replaced by the next one, and these answer "what is this application
    currently attached to", which is never a transient question.

    The tip indicator shows the robot's record, not this application's: it
    is set from `Session.tip_changed`, which is re-read from the run's command
    log after connect and after every tip command. Three states, and the
    third is not the second: no tip, a tip on, and *could not tell*.
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

        # Checkable, so the bulb's own state is the robot's last answer and a
        # click asks for the other one. Not connected to anything here: the
        # window wires it to the session through a worker.
        self.lights = QToolButton()
        self.lights.setCheckable(True)
        self.lights.setAutoRaise(True)
        self.lights.setToolTip("Rail lights")

        for widget in (self._profile, self._robot, self._cameras,
                       self._deck_box, tip, self.lights):
            self.addPermanentWidget(widget)
        self.show_profile(None)
        self.show_robot("not connected")
        self.show_cameras([])
        self.show_tip(None)
        self.show_lights(None)

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
                + "\nFix it on the Labware page before any well move.")
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
            hint = "The robot's tip state could not be read. Re-read it on " \
                   "the Labware page before moving anything."
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
        # One exclusive group across both ends of the row, so choosing a tab
        # on the right clears the one on the left. The id is the page's index
        # in the stack.
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.tabs: dict[str, QToolButton] = {}

        self.session = Session(options, parent=self)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(SPACING // 2)
        aside: list[QToolButton] = []
        self.pages: dict[str, QWidget] = {}
        for name, title, page_class, group in PAGES:
            page = page_class(self.session)
            self.pages[name] = page
            setattr(self, f"{name}_page", page)
            tab = QToolButton()
            tab.setObjectName("navTab")
            tab.setText(title)
            tab.setCheckable(True)
            tab.setAutoRaise(True)
            # The mouse chooses pages; the keyboard drives the robot. A tab
            # with focus takes Space for itself, and on the manual page that
            # is a page change where a jog key was meant.
            tab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.nav.addButton(tab, self.stack.addWidget(page))
            self.tabs[name] = tab
            if group == ASIDE:
                aside.append(tab)
            else:
                tab_row.addWidget(tab)
        tab_row.addStretch(1)
        for tab in aside:
            tab_row.addWidget(tab)

        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        self.show_page(PAGES[0][0])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING // 2)
        layout.addLayout(tab_row)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.status = StatusBar()
        self.setStatusBar(self.status)

        self.session.profile_changed.connect(self._on_profile_changed)
        self.session.robot_state_changed.connect(self.status.show_robot)
        self.session.camera_opened.connect(self._show_cameras)
        self.session.camera_closed.connect(self._show_cameras)
        self.session.tip_changed.connect(self._show_tip)
        self.session.labware_changed.connect(self._show_deck_problems)
        self.session.profile_changed.connect(self._show_deck_problems)
        self.session.robot_state_changed.connect(self._show_deck_problems)
        self.session.lights_changed.connect(self.status.show_lights)
        self.status.lights.clicked.connect(self._toggle_lights)
        self._lights_worker: Worker | None = None
        self.status.camera_clicked.connect(self._show_feed)
        self._feeds: dict[str, FeedWindow] = {}
        self._feed_workers: dict[str, Worker] = {}
        self.status.show_robot(self.session.robot_state)

        # Installed before anything is loaded, so a failure during start-up
        # lands in the log rather than nowhere. Queued across threads by Qt,
        # which is what lets a worker log without touching the widget.
        self.log_bridge = log_bridge.install()
        self.log_bridge.record.connect(self.log_page.append)

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
        worker = Worker(self.session.open_camera, label)
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

    def _show_deck_problems(self, _arg=None) -> None:
        self.status.show_deck_problems(self.session.deck_problems())

    def _show_tip(self, tip) -> None:
        self.status.show_tip(tip if self.session.robot is not None else None)

    def _toggle_lights(self, checked: bool) -> None:
        """The click asks for the state the button now shows; the robot's
        answer, through lights_changed, is what the button settles on."""
        if self._lights_worker is not None and self._lights_worker.running:
            self.status.show_lights(self.session.lights)
            return
        if self.session.robot is None:
            return
        self.status.lights.setEnabled(False)
        worker = Worker(self.session.set_lights, checked)
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

    def show_page(self, name: str) -> None:
        """Bring a page to the front by name, keeping the tabs in step."""
        self.tabs[name].setChecked(True)
        self.stack.setCurrentWidget(self.pages[name])
