"""Connecting the robot and choosing a profile.

Two cards, centred: Robot first, because it is the first thing to do and the
one that needs reading, then Installation. Under them, across both, what is
still to do before a picking run (`gui.readiness`), so loading a profile
shows at once how far the bench is from ready. What a profile holds beyond its
name - which camera is which, the models, the calibration - is set in
Settings (the gear), not here: this page is the start of a day's work, and
those are set once.

Everything here goes through the session. The page knows which buttons exist
and what state they should be in; it does not know whether the robot behind
them is an HTTP client or a mock, and it never opens a device itself.

The slow calls - probing the robot, bringing a run up - run in a `Worker`.
An HTTP round trip is seconds, and in the GUI thread it is a frozen window.

Bringing the robot up is a decision, not a button. The robot may already hold a
run from before this application started, and if it was never powered off that
run is the one to carry on with - its pipette, its labware, its offsets. Or it
may hold nothing, or a finished run. The session can find out which; it cannot
know whether yesterday's run is stale or today's work in progress, so what it
found is shown here and the operator chooses. A new run always ends in a home,
because nothing moves until it has, and that is not a thing to leave to memory.

Profiles are made and removed here too. A new one starts as a copy of an
existing one by default, because the cameras, the calibration and the taught
positions belong to the bench and a profile started empty would have to
measure all of them again. Deleting always asks, and never takes the profile
that is loaded: its cameras are open and its positions are being written. The
chooser shows each profile's folder after its name, quieter; a green check
under it says which one is loaded.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import qtawesome as qta
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QStyle,
                               QStyledItemDelegate, QStyleOptionComboBox,
                               QStyleOptionViewItem, QStylePainter,
                               QVBoxLayout, QWidget)

from ...config import robot_sessions
from ...config.store import (LegacyProfileError, ProfileError, copy_profile,
                             create_profile, delete_profile, list_profiles,
                             profile_dir)
from ..readiness import readiness_checks
from ..session import MOCK_PROFILE_NAME, Session
from ..theme import SPACING
from ..theme.factory import card, heading, primary_button, secondary_button
from ..widgets.checklist import Checklist
from ..widgets.done_banner import DONE_GREEN
from ..workers import Worker

__all__ = ["ProfilePage"]

TITLE = "Profile"

log = logging.getLogger(__name__)

# Each card's width, which it grows past only for a button that would
# not fit; the two sit side by side, centred.
CARD_WIDTH = 560

# What "start from" offers besides the existing profiles.
EMPTY = "empty (defaults)"

# The folder after a profile's name: how much of the text's ink it gets, and
# the gap before it.
PATH_ALPHA = 0.5
PATH_GAP = 12
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
ICON_PX = 18


def _draw_name_and_path(painter: QPainter, rect: QRect, name: str,
                        path: str | None, ink: QColor) -> None:
    """The name in `ink`, then the path in a fainter `ink`, elided in the
    middle so both its ends - the drive and the profile's folder - stay."""
    painter.save()
    metrics = painter.fontMetrics()
    flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
    painter.setPen(ink)
    name = metrics.elidedText(name, Qt.TextElideMode.ElideRight, rect.width())
    painter.drawText(rect, flags, name)
    left = rect.left() + metrics.horizontalAdvance(name) + PATH_GAP
    room = rect.right() - left
    if path and room > 3 * metrics.averageCharWidth():
        faint = QColor(ink)
        faint.setAlphaF(ink.alphaF() * PATH_ALPHA)
        painter.setPen(faint)
        painter.drawText(QRect(left, rect.top(), room, rect.height()), flags,
                         metrics.elidedText(path, Qt.TextElideMode.ElideMiddle,
                                            room))
    painter.restore()


class _PathDelegate(QStyledItemDelegate):
    """The chooser's list: each profile's name, then its folder, fainter."""

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name, opt.text = opt.text, ""
        widget = opt.widget
        style = widget.style() if widget is not None else None
        if style is None:
            super().paint(painter, option, index)
            return
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter,
                          widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText,
                                    opt, widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        ink = opt.palette.color(QPalette.ColorRole.HighlightedText if selected
                                else QPalette.ColorRole.Text)
        _draw_name_and_path(painter, rect.adjusted(4, 0, -4, 0), name,
                            index.data(PATH_ROLE), ink)


