"""The window everything else sits in.

A list on the left and a stack on the right, assembled by hand rather than from
a structural framework: the navigation is five fixed entries and a framework
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

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QStackedWidget, QStatusBar, QWidget)

from . import log_bridge
from .pages import calibration, log, manual, picking, profile, routine
from .session import MOCK_PROFILE_NAME, Session
from .theme import SPACING

if TYPE_CHECKING:                       # app imports this module; annotations
    from .app import Options            # are strings, so the cycle is only a
                                        # type-checker's problem

__all__ = ["MainWindow", "StatusBar", "PAGES"]

_log = logging.getLogger(__name__)

# (attribute name, title in the list, page class). The attribute is how the
# rest of the application reaches a page; the title is what is on screen.
PAGES = (
    ("profile", profile.TITLE, profile.ProfilePage),
    ("manual", manual.TITLE, manual.ManualPage),
    ("calibration", calibration.TITLE, calibration.CalibrationPage),
    ("picking", picking.TITLE, picking.PickingPage),
    ("routine", routine.TITLE, routine.RoutinePage),
    ("log", log.TITLE, log.LogPage),
)

NAV_WIDTH = 200


class StatusBar(QStatusBar):
    """Profile, robot and cameras, permanently visible.

    Permanent widgets rather than `showMessage`: a transient message is
    replaced by the next one, and these three answer "what is this application
    currently attached to", which is never a transient question.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._profile = QLabel()
        self._robot = QLabel()
        self._cameras = QLabel()
        for label in (self._profile, self._robot, self._cameras):
            self.addPermanentWidget(label)
        self.show_profile(None)
        self.show_robot("not connected")
        self.show_cameras([])

    def show_profile(self, name: str | None) -> None:
        self._profile.setText(f"profile: {name or 'none'}")

    def show_robot(self, state: str) -> None:
        self._robot.setText(f"robot: {state}")

    def show_cameras(self, labels: list[str]) -> None:
        self._cameras.setText(f"cameras: {', '.join(labels) or 'none'}")


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

    def show_page(self, name: str) -> None:
        """Bring a page to the front by name, keeping the list in step."""
        self.nav.setCurrentRow(list(self.pages).index(name))
