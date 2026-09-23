"""Choosing a profile, connecting the robot, opening cameras.

Everything here goes through the session. The page knows which buttons exist
and what state they should be in; it does not know whether the robot behind
them is an HTTP client or a mock, and it never opens a device itself.

The slow calls — probing the robot, bringing a run up, opening a camera — run
in a `Worker`. An HTTP round trip and a camera warm-up are both seconds, and in
the GUI thread each of them is a frozen window.

Bringing the robot up is a decision, not a button. The robot may already hold a
run from before this application started, and if it was never powered off that
run is the one to carry on with — its pipette, its labware, its offsets. Or it
may hold nothing, or a finished run. The session can find out which; it cannot
know whether yesterday's run is stale or today's work in progress, so what it
found is shown here and the operator chooses. A new run always ends in a home,
because nothing moves until it has, and that is not a thing to leave to memory.

Profiles are made and removed here too. A new one starts as a copy of an
existing one by default, because the cameras, the calibration and the taught
positions belong to the bench and a profile started empty would have to
measure all of them again. Deleting always asks, and never takes the profile
that is loaded: its cameras are open and its positions are being written.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QVBoxLayout, QWidget)

from ... import paths
from ...config.store import (LegacyProfileError, ProfileError, copy_profile,
                             create_profile, delete_profile, list_profiles,
                             profile_dir)
from ..detector import STANDIN, DetectorService
from ..session import MOCK_PROFILE_NAME, Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             secondary_button)
from ..workers import Worker

__all__ = ["ProfilePage"]

TITLE = "Profile"

log = logging.getLogger(__name__)

# Two columns of cards: one card to a row spent a 1400 px window on a combo
# box and two buttons.
COLUMNS = 2

# What "start from" offers besides the existing profiles.
EMPTY = "empty (defaults)"


def _calibration_state(profile) -> str:
    pixel_map = profile.pixel_map
    if pixel_map is None:
        return "not calibrated — run the camera sweep"
    parts = [f"pixel map: degree {pixel_map.degree}"]
    if pixel_map.n_poses is not None:
        parts.append(f"{pixel_map.n_poses} poses")
    if pixel_map.holdout_mean_um is not None:
        parts.append(f"held-out {pixel_map.holdout_mean_um:.1f} µm mean")
    if profile.calibration.pipette_offset is None:
        parts.append("no pipette offset")
    return ", ".join(parts)


class _CameraRow(QWidget):
    """One camera of the profile: what it is, and one button to open or close."""

    def __init__(self, page: "ProfilePage", label: str, spec, parent=None):
        super().__init__(parent)
        self.label = label
        self.page = page

        width, height = spec.default_resolution
        text = f"{label} — {width}×{height}"
        if spec.crop != 1.0:
            text += f", view crop {spec.crop:g}"

        self.name = QLabel(text)
        self.button = secondary_button("Open", self)
        self.button.clicked.connect(self._toggle)
        # Controls tuned on a feed - the lower camera's focus slider - live
        # on the device until this writes them into cameras.json. A separate
        # act on purpose: trying a focus is not deciding on it.
        self.save_button = secondary_button("Save controls", self)
        self.save_button.setToolTip(
            "Write this camera's current control values (focus, exposure…) "
            "into the profile, so the next open starts from them.")
        self.save_button.clicked.connect(self._save_controls)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.save_button)
        row.addWidget(self.button)

    def _toggle(self) -> None:
        if self.page.session.camera(self.label) is None:
            self.page.open_camera(self.label)
        else:
            self.page.close_camera(self.label)

    def _save_controls(self) -> None:
        try:
            written = self.page.session.save_camera_controls(self.label)
        except Exception as exc:                     # noqa: BLE001
            self.page._show_error(str(exc))
            return
        self.page._show_error(
            f"{self.label}: saved " + ", ".join(f"{k}={v:g}" for k, v in written.items())
            if written else
            f"{self.label}: nothing to save - the profile names no numeric "
            f"control for this camera that the device took.")

    def refresh(self, busy: bool) -> None:
        camera = self.page.session.camera(self.label)
        is_open = camera is not None
        self.button.setText("Close" if is_open else "Open")
        # Closing is instant and safe while something else is opening; opening
        # is not, so only that half waits.
        self.button.setEnabled(is_open or not busy)
        applied = getattr(getattr(camera, "controls", None), "applied", {}) or {}
        self.save_button.setVisible(is_open and bool(applied))


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
        self._camera_rows: list[_CameraRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))

        grid = QGridLayout()
        grid.setSpacing(SPACING)
        cards = (self._profile_card(), self._robot_card(),
                 self._cameras_card(), self._models_card())
        for i, box in enumerate(cards):
            grid.addWidget(box, i // COLUMNS, i % COLUMNS,
                           Qt.AlignmentFlag.AlignTop)
        for column in range(COLUMNS):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        # Full width, selectable, and never truncated: LegacyProfileError's own
        # text says what to do about it, so it is shown as it comes rather than
        # summarised into a line that does not.
        self.error = QLabel()
        self.error.setWordWrap(True)
        # Selectable, so a path or a key name in the message can be copied out
        # rather than retyped from the screen.
        self.error.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addStretch(1)

        session.profile_changed.connect(self._on_profile_changed)
        session.robot_state_changed.connect(lambda _s: self.refresh())
        session.camera_opened.connect(lambda _l: self.refresh())
        session.camera_closed.connect(lambda _l: self.refresh())

        self.reload_profile_list()
        self._reload_models()
        self.refresh()

    # -- construction --------------------------------------------------------

    def _profile_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Installation", 2))

        self.chooser = QComboBox(self)
        self.load_button = primary_button("Load", self)
        self.load_button.clicked.connect(self.load_selected)
        row = QHBoxLayout()
        row.addWidget(self.chooser, 1)
        row.addWidget(self.load_button)
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

        self.profile_path = QLabel()
        self.profile_path.setWordWrap(True)
        self.calibration = QLabel()
        self.calibration.setWordWrap(True)
        box.layout().addWidget(self.profile_path)
        box.layout().addWidget(self.calibration)
        return box

    def _robot_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Robot", 2))
        self.robot_state = QLabel()
        box.layout().addWidget(self.robot_state)

        self.connect_button = primary_button("Connect", self)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button = secondary_button("Disconnect", self)
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
        self.adopt_button = secondary_button("Continue with this run", self)
        self.adopt_button.clicked.connect(self._adopt)
        self.new_run_button = secondary_button("New run + home", self)
        self.new_run_button.clicked.connect(self._new_run)
        choice = QHBoxLayout()
        choice.addWidget(self.adopt_button)
        choice.addWidget(self.new_run_button)
        choice.addStretch(1)
        box.layout().addLayout(choice)
        return box

    def _cameras_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Cameras", 2))
        self.no_cameras = QLabel("No profile loaded.")
        self.no_cameras.setWordWrap(True)
        box.layout().addWidget(self.no_cameras)
        self._cameras_box = box
        return box

    def _models_card(self) -> QWidget:
        """Which weights this installation uses, chosen once.

        Here rather than on the pages that run them: an installation has one
        cuboid detector and one tip detector, the choice is a property of
        the bench and not of a run, and a page that offered it would be
        asking the same question every time it was opened. The pages load
        whatever is named here when they need it.
        """
        box = card(self)
        box.layout().addWidget(heading("Models", 2))

        self.cuboid_model = combo_box(self)
        self.cuboid_model.currentTextChanged.connect(self._cuboid_model_chosen)
        self.tip_model = combo_box(self)
        self.tip_model.currentTextChanged.connect(self._tip_model_chosen)
        for label, widget in (("Cuboids", self.cuboid_model),
                              ("Pipette tip", self.tip_model)):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(90)
            row.addWidget(name)
            row.addWidget(widget, 1)
            box.layout().addLayout(row)

        self.models_note = QLabel()
        self.models_note.setWordWrap(True)
        box.layout().addWidget(self.models_note)
        return box

    # -- actions -------------------------------------------------------------

    def reload_profile_list(self) -> None:
        self.chooser.clear()
        names = list_profiles()
        if self.session.mock:
            # First, because in mock mode it is the one that works.
            names = [MOCK_PROFILE_NAME] + [n for n in names
                                           if n != MOCK_PROFILE_NAME]
        self.chooser.addItems(names)
        if self.session.profile is not None:
            index = self.chooser.findText(self.session.profile.name)
            if index >= 0:
                self.chooser.setCurrentIndex(index)

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
                  "carrying on with the robot's current run")

    def _new_run(self) -> None:
        """Ends in a home, so it asks: the gantry travels to its limits on
        every axis, and a hand in the deck is the failure this dialog is for."""
        state = self.session.run_state
        detail = ("The robot will create a new run, load the pipette and then "
                  "home: the gantry moves to its limits on all three axes. "
                  "Keep hands and labware clear.")
        if state is not None and state.reusable:
            detail += (f"\n\nThe current run {state.run_id} will be left "
                       f"behind, with its labware and offsets.")
        answer = QMessageBox.question(
            self, "New run and home", detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        # `==`, not `is`: PySide6 hands the answer back as a plain int on
        # some builds, and an identity test against the enum member is then
        # always false - the button did nothing and logged nothing.
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._clear_error()
        self._run(Worker(self.session.new_run), "new run, then home")

    def open_camera(self, label: str) -> None:
        self._clear_error()
        self._run(Worker(self.session.open_camera, label),
                  f"opening camera {label!r}")

    def close_camera(self, label: str) -> None:
        try:
            self.session.close_camera(label)
        except Exception as exc:                     # noqa: BLE001
            self._show_error(str(exc))

    def _run(self, worker: Worker, what: str) -> None:
        if self._worker is not None and self._worker.running:
            return
        self._worker = worker
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

    def _reload_models(self) -> None:
        """What is in ml_models/, plus the stand-in for the cuboid detector.

        The tip detector has no stand-in here: `gui.tip_detector` makes one
        for --mock, and offering it on the bench would be offering to
        calibrate a pipette against invented crosshairs.
        """
        found = DetectorService.available_weights()
        profile = self.session.profile
        cuboid = profile.picking.model_file if profile else ""
        tip = profile.calibration.tip_target.model_file if profile else ""

        for widget, current, extra in ((self.cuboid_model, cuboid, [STANDIN]),
                                       (self.tip_model, tip, [])):
            names = [*found, *extra]
            # A name in the profile that is not in ml_models/ is shown
            # anyway, and marked: a profile that points at weights this
            # machine does not have is a fact worth seeing, not a silently
            # reset setting.
            if current and current not in names:
                names.insert(0, current)
            widget.blockSignals(True)
            widget.clear()
            widget.addItems(names)
            index = widget.findText(current)
            widget.setCurrentIndex(index if index >= 0 else -1)
            widget.blockSignals(False)

        missing = [name for name in (cuboid, tip)
                   if name and name != STANDIN and name not in found]
        self.models_note.setText(
            "The pages that need a model load whichever is named here."
            if not missing else
            f"Not in {paths.ml_models_dir()}: {', '.join(missing)}. "
            f"Weights are not tracked in the repository; copy the file in, "
            f"or choose another.")

    def _cuboid_model_chosen(self, name: str) -> None:
        profile = self.session.profile
        if profile is None or not name or profile.picking.model_file == name:
            return
        profile.picking.model_file = name
        profile.save_picking()
        log.info("cuboid detector for %r: %s", profile.name, name)
        self.session.profile_changed.emit(profile)

    def _tip_model_chosen(self, name: str) -> None:
        profile = self.session.profile
        if profile is None or not name:
            return
        target = profile.calibration.tip_target
        if target.model_file == name:
            return
        target.model_file = name
        # backup=False: choosing a model is not a measurement, and archiving
        # the calibration on every combo change would bury the sweeps that
        # are worth keeping.
        profile.save_calibration(backup=False)
        log.info("tip detector for %r: %s", profile.name, name)
        self.session.profile_changed.emit(profile)

    def _on_profile_changed(self, profile) -> None:
        self.reload_profile_list()
        self._rebuild_camera_rows()
        self._reload_models()
        self.refresh()

    def _rebuild_camera_rows(self) -> None:
        layout = self._cameras_box.layout()
        for row in self._camera_rows:
            layout.removeWidget(row)
            row.deleteLater()
        self._camera_rows = []

        profile = self.session.profile
        if profile is None or not profile.cameras:
            self.no_cameras.setText(
                "No profile loaded." if profile is None else
                f"Profile {profile.name!r} lists no cameras.")
            self.no_cameras.show()
            return

        self.no_cameras.hide()
        for label in sorted(profile.cameras):
            row = _CameraRow(self, label, profile.cameras[label], self)
            layout.addWidget(row)
            self._camera_rows.append(row)

    def refresh(self) -> None:
        busy = self._worker is not None and self._worker.running
        session = self.session
        profile = session.profile

        if profile is None:
            self.profile_path.setText("No profile loaded.")
            self.calibration.setText("")
        else:
            temporary = ("  (temporary: created by --mock, safe to delete)"
                         if session.uses_mock_profile() else "")
            self.profile_path.setText(f"{profile.path}{temporary}")
            self.calibration.setText(_calibration_state(profile))

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
        self.connect_button.setEnabled(session.run_state is None and not busy)
        self.disconnect_button.setEnabled(
            session.run_state is not None and not busy)

        state = session.run_state
        self.run_found.setVisible(probed)
        self.adopt_button.setVisible(probed)
        self.new_run_button.setVisible(probed)
        if probed:
            self.run_found.setText(
                "Found: " + state.describe() + ("" if state.reusable else
                 "\nOnly a new run is possible."))
            self.adopt_button.setEnabled(state.reusable and not busy)
            self.new_run_button.setEnabled(not busy)
            # Carrying on is the expected case when the robot was left on, so
            # it is the accented button; when it is not possible, the only
            # way forward takes the accent instead.
            self.adopt_button.setDefault(state.reusable)
            self.new_run_button.setDefault(not state.reusable)

        for row in self._camera_rows:
            row.refresh(busy)

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _clear_error(self) -> None:
        self.error.clear()
        self.error.hide()
