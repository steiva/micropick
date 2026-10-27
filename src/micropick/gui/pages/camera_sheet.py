"""Robot setup's camera sheets (REDESIGN §5, #9): which attached camera is
the upper camera, and which the lower.

A sheet over whatever is shown, opened from the camera's row (or a step
that needs it): the live picture on the left, "Which camera is the upper
camera?" and a Select of the attached cameras on the right. The picture
follows the Select before anything is saved - choosing by looking is the
point - through `Session.preview_camera`, which hands back the profile's
camera when it is on that device and opens any other on its own, so no
device is opened twice. Done saves through `Session.choose_camera` (the
other role's device swapped in, a camera added to a profile without
one); Cancel and × save nothing. `said(text)` is the answer, for the
window's toast.

One preview opens at a time: a choice made while one opens is opened
after it, and Done waits for the picture of what it saves. Done is gray
while anything has the robot: a run reads the cameras.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..session import OTHER_ROLE, Session
from ..tasks import SETUP_TITLES
from ..theme.factory import heading, muted_label, select
from ..widgets.camera_view import CameraView
from ..widgets.overlays import SheetWindow
from ..workers import Worker, describe_error

__all__ = ["CameraSheet"]

log = logging.getLogger(__name__)

PANEL_WIDTH = 320
NOT_ATTACHED = "{} (not attached)"
HINT = ("The picture on the left is live. Pick the camera whose picture "
        "matches.")


class CameraSheet(SheetWindow):
    """`open_for(role)`, "upper" or "lower"; `said(text)` after Done."""

    said = Signal(str)

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, "")
        self.session = session
        self.role = "upper"
        self._worker: Worker | None = None
        # The device the picture is of, once open; None while none is.
        self._shown: str | None = None
        self._opening = False

        row = QHBoxLayout()
        row.setSpacing(24)
        self.view = CameraView(self.body_widget)
        row.addWidget(self.view, 1)
        panel = QVBoxLayout()
        panel.setSpacing(8)
        self.question = heading("", 3, self.body_widget)
        self.question.setWordWrap(True)
        panel.addWidget(self.question)
        self.select = select(self.body_widget)
        self.select.currentIndexChanged.connect(lambda _i: self._chosen())
        panel.addWidget(self.select)
        hint = muted_label(HINT, self.body_widget)
        hint.setWordWrap(True)
        panel.addWidget(hint)
        self.state = muted_label("", self.body_widget)
        self.state.setWordWrap(True)
        panel.addWidget(self.state)
        panel.addStretch(1)
        holder = QWidget(self.body_widget)
        holder.setLayout(panel)
        holder.setFixedWidth(PANEL_WIDTH)
        row.addWidget(holder)
        self.body.addLayout(row, 1)

        self.done.connect(self._save)
        self.canceled.connect(self._let_go)
        session.robot_busy_changed.connect(lambda _h: self._show_done())

    # -- opening -------------------------------------------------------

    def open_for(self, role: str) -> None:
        """The sheet for the `role` camera, on the device it is saved on;
        a role without one starts on the first attached camera the other
        role is not on."""
        self.role = role
        self.title_label.setText(SETUP_TITLES[f"{role}_camera"])
        self.question.setText(f"Which camera is the {role} camera?")
        self.state.setText("")
        try:
            names = self.session.attached_cameras()
        except Exception as exc:                     # noqa: BLE001
            names = []
            self.state.setText(describe_error(exc))
        saved = self.session.camera_device(role, names)
        other = self.session.camera_device(OTHER_ROLE[role], names)
        self.select.blockSignals(True)
        self.select.clear()
        for name in names:
            self.select.addItem(name, name)
        if saved is not None and saved not in names:
            self.select.insertItem(0, NOT_ATTACHED.format(saved), None)
        current = (saved if saved is not None else
                   next((n for n in names if n != other),
                        names[0] if names else None))
        index = self.select.findData(current)
        self.select.setCurrentIndex(max(index, 0))
        self.select.blockSignals(False)
        if not names:
            self.state.setText(self.state.text() or "No camera is attached.")
        self.open()
        self._chosen()

    def _show_done(self) -> None:
        """Done once the picture of the choice is up and the robot free."""
        lease = self.session.robot_lease
        self.done_button.setEnabled(self._shown is not None
                                    and not self._opening and not lease.busy)
        self.done_button.setToolTip(
            f"Robot busy: {lease.holder}." if lease.busy else "")

    def _wanted(self, worker: Worker) -> bool:
        """The worker opened what is chosen now, for this role."""
        return (worker.device == self.select.currentData()
                and worker.role == self.role)

    def _chosen(self) -> None:
        """Show the device chosen: open it, unless one is opening."""
        name = self.select.currentData()
        self._opening = name is not None
        self._show_done()
        if name is None:
            self._shown = None
            self.view.set_camera(None)
            self.session.close_preview()
            return
        if self._worker is not None and self._worker.running:
            return                       # `_opened` opens the newer choice
        worker = Worker(self.session.preview_camera, self.role, name,
                        what=f"opening camera {name!r}")
        worker.device, worker.role = name, self.role
        worker.finished.connect(self._opened)
        worker.failed.connect(self._failed)
        self._worker = worker
        worker.start()

    def _opened(self, camera) -> None:
        worker = self.sender()
        if self.isHidden():
            self.session.close_preview()
            return
        if not self._wanted(worker):
            self._chosen()
            return
        self._shown, self._opening = worker.device, False
        self.view.set_camera(camera)
        self.state.setText("")
        self._show_done()

    def _failed(self, reason: str) -> None:
        worker = self.sender()
        log.error("could not open camera %r: %s", worker.device, reason)
        if self.isHidden():
            return
        if not self._wanted(worker):
            self._chosen()
            return
        self._shown, self._opening = None, False
        self.view.set_camera(None)
        self.state.setText(reason)
        self._show_done()

    # -- closing -------------------------------------------------------

    def _let_go(self) -> None:
        """The picture off, and the camera opened only for it closed; a
        profile's camera stays open for the pages."""
        self.view.set_camera(None)
        self.session.close_preview()

    def _save(self) -> None:
        name = self._shown
        self._let_go()
        if name is None:
            return
        try:
            swapped = self.session.choose_camera(self.role, name)
        except Exception as exc:                     # noqa: BLE001
            log.error("camera not chosen: %s", exc)
            self.said.emit(describe_error(exc))
            return
        title = SETUP_TITLES[f"{self.role}_camera"]
        other = SETUP_TITLES[f"{OTHER_ROLE[self.role]}_camera"]
        self.said.emit(f"{title}: {name}."
                       + (f" {other}: {swapped}." if swapped else ""))

    def discard(self) -> None:
        """Closed by going elsewhere: as ×, nothing is saved."""
        if not self.isHidden():
            self.hide()
            self._let_go()
