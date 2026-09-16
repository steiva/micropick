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

import json
import logging
import os
import time
from dataclasses import dataclass, field
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
from ..hardware.labware import LoadedLabware        # noqa: E402
from ..hardware.mock import (MarkerScene, MockRobot,  # noqa: E402
                             open_scene_camera)
from ..workflows.jog import Limits                  # noqa: E402

__all__ = ["Session", "SessionError", "RunState", "MOCK_PROFILE_NAME",
           "REUSABLE_STATUSES"]

log = logging.getLogger(__name__)

MOCK_PROFILE_NAME = "mock"

# Robot states, as the status bar shows them.
DISCONNECTED = "not connected"
PROBED = "connected, no run chosen"
CONNECTED = "connected"
MOCK = "mock"

# A run in one of these can still take setup commands, so it can be carried
# on with. Anything else - stopped, failed, succeeded - is a record of a run,
# not a run, and the only thing to do with it is start a new one.
REUSABLE_STATUSES = ("idle", "running", "paused")


@dataclass
class RunState:
    """What the robot said when asked what it is doing.

    Read-only: probing adopts nothing and moves nothing. The decision of what to
    do with it belongs to the operator, because the one fact that decides it -
    whether the robot was left running on purpose or is stale from yesterday -
    is not on the robot.
    """

    run_id: str | None = None
    status: str | None = None
    has_pipette: bool = False
    labware: dict[str, LoadedLabware] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.run_id is not None

    @property
    def reusable(self) -> bool:
        return self.exists and self.status in REUSABLE_STATUSES

    def describe(self) -> str:
        if not self.exists:
            return "no current run on the robot"
        slots = ", ".join(f"slot {s}: {lw.load_name}"
                          for s, lw in sorted(self.labware.items())) or "no labware"
        pipette = "pipette loaded" if self.has_pipette else "no pipette"
        return (f"run {self.run_id}, status {self.status}, {pipette}; {slots}"
                + ("" if self.reusable else
                   f" - a {self.status} run cannot take commands"))


