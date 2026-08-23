"""The one thing that owns the robot, the profile and the cameras.

`bench_setup.py` for the GUI: it holds what a session is attached to, and it is
the only module besides `gui/workers` allowed to touch `opentrons_api`. Pages
ask it for things and listen to its signals; nothing else opens a device or
posts a command, so `--mock` is one branch here rather than a condition on
every page.

Every method on it is **synchronous and blocking**, like everything below it —
DESIGN section 2. Connecting the robot is an HTTP round trip and opening a
camera is a device open plus a warm-up, and both would freeze the window if
called directly; that is the caller's problem to solve with a `Worker`, not a
reason to make this asynchronous. Kept this way, the session can be exercised
with no event loop at all.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Before anything that resolves a path. `paths` reads MICROPICK_ROOT at call
# time rather than at import, so the order is not delicate, but this is where
# the notebooks do it and there is no reason for the GUI to differ.
#
# Only when the layout says we are in a checkout: from an installed wheel the
# same arithmetic points at site-packages, and pointing the profiles root there
# is the kind of silent wrong answer that is worse than the documented default
# of the working directory. setdefault, so an explicit override still wins.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if (_REPO_ROOT / "src" / "micropick").is_dir():
    os.environ.setdefault("MICROPICK_ROOT", str(_REPO_ROOT))

from PySide6.QtCore import QObject, Signal          # noqa: E402

from .. import paths                                # noqa: E402
from ..config import store                          # noqa: E402
from ..config.schema import (Calibration, CameraSpec,  # noqa: E402
                             PickingConfig, ProfileMeta)
from ..hardware import labware                      # noqa: E402
from ..hardware.camera import CameraManager         # noqa: E402
from ..hardware.mock import (MarkerScene, MockRobot,  # noqa: E402
                             open_scene_camera)
from ..workflows.jog import Limits                  # noqa: E402

__all__ = ["Session", "SessionError", "MOCK_PROFILE_NAME"]

log = logging.getLogger(__name__)

MOCK_PROFILE_NAME = "mock"

# Robot states, as the status bar shows them.
DISCONNECTED = "not connected"
CONNECTED = "connected"
MOCK = "mock"


class SessionError(RuntimeError):
    """Something was asked of the session that it cannot do yet."""


def mock_profile_dir() -> Path:
    """Under outputs/, where nothing mistakes it for an installation."""
    return paths.outputs_dir() / "mock_profile"


def _mock_cameras() -> dict[str, CameraSpec]:
    """Two cameras shaped like the real pair, including the lower one's crop.

    The crop matters even on mocks: it is a property of the view and not of the
    sensor, and a camera view that forgets it is how the whole coordinate
    confusion in DESIGN section 5 started.
    """
    return {
        "over": CameraSpec(device_name="mock-over",
                           default_resolution=[2592, 1944],
                           resolutions=[[2592, 1944]], crop=1.0,
                           notes="synthetic ArUco scene"),
        "under": CameraSpec(device_name="mock-under",
                            default_resolution=[2000, 1500],
                            resolutions=[[2000, 1500]], crop=0.5,
                            notes="synthetic ArUco scene"),
    }


class Session(QObject):
    profile_changed = Signal(object)
    robot_state_changed = Signal(str)
    camera_opened = Signal(str)
    camera_closed = Signal(str)
    error = Signal(str)

    def __init__(self, options, parent: QObject | None = None):
        super().__init__(parent)
        self.options = options
        self.profile: store.Profile | None = None
        self.robot = None
        self.cameras: CameraManager | None = None
        self._open: dict[str, object] = {}

    # -- state ---------------------------------------------------------------

    @property
    def mock(self) -> bool:
        return bool(self.options.mock)

    @property
    def robot_state(self) -> str:
        if self.robot is None:
            return DISCONNECTED
        return MOCK if self.mock else CONNECTED

    @property
    def open_cameras(self) -> list[str]:
        return sorted(self._open)

    @property
    def jog_limits(self) -> Limits:
        # The profile schema has no limits section. When it grows one, this is
        # the only place that changes; the manual page asks the session and
        # knows nothing about where the numbers came from.
        return Limits(x=(0.0, 380.0), y=(0.0, 350.0), z=(0.1, 150.0))

    def uses_mock_profile(self) -> bool:
        return self.profile is not None and self.profile.path == mock_profile_dir()

    # -- profile -------------------------------------------------------------

    def load_profile(self, name: str) -> store.Profile:
        """Blocking, but only on disk. Raises ProfileError as it comes."""
        if name == MOCK_PROFILE_NAME and self.mock:
            profile = self._load_mock_profile()
        else:
            profile = store.load_profile(name)
        self.profile = profile
        # A manager per profile: the labels and the device names it resolves
        # belong to the installation, not to the application.
        self.cameras = CameraManager.from_profile(profile)
        log.info("profile %r loaded from %s", profile.name, profile.path)
        self.profile_changed.emit(profile)
        return profile

    def _load_mock_profile(self) -> store.Profile:
        """A real profile on disk, created once and kept.

        Deliberately persistent rather than built fresh each run. Half of what
        "remember this position" has to do is `save_positions` — an atomic
        write — and the other half is reading it back on the next start. A
        profile rebuilt at startup exercises neither, and those are the halves
        where `store` is able to go wrong. Deleting the directory costs
        nothing: it comes back on the next launch, empty.
        """
        directory = mock_profile_dir()
        if (directory / store.META_FILE).is_file():
            return store.load_profile(MOCK_PROFILE_NAME, directory=directory)

        profile = store.Profile(
            meta=ProfileMeta(name=MOCK_PROFILE_NAME,
                             notes="scratch profile for --mock; safe to delete"),
            calibration=Calibration(), picking=PickingConfig(),
            cameras=_mock_cameras(), positions={}, path=directory)
        directory.mkdir(parents=True, exist_ok=True)
        profile.save()
        log.info("created the mock profile at %s", directory)
        return profile

    def remember(self, name: str, position) -> None:
        if self.profile is None:
            raise SessionError("no profile is loaded, so there is nowhere to "
                               "remember a position")
        self.profile.remember(name, position)
        log.info("remembered %r at %s in profile %r", name,
                 tuple(round(float(v), 2) for v in position), self.profile.name)
        self.profile_changed.emit(self.profile)

    # -- robot ---------------------------------------------------------------

    def connect_robot(self):
        """Bring up a robot ready to move. Blocking: run it in a Worker.

        Nothing that moves the robot works before `create_run()` and
        `load_pipette()` — not even `get_position` — and custom definitions
        have to be uploaded into every run, after each `create_run`. That order
        is DESIGN section 9 and it is the whole content of this method.
        """
        if self.robot is not None:
            return self.robot

        if self.mock:
            # No create_run and no load_pipette: MockRobot has neither, and
            # calling them would be pretending the mock is an HTTP client.
            robot = MockRobot()
            from ..hardware.protocols import xyz
            log.info("mock robot at %s",
                     tuple(round(v, 1) for v in xyz(robot)))
        else:
            from opentrons_api import ot2_api

            log.info("connecting to the robot")
            api = ot2_api.OpentronsAPI()
            api.create_run()
            log.info("run %s created", api.run_id)
            uploaded = labware.ensure_definitions(api, verbose=False)
            log.info("uploaded %d custom definitions: %s",
                     len(uploaded), ", ".join(sorted(uploaded)) or "none")
            api.load_pipette()
            log.info("pipette %s loaded", api.PIPETTE)
            robot = api

        self.robot = robot
        self.robot_state_changed.emit(self.robot_state)
        return robot

    def disconnect_robot(self) -> None:
        """Let go of the robot. Does not home it and does not end the run.

        A run left open is resumable; a run ended by closing a window is not,
        and the operator did not ask for that.
        """
        if self.robot is None:
            return
        self._retract()
        self.robot = None
        log.info("released the robot")
        self.robot_state_changed.emit(self.robot_state)

    def _retract(self) -> None:
        """Park the left Z before letting go, and never raise doing it."""
        try:
            self.robot.retract_axis("leftZ", verbose=False)
        except Exception as exc:                     # noqa: BLE001
            log.warning("could not retract leftZ: %s", exc)

    # -- cameras -------------------------------------------------------------

    def open_camera(self, label: str):
        """Open one of the profile's cameras. Blocking: run it in a Worker."""
        if self.profile is None:
            raise SessionError("load a profile before opening a camera")
        existing = self._open.get(label)
        if existing is not None:
            return existing

        if self.mock:
            camera = self._open_mock_camera(label)
        else:
            if self.cameras is None:
                raise SessionError("no camera manager; load a profile first")
            camera = self.cameras.open(label, verbose=False)

        self._open[label] = camera
        log.info("camera %r open at %dx%d%s", label, *camera.resolution,
                 f", view crop {camera.crop:g}" if camera.crop != 1.0 else "")
        self.camera_opened.emit(label)
        return camera

    def _open_mock_camera(self, label: str):
        """A synthetic ArUco scene rendered from the mock robot's own pose.

        Needs the robot, and says so rather than quietly handing back a static
        picture: the scene follows the gantry, which is the only reason it is
        worth anything for a calibration sweep or for jogging.
        """
        if self.robot is None:
            raise SessionError(
                f"connect the robot before opening {label!r}: the synthetic "
                f"scene is rendered from the gantry pose")
        spec = self.profile.cameras.get(label)
        if spec is None:
            raise SessionError(
                f"no camera labelled {label!r} in profile {self.profile.name!r}; "
                f"known: {', '.join(sorted(self.profile.cameras)) or 'none'}")
        width, height = spec.default_resolution
        scene = MarkerScene(image_size=(width, height))
        camera = open_scene_camera(self.robot, scene, label=label)
        # crop is a view property carried by the camera, applied where a person
        # looks and nowhere else. The mock keeps the same contract.
        camera.crop = float(spec.crop)
        return camera

    def close_camera(self, label: str) -> None:
        camera = self._open.pop(label, None)
        if camera is None:
            return
        if self.mock or self.cameras is None:
            camera.close()
        else:
            self.cameras.close(label)
        log.info("camera %r closed", label)
        self.camera_closed.emit(label)

    def camera(self, label: str):
        """The open camera with this label, or None. Never opens one."""
        return self._open.get(label)

    # -- teardown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Leave nothing running. Called from the window's closeEvent.

        Cameras first: each holds a grab thread, and a thread still calling
        read() while the interpreter tears down is the one way this exits
        badly. Then the axis is parked and the robot is let go.
        """
        for label in list(self._open):
            try:
                self.close_camera(label)
            except Exception as exc:                 # noqa: BLE001
                log.warning("could not close camera %r: %s", label, exc)
        if self.robot is not None:
            self._retract()
            self.robot = None
            self.robot_state_changed.emit(self.robot_state)
        log.info("session closed")
