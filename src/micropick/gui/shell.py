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

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QStackedWidget, QStatusBar, QWidget)

from .pages import calibration, log, manual, picking, profile
from .theme import SPACING

if TYPE_CHECKING:                       # app imports this module; annotations
    from .app import Options            # are strings, so the cycle is only a
                                        # type-checker's problem

__all__ = ["MainWindow", "StatusBar", "PAGES"]

# (attribute name, title in the list, page class). The attribute is how the
# rest of the application reaches a page; the title is what is on screen.
PAGES = (
    ("profile", profile.TITLE, profile.ProfilePage),
    ("manual", manual.TITLE, manual.ManualPage),
    ("calibration", calibration.TITLE, calibration.CalibrationPage),
    ("picking", picking.TITLE, picking.PickingPage),
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

        self.pages: dict[str, QWidget] = {}
        for name, title, page_class in PAGES:
            page = page_class()
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
        # Static until the session owns them. The robot field is the one the
        # command line already decides, so it says so rather than lying about
        # a bench that is not attached.
        self.status.show_profile(options.profile)
        self.status.show_robot("mock, not connected" if options.mock
                               else "not connected")

    def show_page(self, name: str) -> None:
        """Bring a page to the front by name, keeping the list in step."""
        self.nav.setCurrentRow(list(self.pages).index(name))