def _run_state(api) -> RunState:
    """The current run from `get_all_runs`, which both the wrapper and the
    mock answer in the same shape. Nothing is adopted here."""
    data = json.loads(api.get_all_runs().text)["data"]
    current = next((run for run in data if run.get("current")), None)
    if current is None:
        return RunState()
    return RunState(run_id=current.get("id"), status=current.get("status"),
                    has_pipette=bool(current.get("pipettes")),
                    labware=labware.loaded_labware(api))


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
        # `_api` is the connection; `robot` is the same object once it has a
        # run and a pipette and can be told to move. Pages that move things
        # look at `robot`, so a probed-but-undecided connection cannot be
        # driven by accident.
        self._api = None
        self.robot = None
        self.run_state: RunState | None = None
        self.run_origin: str | None = None       # "reused" or "new"
        self.cameras: CameraManager | None = None
        self._open: dict[str, object] = {}

    # -- state ---------------------------------------------------------------

    @property
    def mock(self) -> bool:
        return bool(self.options.mock)

    @property
    def robot_state(self) -> str:
        if self.robot is None:
            return PROBED if self._api is not None else DISCONNECTED
        base = MOCK if self.mock else CONNECTED
        run = self.run_state.run_id if self.run_state else None
        if run and self.run_origin:
            return f"{base}, run {run} ({self.run_origin})"
        return base

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
    #
    # Bringing the robot up is three blocking steps with a decision in the
    # middle, not one call. The robot may already hold a run - it was in use
    # before this application started, and if it was never powered off that run
    # is the one to carry on with - or it may hold none, or a finished one. Which
    # of those is the case is on the robot; what to do about it is not, so the
    # session probes, reports, and waits to be told.

    def probe_robot(self) -> RunState:
        """Connect and ask what the robot is doing. Blocking; moves nothing.

        Constructing the wrapper costs no network; `get_all_runs` is the one
        request. The result is kept on the session and nothing is adopted: the
        wrapper's own `get_run_info` would set run_id and endpoints as a side
        effect of looking, and looking must not commit to anything.
        """
        if self._api is None:
            if self.mock:
                self._api = MockRobot()
            else:
                from opentrons_api import ot2_api
                self._api = ot2_api.OpentronsAPI()
        state = _run_state(self._api)
        self.run_state = state
        log.info("robot probed: %s", state.describe())
        self.robot_state_changed.emit(self.robot_state)
        return state

    def adopt_run(self):
        """Carry on with the run the robot already has. Blocking; moves nothing.

        `get_run_info` is what adopts it - run_id, pipette_id, the labware map
        and the endpoints - so it is called here and only here. Definitions are
        re-uploaded because uploads are per run and idempotent; a pipette is
        loaded only if the run has none.
        """
        state = self._require_probe()
        if not state.reusable:
            raise SessionError(
                f"the robot's current run cannot be carried on with: "
                f"{state.describe()}. Start a new run instead.")

        if not self.mock:
            self._api.get_run_info()
            uploaded = labware.ensure_definitions(self._api, verbose=False)
            log.info("uploaded %d custom definitions into run %s",
                     len(uploaded), state.run_id)
            if not state.has_pipette:
                self._api.load_pipette()
                log.info("pipette %s loaded into run %s",
                         self._api.PIPETTE, state.run_id)
        self._ready("reused")
        return self.robot

    def new_run(self):
        """A fresh run, and then home. Blocking; **moves the gantry**.

        Homing is part of this and not a separate button because nothing moves
        after a new run until the robot has been homed, and a run created
        without it is a robot that refuses every command with no visible reason.
        The four steps are one method so that state cannot exist.

        create_run is timed and logged: on a robot left powered on it has been
        seen to take longer with each run created, and a number in the log is
        how that stops being an impression.
        """
        self._require_probe()
        if self.mock:
            self._api.home_robot()
            self.run_state = _run_state(self._api)
            log.info("mock homed")
        else:
            started = time.monotonic()
            self._api.create_run(verbose=False)
            log.info("run %s created in %.1f s", self._api.run_id,
                     time.monotonic() - started)
            uploaded = labware.ensure_definitions(self._api, verbose=False)
            log.info("uploaded %d custom definitions: %s",
                     len(uploaded), ", ".join(sorted(uploaded)) or "none")
            self._api.load_pipette(verbose=False)
            log.info("pipette %s loaded", self._api.PIPETTE)
            log.info("homing")
            self._api.home_robot(verbose=False)
            log.info("homed")
            self.run_state = _run_state(self._api)
        self._ready("new")
        return self.robot

    def refresh_run_state(self) -> RunState:
        """Re-read what the run holds, after labware was loaded or moved."""
        if self._api is None:
            raise SessionError("not connected")
        self.run_state = _run_state(self._api)
        return self.run_state

    def _require_probe(self) -> RunState:
        if self._api is None or self.run_state is None:
            raise SessionError("probe the robot first")
        return self.run_state

    def _ready(self, origin: str) -> None:
        self.robot = self._api
        self.run_origin = origin
        self.robot_state_changed.emit(self.robot_state)

    def connect_robot(self):
        """The whole sequence with the default decision made: carry on with a
        reusable run, otherwise start one. For callers with no operator to ask,
        such as a test; the profile page asks."""
        state = self.probe_robot()
        return self.adopt_run() if state.reusable else self.new_run()

    def disconnect_robot(self) -> None:
        """Let go of the robot. Does not home it and does not end the run.

        A run left open is resumable; a run ended by closing a window is not,
        and the operator did not ask for that.
        """
        if self._api is None:
            return
        if self.robot is not None:
            self._retract()
        self._api = self.robot = None
        self.run_state = self.run_origin = None
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
        self._api = self.robot = None
        self.run_state = self.run_origin = None
        self.robot_state_changed.emit(self.robot_state)
        log.info("session closed")
