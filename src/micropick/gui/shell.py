"""The window everything else sits in.

A list on the left and a stack on the right, assembled by hand rather than from
a structural framework: the navigation is a handful of fixed entries and a framework
would decide the window's shape, the page lifecycle and the theme along with
it. Those are exactly the three things this application needs to keep.

`PAGES` is the only place the order and the names exist. The list item and the
stacked widget are built from the same row in one pass, so the entry an
operator clicks and the page that appears cannot come apart — the mistake
`workflows/jog` fixed by generating its help from its layout, in a different
shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QStackedWidget, QStatusBar, QToolButton,
                               QWidget)

from . import log_bridge
from .pages import (calibration, labware, log, manual, picking, profile,
                    routine)
from .session import MOCK_PROFILE_NAME, Session, Tip
from .theme import SPACING
from .workers import Worker

if TYPE_CHECKING:                       # app imports this module; annotations
    from .app import Options            # are strings, so the cycle is only a
                                        # type-checker's problem

__all__ = ["MainWindow", "StatusBar", "PAGES"]

_log = logging.getLogger(__name__)

# (attribute name, title in the list, page class). The attribute is how the
# rest of the application reaches a page; the title is what is on screen.
PAGES = (
    ("profile", profile.TITLE, profile.ProfilePage),
    ("labware", labware.TITLE, labware.LabwarePage),
    ("manual", manual.TITLE, manual.ManualPage),
    ("calibration", calibration.TITLE, calibration.CalibrationPage),
    ("picking", picking.TITLE, picking.PickingPage),
    ("routine", routine.TITLE, routine.RoutinePage),
    ("log", log.TITLE, log.LogPage),
)

NAV_WIDTH = 200

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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._profile = QLabel()
        self._robot = QLabel()
        self._cameras = QLabel()

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

        for widget in (self._profile, self._robot, self._cameras, tip,
                       self.lights):
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

    def show_cameras(self, labels: list[str]) -> None:
        self._cameras.setText(f"cameras: {', '.join(labels) or 'none'}")

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

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(NAV_WIDTH)
        self.nav.setUniformItemSizes(True)
        # The mouse chooses pages; the keyboard drives the robot. A list with
        # focus takes the arrow keys for its own selection, and on the manual
        # page that is a page change where a step in Y was meant.
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stack = QStackedWidget()

        # Row height in Python rather than in the stylesheet. qdarktheme
        # installs a QProxyStyle and item geometry is computed through it, so
        # `padding` and `min-height` on QListWidget#nav::item were painted and
        # not measured: the selected row's rounded rect ran over its
        # neighbours. Derived from the font so it still follows theme.SPACING
        # and the font size rather than being a number picked to look right.
        row = QSize(0, self.nav.fontMetrics().height() + SPACING * 2)

        self.session = Session(options, parent=self)

        self.pages: dict[str, QWidget] = {}
        for name, title, page_class in PAGES:
            page = page_class(self.session)
            self.pages[name] = page
            setattr(self, f"{name}_page", page)
            self.nav.addItem(title)
            self.nav.item(self.nav.count() - 1).setSizeHint(row)
            self.stack.addWidget(page)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.status = StatusBar()
        self.setStatusBar(self.status)

        self.session.profile_changed.connect(
            lambda profile: self.status.show_profile(
                profile.name if profile is not None else None))
        self.session.robot_state_changed.connect(self.status.show_robot)
        self.session.camera_opened.connect(self._show_cameras)
        self.session.camera_closed.connect(self._show_cameras)
        self.session.tip_changed.connect(self._show_tip)
        self.session.lights_changed.connect(self.status.show_lights)
        self.status.lights.clicked.connect(self._toggle_lights)
        self._lights_worker: Worker | None = None
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

    def _show_cameras(self, _label: str) -> None:
        self.status.show_cameras(self.session.open_cameras)

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
        """Bring a page to the front by name, keeping the list in step."""
        self.nav.setCurrentRow(list(self.pages).index(name))