class _ProfileChooser(QComboBox):
    """A combo box of profile names that shows each one's folder after it.

    The folder is item data (`PATH_ROLE`), drawn by hand: a combo box has
    one text colour, and the point is two."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setItemDelegate(_PathDelegate(self))

    def wheelEvent(self, event) -> None:
        # The page does not scroll, but a wheel over the chooser changing
        # which profile Load would load is a surprise all the same.
        event.ignore()

    def paintEvent(self, _event) -> None:
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        name, opt.currentText = opt.currentText, ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt,
            QStyle.SubControl.SC_ComboBoxEditField, self)
        ink = self.palette().color(
            QPalette.ColorGroup.Normal if self.isEnabled()
            else QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        _draw_name_and_path(painter, rect.adjusted(2, 0, 0, 0), name,
                            self.currentData(PATH_ROLE), ink)


class NewProfileDialog(QDialog):
    """A name, and what the new profile starts as."""

    def __init__(self, sources: list[str], current: str | None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("New profile")
        self.name = QLineEdit(self)
        self.name.setPlaceholderText("e.g. lab_main_96well")
        self.source = QComboBox(self)
        self.source.addItems([*sources, EMPTY])
        if current in sources:
            self.source.setCurrentIndex(sources.index(current))
        self.problem = QLabel(self)
        self.problem.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Start from", self.source)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel, self)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(buttons)
        self.name.textChanged.connect(self._check)
        self._check()
        self.resize(420, self.sizeHint().height())

    def _check(self) -> None:
        """Say what is wrong with the name while it is typed, not after."""
        name = self.name.text().strip()
        problem = ""
        if name:
            try:
                if profile_dir(name).exists():
                    problem = f"{name!r} already exists."
            except ProfileError as exc:
                problem = str(exc)
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
        self.ok.setEnabled(bool(name) and not problem)

    @property
    def chosen_name(self) -> str:
        return self.name.text().strip()

    @property
    def chosen_source(self) -> str | None:
        source = self.source.currentText()
        return None if source == EMPTY else source


class ProfilePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None

        # Robot and Installation side by side, the checklist across both
        # under them; the three as one centred column.
        cards = QHBoxLayout()
        cards.setSpacing(SPACING * 2)
        for box in (self._robot_card(), self._profile_card()):
            box.setMinimumWidth(CARD_WIDTH)
            box.setMaximumWidth(CARD_WIDTH * 3 // 2)
            cards.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(SPACING * 2)
        column.addLayout(cards)
        column.addWidget(self._checks_card())
        centred = QHBoxLayout()
        centred.addStretch(1)
        centred.addLayout(column)
        centred.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 4, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(centred)

        # Full width, selectable, and never truncated: LegacyProfileError's own
        # text says what to do about it, so it is shown as it comes rather than
        # summarised into a line that does not.
        self.error = QLabel()
        self.error.setWordWrap(True)
        # Selectable, so a path or a key name in the message can be copied out
        # rather than retyped from the screen.
        self.error.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.setMaximumWidth(2 * CARD_WIDTH + SPACING * 2)
        self.error.hide()
        layout.addWidget(self.error, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        session.profile_changed.connect(self._on_profile_changed)
        session.robot_state_changed.connect(lambda _s: self.refresh())
        # What the checklist reads, beyond the profile and the robot.
        for signal in (session.routine_changed, session.tip_changed,
                       session.labware_changed, session.settings_changed):
            signal.connect(lambda _x: self._show_checks())

        self.reload_profile_list()
        self.refresh()

    # -- construction --------------------------------------------------------

    def _checks_card(self) -> QWidget:
        """Everything before a picking run (`gui.readiness`), each with the
        page where it is done: loading a profile is when the day's work
        starts, so this is where what is still missing is said."""
        box = card(self)
        box.layout().addWidget(heading("Before a picking run", 2))
        self.checks_state = QLabel(self)
        self.checks_state.setWordWrap(True)
        box.layout().addWidget(self.checks_state)
        self.checklist = Checklist(self)
        self.checklist.go.connect(self.session.page_requested)
        box.layout().addWidget(self.checklist)
        return box

    def _profile_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Installation", 2))

        self.chooser = _ProfileChooser(self)
        self.load_button = primary_button("Load", self)
        self.load_button.clicked.connect(self.load_selected)
        row = QHBoxLayout()
        row.addWidget(self.chooser, 1)
        row.addWidget(self.load_button)
        box.layout().addLayout(row)

        # Which profile is loaded: a green check and its name, so a glance
        # tells whether Load still has to be pressed.
        self.loaded_icon = QLabel(self)
        self.loaded_icon.setPixmap(qta.icon("mdi6.check-circle",
                                            color=DONE_GREEN)
                                   .pixmap(ICON_PX, ICON_PX))
        self.loaded = QLabel(self)
        self.loaded.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.loaded_icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.loaded, 1)
        box.layout().addLayout(row)

        self.new_button = secondary_button("New profile…", self)
        self.new_button.clicked.connect(self._new_profile)
        self.delete_button = secondary_button("Delete…", self)
        self.delete_button.clicked.connect(self._delete_profile)
        self.chooser.currentTextChanged.connect(lambda _t: self.refresh())
        row = QHBoxLayout()
        row.addWidget(self.new_button)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        return box

    def _robot_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Robot", 2))
        self.robot_state = QLabel()
        box.layout().addWidget(self.robot_state)

        # The first thing to do, said before the button that needs it: a
        # Connect pressed while the robot is still starting up fails with a
        # network error that says nothing about why.
        self.robot_hint = QLabel(
            "1. Turn the robot on and wait for it to start up - about a "
            "minute, until the light on its front button stops blinking and "
            "stays on.\n"
            "2. Press Connect. It only asks the robot what it is doing; "
            "nothing moves yet.")
        self.robot_hint.setWordWrap(True)
        box.layout().addWidget(self.robot_hint)

        self.connect_button = primary_button("Connect", self)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button = secondary_button("Disconnect robot session",
                                                  self)
        self.disconnect_button.clicked.connect(self.session.disconnect_robot)
        row = QHBoxLayout()
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        # What the probe found, and the two ways on from it. A block on the
        # page rather than a dialog: the text has to be read, and a dialog's
        # default button is the thing that gets pressed without reading.
        self.run_found = QLabel()
        self.run_found.setWordWrap(True)
        self.run_found.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.run_found)

        # Both secondary; setDefault below picks which one carries the accent.
        # New first, on the left: it is the way on that always exists.
        self.new_run_button = secondary_button("New robot session", self)
        self.new_run_button.setToolTip("Start a new robot session. The robot "
                                       "homes its axes first; it asks before.")
        self.new_run_button.clicked.connect(self._new_run)
        self.adopt_button = secondary_button(
            "Continue with current robot session", self)
        self.adopt_button.clicked.connect(self._adopt)
        choice = QHBoxLayout()
        choice.addWidget(self.new_run_button)
        choice.addWidget(self.adopt_button)
        choice.addStretch(1)
        box.layout().addLayout(choice)
        return box

    # -- actions -------------------------------------------------------------

    def _profile_path(self, name: str) -> str:
        """Where the profile `name` is on disk, for the chooser."""
        profile = self.session.profile
        if profile is not None and profile.name == name:
            return str(profile.path)
        if name == MOCK_PROFILE_NAME and self.session.mock:
            return ""
        try:
            return str(profile_dir(name))
        except ProfileError:
            return ""

    def reload_profile_list(self) -> None:
        self.chooser.blockSignals(True)
        self.chooser.clear()
        names = list_profiles()
        if self.session.mock:
            # First, because in mock mode it is the one that works.
            names = [MOCK_PROFILE_NAME] + [n for n in names
                                           if n != MOCK_PROFILE_NAME]
        for name in names:
            self.chooser.addItem(name)
            self.chooser.setItemData(self.chooser.count() - 1,
                                     self._profile_path(name), PATH_ROLE)
        if self.session.profile is not None:
            index = self.chooser.findText(self.session.profile.name)
            if index >= 0:
                self.chooser.setCurrentIndex(index)
        self.chooser.blockSignals(False)
        self.chooser.update()

    def load_selected(self) -> None:
        """Load whatever the chooser is showing. Also the start-up path."""
        name = self.chooser.currentText()
        if not name:
            self._show_error("There are no profiles under profiles/.")
            return
        self._clear_error()
        try:
            self.session.load_profile(name)
        except LegacyProfileError as exc:
            # Shown in full: this message is the instructions.
            log.error("%s", exc)
            self._show_error(str(exc))
        except Exception as exc:                     # noqa: BLE001
            log.error("could not load profile %r: %s", name, exc)
            self._show_error(str(exc))

    def _new_profile(self) -> None:
        loaded = self.session.profile.name if self.session.profile else None
        dialog = NewProfileDialog(list_profiles(), loaded, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, source = dialog.chosen_name, dialog.chosen_source
        self._clear_error()
        try:
            if source is None:
                create_profile(name)
            else:
                copy_profile(source, name)
        except Exception as exc:                     # noqa: BLE001
            log.error("could not create profile %r: %s", name, exc)
            self._show_error(str(exc))
            return
        log.info("created profile %r%s", name,
                 f" from {source!r}" if source else " with defaults")
        self.reload_profile_list()
        self.chooser.setCurrentIndex(self.chooser.findText(name))
        self.load_selected()

    def _deletable(self, name: str) -> bool:
        """Not the loaded one, and not the mock's scratch profile, which is
        not under profiles/ and comes back on the next launch anyway."""
        if not name or (name == MOCK_PROFILE_NAME and self.session.mock):
            return False
        profile = self.session.profile
        return profile is None or profile.name != name

    def _delete_profile(self) -> None:
        name = self.chooser.currentText()
        if not self._deletable(name):
            return
        path = profile_dir(name)
        answer = QMessageBox.warning(
            self, "Delete profile",
            f"Delete the profile {name!r}?\n\n"
            f"Everything in {path} goes with it: calibration and its "
            f"history, picking settings, cameras, positions and deck. "
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._clear_error()
        try:
            delete_profile(name)
        except Exception as exc:                     # noqa: BLE001
            log.error("could not delete profile %r: %s", name, exc)
            self._show_error(str(exc))
            return
        log.info("deleted profile %r (%s)", name, path)
        self.reload_profile_list()
        self.refresh()

    def _connect(self) -> None:
        """Probe only. What happens next is the operator's, below."""
        self._clear_error()
        self._run(Worker(self.session.probe_robot), "probing the robot")

    def _adopt(self) -> None:
        self._clear_error()
        self._run(Worker(self.session.adopt_run),
                  "continuing the robot session")

    def _new_run(self) -> None:
        """Ends in a home, so it asks: the gantry travels to its limits on
        every axis, and a hand in the deck is the failure this dialog is for."""
        state = self.session.run_state
        detail = ("The robot will home its axes. Stand clear and keep your "
                  "hands out of the robot.")
        if state is not None and state.reusable:
            detail += (f"\n\nThe current robot session {state.label} will "
                       f"be left behind, with its labware and offsets.")
        # The name is asked here, in the same question: it is what the
        # session is shown as from now on (`config.robot_sessions`).
        profile = self.session.profile
        dialog = QInputDialog(self)
        dialog.setWindowTitle("New robot session")
        dialog.setLabelText(detail + "\n\nName of the new session:")
        dialog.setTextValue(robot_sessions.default_name(
            profile.name if profile is not None else None))
        dialog.setOkButtonText("Start")
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return
        name = dialog.textValue().strip() or None
        self._clear_error()
        self._run(Worker(self.session.new_run, name),
                  "new robot session, then home")

    def _run(self, worker: Worker, what: str) -> None:
        if self._worker is not None and self._worker.running:
            return
        self._worker = worker
        worker.what = worker.what or what
        # Bound methods, not lambdas. Qt takes a connection's thread from the
        # receiver object, and a lambda has none: it would be connected
        # directly and would repaint this page from the worker's thread.
        worker.message.connect(self._worker_said)
        worker.finished.connect(self._worker_done)
        worker.failed.connect(self._worker_failed)
        log.info("%s", what)
        worker.start()
        self.refresh()

    def _worker_said(self, text: str) -> None:
        log.info("%s", text)

    def _worker_done(self, _result=None) -> None:
        self._worker = None
        self.refresh()

    def _worker_failed(self, reason: str) -> None:
        self._worker = None
        self._show_error(reason)
        self.refresh()

    # -- display -------------------------------------------------------------

    def _on_profile_changed(self, profile) -> None:
        self.reload_profile_list()
        self.refresh()

    def refresh(self) -> None:
        busy = self._worker is not None and self._worker.running
        session = self.session
        profile = session.profile

        self.loaded_icon.setVisible(profile is not None)
        self.loaded.setVisible(profile is not None)
        if profile is not None:
            temporary = ("<br>Temporary: created by --mock, safe to delete."
                         if session.uses_mock_profile() else "")
            self.loaded.setText(f"Profile <b>{profile.name}</b> is loaded."
                                f"{temporary}")

        self.load_button.setEnabled(not busy and self.chooser.count() > 0)
        self.chooser.setEnabled(not busy)
        self.new_button.setEnabled(not busy)
        chosen = self.chooser.currentText()
        self.delete_button.setEnabled(not busy and self._deletable(chosen))
        self.delete_button.setToolTip(
            "Load another profile first: this one is in use."
            if profile is not None and chosen == profile.name else
            f"Delete {chosen!r}, after asking." if chosen else "")

        connected = session.robot is not None
        probed = session.run_state is not None and not connected
        self.robot_state.setText(f"State: {session.robot_state}")
        self.robot_hint.setVisible(session.run_state is None)
        self.connect_button.setEnabled(session.run_state is None and not busy)
        self.disconnect_button.setEnabled(
            session.run_state is not None and not busy)

        # The two ways on are always there, greyed until Connect has found
        # what the robot holds and again once one of them was taken: the
        # operator sees from the start what comes after Connect.
        state = session.run_state
        self.run_found.setVisible(probed)
        if probed:
            self.run_found.setText(
                "Found: " + state.describe() + ("" if state.reusable else
                 "\nOnly a new robot session is possible."))
        self.adopt_button.setEnabled(probed and state.reusable and not busy)
        self.new_run_button.setEnabled(probed and not busy)
        # Carrying on is the expected case when the robot was left on, so
        # it is the accented button; when it is not possible, the only way
        # forward takes the accent instead. Neither while both are grey.
        self.adopt_button.setDefault(probed and state.reusable)
        self.new_run_button.setDefault(probed and not state.reusable)

        self._show_checks()

    def showEvent(self, event) -> None:
        """Again on arrival: a position or a calibration set on another
        page changes the list without a signal of its own."""
        super().showEvent(event)
        self._show_checks()

    def _show_checks(self) -> None:
        # What is done on this page is pointed at, not linked to.
        checks = [replace(check, page=None,
                          text=check.text.replace("on the Profile page",
                                                  "above"))
                  if check.page == "profile" else check
                  for check in readiness_checks(self.session)]
        todo = sum(check.blocking for check in checks)
        self.checks_state.setText(
            f"{todo} thing{'s' if todo != 1 else ''} to do, marked red; the "
            f"button beside each goes to the page where it is done."
            if todo else "Ready for a picking run.")
        self.checklist.show_checks(checks)

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _clear_error(self) -> None:
        self.error.clear()
        self.error.hide()
