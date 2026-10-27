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

While the chosen camera opens, a spinner and "Opening camera..." stand
over the picture until its first frame. A camera whose device took a
focus at open - the profile's or a device only previewed - then shows the
focus under the picture: a slider, a typed number and Default (960),
tried on the device live. Done writes it into the profile
(`Session.save_camera_focus`); × saves nothing and puts back the focus
the profile's open camera had.

One preview opens at a time: a choice made while one opens is opened
after it, and Done waits for the picture of what it saves. Done is gray
while anything has the robot: a run reads the cameras.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                               QSlider, QVBoxLayout, QWidget)

from ..session import OTHER_ROLE, Session
from ..tasks import SETUP_TITLES
from ...hardware.camera import DEFAULT_FOCUS
from ..theme.factory import (heading, muted_label, secondary_button, select,
                             spin_box)
from ..widgets.camera_view import FOCUS_RANGE, FOCUS_SETTLE_MS, CameraView
from ..widgets.overlays import SheetWindow
from ..workers import Worker, describe_error

__all__ = ["CameraSheet"]

log = logging.getLogger(__name__)

PANEL_WIDTH = 320
NOT_ATTACHED = "{} (not attached)"
HINT = ("The picture on the left is live. Pick the camera whose picture "
        "matches.")
OPENING = "Opening camera…"


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
        left = QVBoxLayout()
        left.setSpacing(8)
        self.view = CameraView(self.body_widget)
        self.view.frame_shown.connect(self._first_frame)
        left.addWidget(self.view, 1)
        self._make_opening()
        self._make_focus_row()
        left.addWidget(self.focus_row)
        row.addLayout(left, 1)
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

    def _make_opening(self) -> None:
        """The spinner and "Opening camera..." over the picture's middle."""
        self.opening = QFrame(self.view)
        self.opening.setObjectName("opening")
        self.opening.setStyleSheet(
            "QFrame#opening { background: rgb(24,24,26); }"
            "QLabel { color: rgb(200,200,206); }")
        box = QVBoxLayout(self.opening)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        spinner = QProgressBar(self.opening)
        spinner.setRange(0, 0)                   # turning, no amount
        spinner.setTextVisible(False)
        spinner.setFixedSize(160, 6)
        box.addWidget(spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        self.opening_label = QLabel(OPENING, self.opening)
        self.opening_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.opening_label)
        self.opening.hide()
        self._centerer = _Centerer(self.opening, self.view)

    def _make_focus_row(self) -> None:
        """Focus under the picture: slider, number, Default."""
        self.focus_row = QWidget(self.body_widget)
        line = QHBoxLayout(self.focus_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(QLabel("Focus", self.focus_row))
        self.focus_slider = QSlider(Qt.Orientation.Horizontal, self.focus_row)
        self.focus_slider.setRange(*FOCUS_RANGE)
        self.focus_slider.setPageStep(10)
        self.focus_slider.valueChanged.connect(self._focus_moved)
        line.addWidget(self.focus_slider, 1)
        self.focus_number = spin_box(self.focus_row)
        self.focus_number.setRange(*FOCUS_RANGE)
        self.focus_number.setKeyboardTracking(False)
        self.focus_number.setToolTip("Focus value; Enter applies.")
        self.focus_number.valueChanged.connect(self.focus_slider.setValue)
        line.addWidget(self.focus_number)
        self.focus_default = secondary_button(f"Default ({DEFAULT_FOCUS})",
                                              self.focus_row)
        self.focus_default.setToolTip(f"Set the focus to {DEFAULT_FOCUS}.")
        self.focus_default.clicked.connect(
            lambda: self.focus_slider.setValue(DEFAULT_FOCUS))
        line.addWidget(self.focus_default)
        self.focus_row.hide()
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(FOCUS_SETTLE_MS)
        self._focus_timer.timeout.connect(self._apply_focus)
        # The camera the row tunes, the focus it had when shown, and
        # whether the operator moved it since.
        self._focus_camera = None
        self._focus_was: int | None = None
        self._focus_touched = False

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

    # -- focus ---------------------------------------------------------

    def _first_frame(self) -> None:
        """The picture is up: the opening goes, the focus comes."""
        camera = self.view.camera
        if camera is None or self._focus_camera is camera:
            return
        self.opening.hide()
        self._focus_camera = camera
        controls = getattr(camera, "controls", None)
        applied = getattr(controls, "applied", {}) or {}
        if "focus" not in applied or not hasattr(camera, "set_controls"):
            return                               # looked at: no focus
        current = camera.get_control("focus")
        value = int(round(current if current is not None
                          else applied["focus"]))
        value = max(FOCUS_RANGE[0], min(FOCUS_RANGE[1], value))
        self._focus_was, self._focus_touched = value, False
        self._show_focus_value(value)
        self.focus_row.show()

    def _show_focus_value(self, value: int) -> None:
        for widget in (self.focus_slider, self.focus_number):
            widget.blockSignals(True)
            widget.setValue(int(value))
            widget.blockSignals(False)

    def _focus_moved(self, value: int) -> None:
        """Coalesced: a drag gives dozens of values a second, the device
        takes one control transfer at a time."""
        self._show_focus_value(value)
        self._focus_touched = True
        self._focus_timer.start()

    def _apply_focus(self) -> None:
        self._focus_timer.stop()
        camera = self._focus_camera
        if camera is None or not self._focus_touched:
            return
        try:
            camera.set_controls({"focus": self.focus_slider.value()})
        except Exception as exc:                     # noqa: BLE001
            log.error("focus not set: %s", exc)

    def _drop_focus(self, put_back: bool = True) -> None:
        """The row goes; a focus tried on the profile's open camera is put
        back as it was, a preview's goes with the preview."""
        self._focus_timer.stop()
        camera = self._focus_camera
        if (put_back and camera is not None and self._focus_touched
                and self._focus_was is not None
                and self._profile_label(camera) is not None):
            try:
                camera.set_controls({"focus": self._focus_was})
            except Exception as exc:                 # noqa: BLE001
                log.error("focus not put back: %s", exc)
        self._focus_camera = None
        self._focus_was, self._focus_touched = None, False
        self.focus_row.hide()

    def _profile_label(self, camera) -> str | None:
        """The profile's label of `camera`, or None for a preview."""
        label = getattr(camera, "label", None)
        if camera is None or self.session.camera(label) is not camera:
            return None
        return label

    def _wanted(self, worker: Worker) -> bool:
        """The worker opened what is chosen now, for this role."""
        return (worker.device == self.select.currentData()
                and worker.role == self.role)

    def _chosen(self) -> None:
        """Show the device chosen: open it, unless one is opening."""
        name = self.select.currentData()
        self._opening = name is not None
        self._drop_focus()
        self._shown = None
        self.view.set_camera(None)
        self.opening.setVisible(name is not None)
        self._centerer.place()
        self._show_done()
        if name is None:
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
        self.opening.hide()
        self.state.setText(reason)
        self._show_done()

    # -- closing -------------------------------------------------------

    def _let_go(self, put_back: bool = True) -> None:
        """The picture off, and the camera opened only for it closed; a
        profile's camera stays open for the pages, its focus as saved."""
        self._drop_focus(put_back)
        self.opening.hide()
        self.view.set_camera(None)
        self.session.close_preview()

    def _save(self) -> None:
        name = self._shown
        focus = None
        if self._focus_camera is not None and self._focus_touched:
            self._apply_focus()
            focus = self.focus_slider.value()
        self._let_go(put_back=False)
        if name is None:
            return
        try:
            swapped = self.session.choose_camera(self.role, name)
            if focus is not None:
                self.session.save_camera_focus(self.role, focus)
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


class _Centerer(QObject):
    """Keeps `widget` in the middle of `host` as the host is resized."""

    def __init__(self, widget: QWidget, host: QWidget):
        super().__init__(host)
        self.widget, self.host = widget, host
        host.installEventFilter(self)

    def place(self) -> None:
        size = self.widget.sizeHint()
        self.widget.setGeometry((self.host.width() - size.width()) // 2,
                                (self.host.height() - size.height()) // 2,
                                size.width(), size.height())
        self.widget.raise_()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.place()
        return False
