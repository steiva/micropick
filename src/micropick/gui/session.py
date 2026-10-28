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
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from ..config import app_settings, robot_sessions, store            # noqa: E402
from ..config import templates                       # noqa: E402
from ..config.labware import (LabwareDefinition,       # noqa: E402
                              LabwareError, resolve_definition)
from ..config.schema import (CALIBRATION_MODULE,  # noqa: E402
                             Calibration, CameraSpec,
                             DeckConfig, DeckModule, ModuleType,
                             PickingConfig, WellCenter,
                             ProfileMeta)
from ..hardware import devices as camera_devices   # noqa: E402
from ..hardware import labware                      # noqa: E402
from ..hardware import robot_clock                  # noqa: E402
from ..hardware.camera import (DEFAULT_FOCUS,         # noqa: E402
                               CameraManager)
from ..hardware.labware import LoadedLabware        # noqa: E402
from ..hardware.mock import (MarkerScene, MockRobot,  # noqa: E402
                             open_scene_camera)
from ..hardware import tips                          # noqa: E402
from ..hardware.protocols import (lights_on, require_ok,  # noqa: E402
                                  set_lights)
from ..hardware.robot_lease import RobotLease       # noqa: E402
from ..workflows import manual as moves             # noqa: E402
from ..workflows.motion import Motion               # noqa: E402
from .detector import DetectorService              # noqa: E402
from .tasks import SETUP_POSITIONS                  # noqa: E402

__all__ = ["Session", "SessionError", "RunState", "Tip", "DeckProblem",
           "MemoPoint", "MEASURED", "SKIPPED", "NOT_MEASURED",
           "DEFAULT_PROFILE_NAME", "MOCK_PROFILE_NAME", "REUSABLE_STATUSES"]

log = logging.getLogger(__name__)

MOCK_PROFILE_NAME = "mock"
# The label of a camera opened to be looked at before it is chosen
# (`Session.preview_camera`); never a profile's.
PREVIEW_LABEL = "preview"
# The camera roles, each to the other.
OTHER_ROLE = {"upper": "lower", "lower": "upper"}
# The profile made at the first start, when there is none to load.
DEFAULT_PROFILE_NAME = "default"

# A plate's well center (`Session.well_center_status`): measured, its
# drawing used on purpose for this robot session, or neither yet.
MEASURED, SKIPPED, NOT_MEASURED = "measured", "skipped", "not measured"

# Labware that holds no destination. A tip rack has wells and an ordering
# like a plate, and delivering a cuboid into one is a mistake nothing
# downstream would catch.
NOT_A_DESTINATION = ("tipRack", "trash", "adapter")

# Robot states, as the status bar shows them. The OT-2's HTTP "run" - the
# thing that holds the loaded labware and offsets - is a "robot session" on
# screen: "run" reads as a picking run to everyone at the bench.
DISCONNECTED = "not connected"
PROBED = "connected, no robot session chosen"
CONNECTED = "connected"
MOCK = "mock"

# A run in one of these can still take setup commands, so it can be carried
# on with. Anything else - stopped, failed, succeeded - is a record of a run,
# not a run, and the only thing to do with it is start a new one.
REUSABLE_STATUSES = ("idle", "running", "paused")

# How long the connection monitor waits for the robot's answer.
LOOK_TIMEOUT_S = 3.0


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
    # The robot's `createdAt`, and the name this computer gave the session
    # when it created it (`config.robot_sessions`), if it did.
    created_at: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        """The session as shown: its name, else when the robot started it,
        else the id. The id is 36 characters that say nothing; the name says
        how old a session left on the robot is."""
        if self.name:
            return self.name
        created = robot_sessions.created_label(self.created_at)
        if created:
            return f"started {created}"
        return str(self.run_id)

    @property
    def exists(self) -> bool:
        return self.run_id is not None

    @property
    def reusable(self) -> bool:
        return self.exists and self.status in REUSABLE_STATUSES

    def describe(self) -> str:
        if not self.exists:
            return "no robot session on the robot"
        slots = ", ".join(f"slot {s}: {lw.load_name}"
                          for s, lw in sorted(self.labware.items())) or "no labware"
        # The pipette is left out: whether the run has one is set up under
        # the hood, and "pipette loaded" reads as "a tip is on".
        return (f"robot session {self.label}, status {self.status}; {slots}"
                + ("" if self.reusable else
                   f" - a {self.status} session cannot take commands"))


def _run_state(api) -> RunState:
    """The current run from `get_all_runs`, which both the wrapper and the
    mock answer in the same shape. Nothing is adopted here."""
    data = json.loads(api.get_all_runs().text)["data"]
    current = next((run for run in data if run.get("current")), None)
    if current is None:
        return RunState()
    run_id = current.get("id")
    return RunState(run_id=run_id, status=current.get("status"),
                    has_pipette=bool(current.get("pipettes")),
                    labware=labware.loaded_labware(api),
                    created_at=current.get("createdAt"),
                    name=robot_sessions.name_of(run_id))


@dataclass(frozen=True)
class Tip:
    """The tip on the pipette, as the robot's command log has it.

    `attached` None means the log could not be read, and the difference
    between "no tip" and "do not know" is the whole point: nothing that moves
    the gantry should treat the second as the first. `slot` and `well` are
    where the tip came from when the log said and that rack is still in a
    slot; a tip from labware since moved off the deck has neither. `pickup`
    is the robot's id of the pickUpTip command that put it on, which a tip
    calibration records (`PipetteOffset.tip_pickup`): it names this tip in
    this robot session, and no other.
    """

    attached: bool | None
    slot: str | None = None
    well: str | None = None
    pickup: str | None = None
    length: float | None = None              # below the nozzle, mm

    @classmethod
    def unknown(cls) -> "Tip":
        return cls(attached=None)

    @property
    def returnable(self) -> bool:
        return bool(self.attached) and self.slot is not None and self.well is not None

    def describe(self) -> str:
        if self.attached is None:
            return "tip: unknown"
        if not self.attached:
            return "no tip"
        where = (f" from slot {self.slot} {self.well}"
                 if self.returnable else "")
        return "TIP ON" + where


@dataclass(frozen=True)
class DeckProblem:
    """Labware the run holds in a module slot without the module's offset.

    The one deck mistake that ends in a crash: the robot believes the plate
    sits on the slot floor, 64 mm below where it is, and drives the tip
    into the module on the first well move. It is found by comparing the
    profile's modules with the offsets the run reports on each labware, and
    fixed by loading the labware again - the offset attaches at load.
    """

    slot: str
    load_name: str
    expected: tuple[float, float, float]
    applied: tuple[float, float, float] | None

    def describe(self) -> str:
        have = ("no offset" if self.applied is None else
                f"offset ({self.applied[0]:+g}, {self.applied[1]:+g}, "
                f"{self.applied[2]:+g})")
        return (f"slot {self.slot}: {self.load_name} was loaded with {have}, "
                f"but the profile puts a module of +{self.expected[2]:g} mm "
                f"there. A well move will hit the module. Load it again.")


@dataclass
class MemoPoint:
    """A point marked on Manual control's picture, for this start only.

    Where the tip goes, in the robot's frame - a position's coordinates, so
    it is drawn as a position is. `z` None: over it at the travel height,
    not down to anything, until a height is given.
    """

    name: str
    x: float
    y: float
    z: float | None = None


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
                            # A motorised focus like the real module's, so
                            # the focus slider exists here too. The scene
                            # ignores it; the control round-trips.
                            controls={"autofocus": 0, "focus": 500},
                            notes="synthetic ArUco scene"),
    }


def _mock_camera(device_name: str) -> CameraSpec:
    """The mock camera on the device `device_name`."""
    return next(spec for spec in _mock_cameras().values()
                if spec.device_name == device_name)


# A new profile's cameras, as the bench's are set up (profiles/lab_main):
# role -> (label, preferred mode, crop, controls, capture backend, notes).
# The upper camera opens at 2592x1944, the mode the pixel maps are fitted
# at; the lower one at 4000x3000 with the Arducam's motorised focus through
# DirectShow, the backend that reads its focus back (`CameraSpec.backend`).
NEW_CAMERAS = {
    "upper": ("overview_cam", (2592, 1944), 1.0,
              {"auto_exposure": "manual"}, None, "on the gantry"),
    "lower": ("underview_cam", (4000, 3000), 0.5,
              {"autofocus": 0.0, "focus": float(DEFAULT_FOCUS),
               "auto_exposure": "manual"},
              "dshow" if sys.platform == "win32" else None,
              "tip calibration module"),
}


def new_camera_spec(role: str, device_name: str,
                    sizes: list[tuple[int, int]]) -> tuple[str, CameraSpec]:
    """(label, spec) for a profile's `role` camera on `device_name`.

    The mode is the bench's when the device offers it, else the largest
    4:3 one no wider, else the largest no wider, else the largest: a mode
    the device does not offer would open at some other size. With no
    `sizes` (the modes could not be read) the list is left empty, which
    lets any mode through, and the bench's mode is kept."""
    label, wanted, crop, controls, backend, notes = NEW_CAMERAS[role]
    sizes = sorted({(int(w), int(h)) for w, h in sizes},
                   key=lambda s: s[0] * s[1])
    default = wanted
    if sizes and wanted not in sizes:
        narrower = [s for s in sizes if s[0] <= wanted[0]] or sizes
        four_three = [s for s in narrower if s[0] * 3 == s[1] * 4]
        default = (four_three or narrower)[-1]
    return label, CameraSpec(
        device_name=device_name, resolutions=[list(s) for s in sizes],
        default_resolution=list(default), fps=30, fourcc="MJPG",
        controls=dict(controls), crop=crop, backend=backend, notes=notes)


class Session(QObject):
    profile_changed = Signal(object)
    robot_state_changed = Signal(str)
    # The run's labware map was re-read; carries the RunState. Separate from
    # robot_state_changed because loading a plate changes nothing in the
    # status bar and everything on the deck.
    labware_changed = Signal(object)
    # What the robot's record says is on the pipette; carries a Tip. Re-read
    # from the run's command log after connect and after every tip command,
    # never inferred from what this application asked for: a tip picked up
    # from a notebook is in that log too, and a tip nobody knows about is
    # the first way the robot crashes.
    tip_changed = Signal(object)
    # What is in the tip, in µl, counted here (`in_tip`); carries the count.
    in_tip_changed = Signal(float)
    # The rail lights, as last read from the robot; carries bool or None.
    lights_changed = Signal(object)
    # The routine a run would deliver into, or None. Set by the Plate plan page
    # and read by the Picking page: the two never see each other, and the
    # session is what a session is attached to.
    routine_changed = Signal(object)
    # The liquid plan a liquid run does, and which of its groups (None: all),
    # or None. Set by Liquid's Plan step (Use this plan).
    liquid_plan_changed = Signal(object)
    camera_opened = Signal(str)
    camera_closed = Signal(str)
    # The application's settings were saved; carries the AppSettings.
    settings_changed = Signal(object)
    error = Signal(str)
    # Stop was pressed (the status bar's button, or Esc): every page stops
    # what it runs at its next safe point. See `request_stop`.
    stop_requested = Signal()
    # A page asks for another to be shown, by its name in `shell.PAGES`:
    # the checklist before a run sends the operator where a step is done.
    page_requested = Signal(str)
    # A page asks for a step of another task, (task, step): Picking's
    # Finish by hand opens Manual pick's Work.
    task_requested = Signal(str, str)
    # Picking done's Choose plan: the plan a run delivered into, for Plan to
    # show again, not in use, its counts fresh at Use this plan.
    plan_chosen = Signal(object)
    # A memo point was added, changed or removed; see `memo_points`.
    memo_points_changed = Signal()
    # What now holds the robot (`robot_lease`), in a few words, or None.
    # Emitted from whichever thread claimed or released it.
    robot_busy_changed = Signal(object)
    # A run's page may have paused, waited or carried on: the top bar's
    # pill asks it again (`run_activity` on the run's page).
    run_activity_changed = Signal()
    # Lock was turned on or off (`set_locked`); carries the new state.
    lock_changed = Signal(bool)
    # A step was confirmed or its confirmation dropped; see `confirm`.
    confirmations_changed = Signal()

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
        # What the connection monitor last saw (`robot_seen`): None until it
        # has looked. `restarted`: the robot's session changed under this
        # one - it was switched off and on - until a new one is made.
        self.robot_answers: bool | None = None
        self.restarted = False
        # The robot session whose carrying on failed (`adopt_failed`): not
        # tried again; Home and connect is the way on.
        self.adopt_refused: str | None = None
        # The robot session let go on the operator's word (`release_run`):
        # no session is carried on with until `continue_run` or a new one
        # made here - the one the Opentrons app made meanwhile included.
        self.released: str | None = None
        # Counts every change of the robot session this one holds: a look
        # begun before one and answered after it saw the robot as it was
        # (`RobotMonitor`), and is dropped.
        self.run_generation = 0
        self.routine = None                      # see routine_changed
        self.liquid_plan = None                  # see liquid_plan_changed
        self.liquid_groups = None
        self.tip = Tip.unknown()                 # see tip_changed
        # What is in the tip, in µl, from `aspirate` and `dispense`; see
        # "liquid in the tip".
        self.in_tip = 0.0
        # The wells taken from each rack, by its labware id: a rack loaded
        # again, or a new robot session, is a new id and starts full.
        self._used_tips: dict[str, set[str]] = {}
        # Every move across the deck: the travel height, the measured top
        # of Z and the jog envelope (`workflows.motion`). One for the
        # session's life; it is told the robot, the tip and the deck
        # modules as they change.
        self.motion = Motion(modules=self._deck_modules, tip=self.tip)
        # One job at a time that moves the robot or changes its session
        # (`hardware.robot_lease`): claimed before it, on every page.
        self.robot_lease = RobotLease(on_change=self.robot_busy_changed.emit)
        # The second Stop's hold (`hold_robot`); the lock (`set_locked`) is
        # the other reason the robot wrapper refuses moves.
        self._stop_hold = False
        self.lights: bool | None = None          # see lights_changed
        self.cameras: CameraManager | None = None
        self._open: dict[str, object] = {}
        # See preview_camera: set in a worker, closed from the window.
        self._preview = None
        self._preview_lock = threading.Lock()
        # The cuboid detector, one for the application: Picking and Manual
        # control both run it, and the weights are seconds to load and
        # hundreds of megabytes to hold twice.
        self.detector = DetectorService(self)
        # Points marked on Manual control's picture: held here, so the
        # "points" layer of every page draws them, and gone at the next
        # start - a profile's positions are what is kept.
        self.memo_points: list[MemoPoint] = []
        # What the operator confirmed in this robot session (`confirm`):
        # key -> the robot session it was confirmed in.
        self._confirmed: dict[str, str | None] = {}
        # When the robot was last homed from here (`home`), UTC: a tip
        # calibration measured before it is to do again.
        self.homed_at: datetime | None = None
        # This computer's settings (config.app_settings): the robot's address
        # and where outputs go. Applied at once, so every writer asks paths
        # for the right folder from the first file.
        self.settings, self.settings_note = app_settings.load_settings()
        app_settings.apply(self.settings)
        if self.settings_note:
            log.error("%s", self.settings_note)

    # -- confirmations ---------------------------------------------------------
    #
    # A step that is done because the operator said so - the deck checked
    # against the real one, the dish bottom's last value taken, a plate's
    # drawing used instead of measuring it - holds for the robot session it
    # was said in (REDESIGN §4). The robot remembers its deck across a
    # restart of this application, not across its own: what it holds is not
    # what the operator checked. Kept in memory only.

    def _robot_session_id(self) -> str | None:
        state = self.run_state
        return state.run_id if state is not None else None

    def confirm(self, key: str) -> None:
        """Ignored with no robot session: there is nothing for it to hold
        for."""
        run_id = self._robot_session_id()
        if run_id is None:
            return
        self._confirmed[key] = run_id
        self.confirmations_changed.emit()

    def is_confirmed(self, key: str) -> bool:
        run_id = self._robot_session_id()
        return run_id is not None and self._confirmed.get(key) == run_id

    # -- memo points ---------------------------------------------------------

    def add_memo_point(self, x: float, y: float,
                       z: float | None) -> MemoPoint:
        """A new point, named with the lowest free number: M1, M2 ... in the
        order marked, and a deleted point's number is the next one's."""
        taken = {p.name for p in self.memo_points}
        number = 1
        while f"M{number}" in taken:
            number += 1
        point = MemoPoint(f"M{number}", float(x), float(y),
                          None if z is None else float(z))
        self.memo_points.append(point)
        log.info("memo point %s at (%.2f, %.2f)%s", point.name, point.x,
                 point.y, "" if z is None else f", z {point.z:g}")
        self.memo_points_changed.emit()
        return point

    def memo_problem(self, point: MemoPoint) -> str:
        """Why the tip cannot go to `point`, or "".

        The jog envelope's checks: X and Y on the deck, and Z not below the
        floor there - the top of a deck module in the profile, over its slot
        (the deck definition's slot, in the robot's frame), so a point
        marked over a 64 mm platform at z 50 is said to be inside it. A
        point with no Z is gone to at the travel height, over everything.
        """
        z = self.motion.envelope.z[1] if point.z is None else point.z
        return self.motion.refused((point.x, point.y, z))

    def memo_point(self, name: str) -> MemoPoint | None:
        return next((p for p in self.memo_points if p.name == name), None)

    def change_memo_point(self, name: str, **values) -> None:
        """Set any of x, y and z (None for z: the travel height)."""
        point = self.memo_point(name)
        if point is None:
            return
        for key, value in values.items():
            if key not in ("x", "y", "z"):
                raise ValueError(f"a memo point has no {key!r}")
            setattr(point, key, None if value is None else float(value))
        self.memo_points_changed.emit()

    def delete_memo_point(self, name: str) -> bool:
        """Delete the point named `name`; False if there is none. Its
        number is free again: the next point marked takes it."""
        kept = [p for p in self.memo_points if p.name != name]
        if len(kept) == len(self.memo_points):
            return False
        self.memo_points = kept
        log.info("memo point %s deleted", name)
        self.memo_points_changed.emit()
        return True

    # -- state ---------------------------------------------------------------

    @property
    def mock(self) -> bool:
        return bool(self.options.mock)

    @property
    def robot_state(self) -> str:
        if self.robot is None:
            return PROBED if self._api is not None else DISCONNECTED
        base = MOCK if self.mock else CONNECTED
        state = self.run_state
        if state is not None and state.exists and self.run_origin:
            origin = {"reused": "continued"}.get(self.run_origin,
                                                 self.run_origin)
            return f"{base}, session {state.label} ({origin})"
        return base

    def has_pixel_map(self) -> bool:
        """Whether a click on a picture can move the robot: only through
        the profile's pixel map, so not before the first camera
        calibration. A method, so a page hands it to
        `CameraView.set_click_to_move` as it is."""
        profile = self.profile
        return profile is not None and profile.pixel_map is not None

    @property
    def robot_address(self) -> str:
        """Where Connect goes, as shown: the command line's address, else the
        saved one, else the wrapper's default."""
        host = self.options_host or self.settings.robot_host
        if not host:
            return "default address"
        return host if ":" in host else f"{host}:{self.settings.robot_port}"

    @property
    def has_connection(self) -> bool:
        """The robot client exists: made by the first look at the robot,
        answered or not. Whether the robot answers is `robot_answers`."""
        return self._api is not None

    @property
    def options_host(self) -> str | None:
        return getattr(self.options, "robot_host", None)

    def set_settings(self, settings) -> None:
        """Save the application's settings and apply them. A new robot
        address, while the robot is not connected, is where the next look
        goes: the client made for the old one is let go (it holds no robot
        session, so nothing is retracted)."""
        address = self.robot_address
        app_settings.save_settings(settings)
        app_settings.apply(settings)
        if settings.inference_device != self.settings.inference_device:
            # Loaded for the old device; the next page that needs it loads
            # it again.
            self.detector.forget()
        self.settings, self.settings_note = settings, ""
        log.info("settings saved to %s", app_settings.settings_path())
        if (self.robot_address != address and self._api is not None
                and self.robot is None):
            self._api = self.run_state = self.robot_answers = None
            # A look at the old address still out is dropped.
            self.run_generation += 1
            log.info("the robot is looked for at %s", self.robot_address)
            self.robot_state_changed.emit(self.robot_state)
        self.settings_changed.emit(settings)

    @property
    def open_cameras(self) -> list[str]:
        return sorted(self._open)

    def _deck_modules(self) -> list:
        """The loaded profile's deck modules, for the jog envelope's floors.
        Asked for each time: the profile is replaced and edited in place."""
        return list(self.profile.deck.modules) if self.profile is not None else []

    @property
    def upper_camera_label(self) -> str | None:
        """The camera the pixel map belongs to, by name.

        `ProfileMeta.camera_label` is where an installation records it, and
        it is the only non-guess available: the map, the detector and every
        pixel-to-deck conversion are that camera's. Falling back to a label
        that does not say "under" is a guess and says so by being last.
        """
        profile = self.profile
        if profile is None:
            return None
        named = profile.meta.camera_label
        if named and named in profile.cameras:
            return named
        for label in sorted(profile.cameras):
            if "under" not in label.lower():
                return label
        return None

    @property
    def lower_camera_label(self) -> str | None:
        """The other one. This rig has two cameras and the upper one is
        named in the profile, so the lower one is what is left."""
        profile = self.profile
        if profile is None:
            return None
        upper = self.upper_camera_label
        others = [label for label in sorted(profile.cameras) if label != upper]
        if not others:
            return None
        under = [label for label in others if "under" in label.lower()]
        return (under or others)[0]

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
        if not self.uses_mock_profile():
            store.remember_last_profile(profile.name)
        # A manager per profile: the labels and the device names it resolves
        # belong to the installation, not to the application.
        self.cameras = CameraManager.from_profile(profile)
        log.info("profile %r loaded from %s", profile.name, profile.path)
        if self._api is not None:
            # This profile's modules, not the last one's, for the next load.
            self.register_deck_modules()
        self.profile_changed.emit(profile)
        if self.run_state is not None:
            # What counts as a deck problem is the new profile's to say.
            self.labware_changed.emit(self.run_state)
        return profile

    def load_last_profile(self) -> store.Profile:
        """The start of every day: the profile loaded last, or a profile
        `default` made here when there is none. Profiles are never chosen
        on screen (REDESIGN §12.8); the robot setup is what they hold."""
        name = store.last_profile()
        if name is None:
            name = DEFAULT_PROFILE_NAME
            store.create_profile(name)
            log.info("no profile yet: created %r", name)
        return self.load_profile(name)

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

    def set_routine(self, routine) -> None:
        """What a picking run would deliver into. None clears it."""
        self.routine = routine
        log.info("routine in hand: %s", routine.name if routine is not None
                 else "none")
        self.routine_changed.emit(routine)

    def set_liquid_plan(self, program, groups=None) -> None:
        """Use this plan on Liquid's Plan step: a copy of `program` is what
        a liquid run does, `groups` the indices of the groups it runs (None:
        all, one after another). Liquid's Plan is done for the robot
        session (`tasks.step_state`). None clears it."""
        self.liquid_plan = (program.model_copy(deep=True)
                            if program is not None else None)
        self.liquid_groups = (list(groups) if program is not None
                              and groups is not None else None)
        if program is not None:
            log.info("liquid plan in hand: %d groups, running %s",
                     len(program.groups), "all" if groups is None
                     else ", ".join(program.groups[i].name for i in groups))
            self.confirm("liquid_plan")
        self.liquid_plan_changed.emit(self.liquid_plan)

    # -- templates -----------------------------------------------------------
    #
    # Plans saved under the operator's name, to start the next one from
    # (`config.templates`). On the Plan step: Save as template for a new
    # plan, Save changes to template for an edited one.

    def plate_templates(self) -> list[templates.Template]:
        return templates.plate_templates()

    def save_plate_template(self, preset, name: str) -> templates.Template:
        """`preset` as a new template called `name`. Refused for an empty
        name or one a template already has: Save as never writes over."""
        name = " ".join(str(name).split())
        if not name:
            raise SessionError("Name the template.")
        if templates.name_in_use(name):
            raise SessionError(f'A template is already called "{name}".')
        preset.name = name
        return templates.save_plate_template(preset)

    def save_plate_template_changes(self, template: templates.Template,
                                    preset) -> templates.Template:
        """`preset` written over `template`, which keeps its name."""
        return templates.save_plate_template(preset, over=template)

    def liquid_templates(self) -> list[templates.Template]:
        return templates.liquid_templates()

    def save_liquid_template(self, program, name: str) -> templates.Template:
        """`program` as a new liquid plan template called `name`, refused as
        `save_plate_template` refuses. The program itself is not renamed."""
        name = " ".join(str(name).split())
        if not name:
            raise SessionError("Name the template.")
        if templates.name_in_use(name, templates.LIQUID_PLANS):
            raise SessionError(f'A template is already called "{name}".')
        return templates.save_liquid_template(
            program.model_copy(update={"name": name}))

    def save_liquid_template_changes(self, template: templates.Template,
                                     program) -> templates.Template:
        """`program` written over `template`, which keeps its name."""
        return templates.save_liquid_template(program, over=template)

    def remember(self, name: str, position) -> None:
        if self.profile is None:
            raise SessionError("no profile is loaded, so there is nowhere to "
                               "remember a position")
        self.profile.remember(name, position)
        log.info("remembered %r at %s in profile %r", name,
                 tuple(round(float(v), 2) for v in position), self.profile.name)
        self.profile_changed.emit(self.profile)

    def set_dish_bottom(self, z: float) -> None:
        """The Z calibration: the robot's Z with the tip on the dish bottom,
        into the picking settings and saved. Every pickup height is measured
        up from it (`PickingConfig.pickup_height`). Measured, the Dish
        bottom step is done for this robot session."""
        if self.profile is None:
            raise SessionError("no profile is loaded, so there is nowhere to "
                               "save the dish bottom")
        before = self.profile.picking.dish_bottom
        self.profile.picking = self.profile.picking.model_copy(
            update={"dish_bottom": round(float(z), 2)})
        self.profile.save_picking()
        # When, for the checklist before a run: a calibration's fact, kept
        # with the others. Not archived for this: nothing else changed.
        self.profile.calibration.dish_bottom_set_at = datetime.now(timezone.utc)
        self.profile.save_calibration(backup=False)
        log.info("dish bottom %.2f -> %.2f mm in profile %r", before,
                 self.profile.picking.dish_bottom, self.profile.name)
        self.profile_changed.emit(self.profile)
        self.confirm("dish_bottom")

    def use_last_dish_bottom(self) -> None:
        """Use it: the dish bottom measured last time, taken as it is for
        this robot session (REDESIGN §8.8). Refused while it was never
        measured: the default is a number nobody checked."""
        if self.profile is None:
            raise SessionError("no profile is loaded")
        if self.profile.calibration.dish_bottom_set_at is None:
            raise SessionError("The dish bottom was never measured.")
        log.info("dish bottom %.2f mm taken as it is",
                 self.profile.picking.dish_bottom)
        self.confirm("dish_bottom")

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
            self._sync_clock(self._client())
        state = _run_state(self._api)
        self.run_state = state
        log.info("robot probed: %s", state.describe())
        self.robot_state_changed.emit(self.robot_state)
        return state

    def _sync_clock(self, api) -> None:
        """Before a run is made: a session created from now on is stamped
        right (`hardware.robot_clock`). Never raises."""
        if not self.mock:
            log.info("%s", robot_clock.sync_clock(api))

    def _client(self):
        """The robot client, made the first time. Costs no network. GUI
        thread: a look is handed the client (`look_at_robot`)."""
        if self._api is None:
            if self.mock:
                self._api = MockRobot()
            else:
                from opentrons_api import ot2_api
                host = self.options_host or self.settings.robot_host
                self._api = ot2_api.OpentronsAPI(
                    host=host,
                    port=None if host and ":" in host
                    else self.settings.robot_port)
                log.info("connecting to the robot at %s", self._api.BASE_URL)
            self._apply_hold()
        return self._api

    # -- the connection monitor ----------------------------------------------
    #
    # The robot is looked at every few seconds (`gui.robot_monitor`): the
    # read on a worker (`look_at_robot`), what it changes on the GUI thread
    # (`robot_seen`). Looking moves nothing, and neither does carrying on
    # with the robot's session. A new session homes, and a hand may be in
    # the robot, so that one waits for the operator (`needs_home`).

    def look_at_robot(self, api=None) -> RunState | None:
        """What the robot is doing, or None if it does not answer.
        Blocking, for a worker, with `api` from `_client` (None: made here,
        on the calling thread); changes nothing on the session."""
        if api is None:
            api = self._client()
        try:
            if not self.mock:
                # Answers at once or not at all: a run list asked of a robot
                # switched off waits for as long as the network does.
                api.health(timeout=LOOK_TIMEOUT_S)
                if self.robot_answers is not True:
                    # Back, or here for the first time: its clock may have
                    # been reset.
                    self._sync_clock(api)
            return _run_state(api)
        except (OSError, ValueError) as exc:
            # OSError: requests' errors are, as is the mock's switched off.
            # ValueError: a robot still starting answers with no JSON.
            log.debug("the robot does not answer: %s", exc)
            return None

    def robot_seen(self, state: RunState | None) -> bool:
        """Take what `look_at_robot` saw. GUI thread. True: the robot's
        session is to be carried on with now (`adopt_run`, under the
        robot's lease)."""
        if state is None:
            if self.robot_answers is not False:
                self.robot_answers = False
                log.warning("the robot does not answer")
                self.robot_state_changed.emit(self.robot_state)
            return False
        answered, self.robot_answers = self.robot_answers, True
        if answered is not True:
            log.info("the robot answers: %s", state.describe())
        changed = answered is not True
        if self.robot is not None:
            held = self.run_state.run_id if self.run_state else None
            # A session that cannot take commands any more is let go only
            # while nothing uses the robot: a status passing by under a run
            # is the run's to meet. Another id is a restart either way.
            if state.run_id != held or (not state.reusable
                                        and not self.robot_lease.busy):
                self._lose_run(state, restarted=state.run_id != held)
                changed = False                 # said by _lose_run
        elif (self.run_state is None or state.run_id != self.run_state.run_id
              or state.status != self.run_state.status):
            self.run_state = state
            changed = True
        if changed:
            self.robot_state_changed.emit(self.robot_state)
        return (self.robot is None and state.reusable
                and self.released is None
                and state.run_id != self.adopt_refused)

    def adopt_failed(self, run_id: str | None) -> None:
        """Carrying on with `run_id` failed: it is not tried again, and
        the robot needs to home (`needs_home`)."""
        self.adopt_refused = run_id
        self.robot_state_changed.emit(self.robot_state)

    @property
    def needs_home(self) -> bool:
        """The robot answers and has no session to carry on with: only a
        new one, which homes, gets it working - on the operator's word."""
        state = self.run_state
        return (self.robot is None and self.robot_answers is True
                and state is not None
                and (not state.reusable
                     or state.run_id == self.adopt_refused))

    def _lose_run(self, state: RunState, restarted: bool) -> None:
        """The robot's session is not this one's any more: switched off and
        on (`restarted`), or ended from elsewhere. What it held - labware,
        the tip, what the operator confirmed - is not known now."""
        if restarted:
            log.warning("the robot was restarted: its session %s is gone; "
                        "labware and the tip are unknown",
                        self._robot_session_id())
        else:
            log.warning("the robot's session can no longer be used: %s",
                        state.describe())
        self.restarted = self.restarted or restarted
        self.run_generation += 1
        self.robot = None
        self.motion.set_robot(None)
        self.run_state, self.run_origin = state, None
        self._forget_tip()
        if self._confirmed:
            self._confirmed.clear()
            self.confirmations_changed.emit()
        self.labware_changed.emit(state)
        self.robot_state_changed.emit(self.robot_state)

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
                f"the robot's current session cannot be continued: "
                f"{state.describe()}. Start a new robot session instead.")

        if not self.mock:
            self._api.get_run_info()
            uploaded = labware.ensure_definitions(self._api, verbose=False)
            log.info("uploaded %d custom definitions into run %s",
                     len(uploaded), state.run_id)
            if not state.has_pipette:
                self._api.load_pipette()
                log.debug("pipette %s set up in run %s",
                          self._api.PIPETTE, state.run_id)
        self._ready("reused")
        return self.robot

    def home(self) -> None:
        """Home all axes. Blocking; **moves the gantry**. The tip stays on,
        but the tip calibration is to do again (`homed_at`,
        `readiness.tip_calibration_status`): the home moves the pipette and
        the measured offset no longer holds."""
        robot = self.robot
        if robot is None:
            raise RuntimeError("no robot connected")
        log.info("homing")
        robot.home_robot(verbose=False)
        self.homed_at = datetime.now(timezone.utc)
        log.info("homed; the tip calibration is to do again")
        self.confirmations_changed.emit()

    def new_run(self, name: str | None = None):
        """A fresh run, and then home. Blocking; **moves the gantry**.

        `name` is what the session is called on screen from now on, kept on
        this computer against the robot's id; None is the date, the time and
        the profile (`config.robot_sessions.default_name`).

        Homing is part of this and not a separate button because nothing moves
        after a new run until the robot has been homed, and a run created
        without it is a robot that refuses every command with no visible reason.
        The four steps are one method so that state cannot exist.

        create_run is timed and logged: on a robot left powered on it has been
        seen to take longer with each run created, and a number in the log is
        how that stops being an impression.
        """
        state = self._require_probe()
        if self.mock:
            if not state.exists:                # switched off and on
                self._api.create_run()
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
            log.debug("pipette %s set up", self._api.PIPETTE)
            log.info("homing")
            self._api.home_robot(verbose=False)
            log.info("homed")
            self.run_state = _run_state(self._api)
        self._name_run(name)
        self._ready("new")
        return self.robot

    def _name_run(self, name: str | None) -> None:
        state = self.run_state
        if state is None or not state.exists:
            return
        name = (name or "").strip() or robot_sessions.default_name(
            self.profile.name if self.profile is not None else None)
        # Not for the mock: its one run id is not a robot's, and kept on
        # disk it would name a session no robot has.
        if not self.mock:
            try:
                robot_sessions.remember_name(state.run_id, name)
            except OSError as exc:          # a name is never worth a failure
                log.warning("could not keep the session's name: %s", exc)
        state.name = name
        log.info("robot session %s is %r", state.run_id, name)

    def refresh_run_state(self) -> RunState:
        """Re-read what the run holds, after labware was loaded or moved."""
        if self._api is None:
            raise SessionError("not connected")
        self.run_state = _run_state(self._api)
        self.labware_changed.emit(self.run_state)
        return self.run_state

    # -- labware -------------------------------------------------------------
    #
    # The robot only knows what it was told. A plate put on the deck by hand
    # is invisible to the run, and a plate loaded into the run and then taken
    # away by hand is still there as far as every move_to_well is concerned.
    # These two are the telling; both are blocking, both move nothing.

    def load_labware(self, definition, slot: int):
        """Tell the run that `definition` sits in `slot`. Blocking; moves
        nothing. Returns the LoadedLabware entry the run reports back.

        A custom definition is uploaded first. Uploads are per run and
        idempotent, so this repeats what connect did rather than trusting
        that it happened - a file added to labware/ after the connect is the
        case that trust gets wrong.

        An occupied slot is emptied first with move_labware('offDeck'): the
        robot refuses to load over labware it believes is there, and the
        operator asking for a plate in slot 5 has already decided what is in
        slot 5. Both steps are logged so the run's history reads as it went.
        """
        self._require_robot()
        slot = int(slot)
        if not 1 <= slot <= 11:
            raise SessionError(f"slot {slot} is not a deck slot (1-11; 12 is "
                               f"the fixed trash)")
        if self.refuses_labware(slot):
            raise SessionError(f"Slot {slot} holds the calibration module: "
                               f"no labware goes there.")
        current = self.run_state.labware.get(str(slot)) if self.run_state else None
        if current is not None:
            self._move_off_deck(current)
        if definition.source == "local" and not self.mock:
            # The mock has no run to upload into and accepts any load name.
            labware.upload_definition(self._api, definition)
            log.info("uploaded %s into run %s", definition.load_name,
                     getattr(self._api, "run_id", "?"))
        # The wrapper attaches a module's offset at load from its own table,
        # and silently loads without one when the slot is not in it. So the
        # table is the profile's as it is now, every time, not as it was at
        # connect: a profile loaded or a module placed since must count.
        modules = self.register_deck_modules()
        labware.load_labware(self._api, definition.load_name, slot,
                             namespace=definition.namespace,
                             version=definition.version, verbose=False)
        log.info("loaded %s (%s) into slot %d", definition.load_name,
                 definition.namespace, slot)
        state = self.refresh_run_state()
        entry = state.labware.get(str(slot))
        if entry is None:
            raise SessionError(
                f"the robot session accepted {definition.load_name!r} for "
                f"slot {slot} but does not report it there afterwards")
        module = next((m for m in modules if slot in m.slots), None)
        problem = next((p for p in self.deck_problems()
                        if p.slot == str(slot)), None)
        if module is not None and problem is not None:
            # Said here, at the load, rather than left to the deck warning:
            # the reload that warning asks for would fail the same way.
            raise SessionError(
                f"slot {slot}: the robot did not attach the {module.name or 'module'} "
                f"offset (+{module.height_mm:g} mm) to "
                f"{definition.load_name}; it reports "
                f"{'none' if problem.applied is None else problem.applied}. "
                f"A well move would hit the module: do not use this slot "
                f"until it loads with the offset.")
        return entry

    def refuses_labware(self, slot) -> bool:
        """Whether nothing may be loaded into `slot`: the fixed trash, or
        the slot the profile puts the calibration module in (#40)."""
        slot = int(slot)
        if slot == 12:
            return True
        module = (self.profile.deck.module_for(slot)
                  if self.profile is not None else None)
        return module is not None and module.name == CALIBRATION_MODULE

    def unload_labware(self, slot: int) -> None:
        """move_labware('offDeck') for whatever the run holds in `slot`.
        Blocking; moves nothing. A slot the run thinks is empty is a no-op
        with a log line, not an error: the operator's intent is met."""
        self._require_robot()
        current = self.run_state.labware.get(str(int(slot))) if self.run_state else None
        if current is None:
            log.info("slot %s already holds nothing in the run", slot)
            return
        self._move_off_deck(current)
        self.refresh_run_state()

    def move_labware(self, source: int, target: int) -> LoadedLabware:
        """What the run holds in `source`, into the empty slot `target`.
        Blocking; moves nothing.

        Loaded again rather than moved with move_labware: a module's offset
        attaches only at load, so a plate moved onto a platform would be
        planned a platform's height too low. Loaded at `target` first, so a
        refused load leaves the plate where it was. Not a rack whose tip
        is on: the tip's record names the rack it came from, and putting
        it back would go to one no longer there."""
        entry = self._labware_in(source)
        if self.run_state.labware.get(str(int(target))) is not None:
            raise SessionError(f"Slot {int(target)} is not empty.")
        if self.tip.returnable and self.tip.slot == str(int(source)):
            raise SessionError("The tip on came from this box. Put it back "
                               "or drop it first.")
        definition = labware.resolve_definition(entry.load_name)
        moved = self.load_labware(definition, int(target))
        self._move_off_deck(entry)
        self.refresh_run_state()
        return moved

    def _move_off_deck(self, entry: LoadedLabware) -> None:
        self._api.move_labware(entry.labware_id, "offDeck", verbose=False)
        log.info("moved %s out of slot %s (off deck)", entry.load_name,
                 entry.slot)

    def _require_robot(self) -> None:
        if self.robot is None:
            raise SessionError("no robot session to load labware into: "
                               "connect the robot on the Profile page first")

    # -- deck modules --------------------------------------------------------
    #
    # The picking platform and the calibration module raise the labware in
    # their slots by an amount the robot's deck model does not have. The
    # profile says which slots and by how much; the wrapper is told at every
    # connect and attaches the offset to each labware loaded afterwards; and
    # the run is read back to find labware that was loaded without it.

    def register_deck_modules(self) -> list[DeckModule]:
        """Tell the wrapper the profile's modules. Blocking on nothing: the
        table is client-side, and it reaches the robot per load."""
        if self._api is None:
            return []
        modules = list(self.profile.deck.modules) if self.profile else []
        # Replaced, not appended: the wrapper refuses a slot list it already
        # has, and a reconnect or an edit must not fight the old table.
        self._api.slot_offsets = {"data": []}
        for module in modules:
            self._api.add_slot_offsets(list(module.slots), tuple(module.offset))
        if modules:
            log.info("deck modules registered for loading: %s",
                     "; ".join(m.describe() for m in modules))
        return modules

    def plates(self) -> list[tuple[str, LoadedLabware, LabwareDefinition]]:
        """(slot, what the run holds there, its definition) for every
        labware in the run that a cuboid could be put into, nearest slot
        first. Empty with no robot: the list is the run's, not the plan's."""
        state = self.run_state
        if self.robot is None or state is None:
            return []
        out = []
        for slot, entry in sorted(state.labware.items(),
                                  key=lambda kv: int(kv[0])
                                  if kv[0].isdigit() else 99):
            try:
                definition = resolve_definition(entry.load_name)
            except LabwareError:
                continue
            if (definition.category in NOT_A_DESTINATION
                    or not definition.ordering):
                continue
            out.append((slot, entry, definition))
        return out

    def deck_problems(self) -> list[DeckProblem]:
        """Labware the run holds in a module slot without that module's
        offset. From the last run state read; nothing is asked here."""
        if self.robot is None or self.run_state is None or self.profile is None:
            return []
        problems = []
        for slot, entry in sorted(self.run_state.labware.items()):
            module = self.profile.deck.module_for(slot)
            if module is None:
                continue
            expected = tuple(float(v) for v in module.offset)
            applied = entry.offset
            if applied is None or any(abs(a - e) > 0.05
                                      for a, e in zip(applied, expected)):
                problems.append(DeckProblem(slot, entry.load_name, expected,
                                            applied))
        return problems

    def reload_labware(self, slot) -> LoadedLabware:
        """Load again what the run holds in `slot`, so the module offset
        attaches. Moves nothing; the labware goes off deck and comes back
        with a new id."""
        entry = self._labware_in(slot)
        definition = labware.resolve_definition(entry.load_name)
        return self.load_labware(definition, int(slot))

    def set_deck_modules(self, modules: list[DeckModule]) -> None:
        """Replace the profile's modules and save; re-register if connected.
        The run's labware is judged again afterwards, since what counts as
        a problem just changed."""
        if self.profile is None:
            raise SessionError("no profile to save deck modules into")
        # Built again, not copied: the constructor is what refuses two
        # modules in one slot. The catalogue and the history are kept.
        deck = self.profile.deck
        self.profile.deck = DeckConfig(modules=list(modules),
                                       module_types=list(deck.module_types),
                                       recent_labware=list(deck.recent_labware),
                                       well_centers=list(deck.well_centers))
        self.profile.save_deck()
        log.info("deck modules saved to profile %r: %s", self.profile.name,
                 "; ".join(m.describe() for m in modules) or "none")
        if self._api is not None:
            self.register_deck_modules()
        self.profile_changed.emit(self.profile)
        if self.run_state is not None:
            self.labware_changed.emit(self.run_state)

    def set_module_types(self, types: list[ModuleType]) -> None:
        """Replace the catalogue of module kinds and save. Placed modules
        keep the heights they were placed with."""
        if self.profile is None:
            raise SessionError("no profile to save module types into")
        deck = self.profile.deck
        self.profile.deck = DeckConfig(modules=list(deck.modules),
                                       module_types=list(types),
                                       recent_labware=list(deck.recent_labware),
                                       well_centers=list(deck.well_centers))
        self.profile.save_deck()
        log.info("module types in profile %r: %s", self.profile.name,
                 ", ".join(f"{t.name} {t.height_mm:g} mm" for t in types)
                 or "none")
        self.profile_changed.emit(self.profile)

    def set_well_center(self, slot, load_name: str, offset, well: str,
                        depth_mm: float | None = None) -> None:
        """Save where the wells of the plate in `slot` really are (see
        `WellCenter`), replacing any earlier measurement of it. The depth
        set for it before is kept; `depth_mm` is used when there was none."""
        if self.profile is None:
            raise SessionError("no profile to save the well center into")
        deck = self.profile.deck
        before = deck.well_center(slot, load_name)
        if before is not None and before.depth_mm is not None:
            depth_mm = before.depth_mm
        center = WellCenter(slot=int(slot), load_name=load_name,
                            offset=[round(float(v), 3) for v in offset],
                            well=well, measured_at=datetime.now(),
                            depth_mm=depth_mm)
        deck.well_centers = [c for c in deck.well_centers
                             if (c.slot, c.load_name) != (center.slot,
                                                          load_name)]
        deck.well_centers.append(center)
        self.profile.save_deck()
        log.info("well center of %s in slot %s: %s", load_name, slot,
                 center.describe())
        self.profile_changed.emit(self.profile)

    def set_well_depth(self, slot, load_name: str, depth_mm: float) -> None:
        """The depth of the measured plate's wells, for its measured
        bottom."""
        center = (self.profile.deck.well_center(slot, load_name)
                  if self.profile is not None else None)
        if center is None:
            raise SessionError("measure the well center first")
        depth = round(float(depth_mm), 3)
        if center.depth_mm == depth:
            return
        center.depth_mm = depth
        self.profile.save_deck()
        log.info("well depth of %s in slot %s: %g mm", load_name, slot, depth)
        self.profile_changed.emit(self.profile)

    def skip_plate(self, slot, load_name: str) -> None:
        """Skip this plate, use the drawing: its well center is not
        measured, on purpose, for this robot session (REDESIGN §12.10).
        Per slot and plate type, like a measurement; in memory only."""
        if self._robot_session_id() is None:
            raise SessionError("No robot session.")
        log.info("well center of %s in slot %s skipped: the drawing is used",
                 load_name, slot)
        self.confirm(_skipped_plate(slot, load_name))

    def plate_skipped(self, slot, load_name: str) -> bool:
        return self.is_confirmed(_skipped_plate(slot, load_name))

    def well_center_status(self, slot, load_name: str) -> str:
        """MEASURED, SKIPPED or NOT_MEASURED. A measurement is the plate's
        whatever was said in this robot session."""
        profile = self.profile
        if (profile is not None
                and profile.deck.well_center(slot, load_name) is not None):
            return MEASURED
        return SKIPPED if self.plate_skipped(slot, load_name) else NOT_MEASURED

    def forget_well_center(self, slot, load_name: str) -> None:
        if self.profile is None:
            return
        deck = self.profile.deck
        kept = [c for c in deck.well_centers
                if (c.slot, c.load_name) != (int(slot), load_name)]
        if len(kept) == len(deck.well_centers):
            return
        deck.well_centers = kept
        self.profile.save_deck()
        log.info("well center of %s in slot %s forgotten", load_name, slot)
        self.profile_changed.emit(self.profile)

    def remember_labware(self, load_name: str) -> None:
        """Note a load name as just used, for the Deck step's
        Frequent. Saved, but announced to nobody: only that list
        shows it, and it asks."""
        if self.profile is None:
            return
        self.profile.deck.remember_labware(load_name)
        self.profile.save_deck()

    # -- tips ----------------------------------------------------------------
    #
    # Four acts, all blocking and all of them **moving the gantry** except
    # the drop in place. The robot's answer to each is checked with
    # require_ok, because a tip command it declines is answered 201 like one
    # it performed. After each one the tip is re-read from the robot rather
    # than assumed from the command that was sent.

    def read_tip(self) -> Tip:
        """Ask the robot what is on the pipette. Blocking; moves nothing.

        From the run's command log (`hardware.tips.tip_state`), which is the
        robot's own record and covers a tip picked up from a notebook or the
        Opentrons app in the same run. A log that cannot be read leaves the
        answer unknown, which is reported as such and never as "no tip".
        """
        self._require_robot()
        if self.mock:
            held = getattr(self._api, "tip", None)
            # The mock keeps no command log: the count of pick-ups stands in
            # for the command's id.
            count = sum(1 for call in getattr(self._api, "calls", ())
                        if call[0] == "pick_up_tip")
            state = (tips.TipState(True, held[0], held[1],
                                   f"mock-pickup-{count}") if held
                     else tips.TipState(False))
        else:
            try:
                state = tips.tip_state(self._api, self.run_state.run_id)
            except Exception as exc:                 # noqa: BLE001
                log.warning("could not read the tip state: %s", exc)
                state = tips.TipState.unknown()
        slot = well = None
        length = state.length
        if state.attached and state.labware_id:
            for entry in (self.run_state.labware if self.run_state else {}).values():
                if entry.labware_id == state.labware_id:
                    slot, well = entry.slot, state.well
                    if length is None:
                        # The robot's answer did not say: the rack's whole
                        # tip length, longer than what the robot adds, so
                        # the Z top comes out low rather than high.
                        length = tips.nominal_tip_length(entry.load_name)
        tip = Tip(state.attached, slot, well, state.pickup,
                  round(length, 3) if length is not None else None)
        if tip.attached and tip != self.tip:
            log.info("tip length %s mm (%s)", tip.length,
                     "robot" if state.length is not None else
                     "rack definition, whole" if tip.length is not None
                     else "unknown")
        if tip != self.tip:
            log.info("robot reports: %s", tip.describe())
        same = (tip.attached and self.tip.attached
                and tip.pickup == self.tip.pickup)
        self.tip = tip
        if not same:
            self._set_in_tip(0.0)
        self.motion.tip_changed(tip)
        self.tip_changed.emit(tip)
        return tip

    def pick_up_tip(self, slot, well: str) -> None:
        """Take a tip from `well` of the rack in `slot`. Moves the gantry.

        Refused outright when the robot's record shows a tip already on, or
        cannot be read: a second tip pressed onto the first is a crash into
        the rack, and "unknown" is not "none". The slot has to hold a tip
        rack as far as the definition says (`isTiprack`), which is the same
        flag the robot decides on.
        """
        entry = self._labware_in(slot)
        if self.tip.attached is None:
            self.read_tip()
        if self.tip.attached:
            raise SessionError(f"the robot reports a tip on the pipette "
                               f"({self.tip.describe()}); drop it first")
        if self.tip.attached is None:
            raise SessionError("the robot's tip state could not be read, and "
                               "a tip is not picked up over an unknown one")
        definition = labware.resolve_definition(entry.load_name)
        if not definition.is_tiprack:
            raise SessionError(f"slot {slot} holds {entry.load_name}, which is "
                               f"not a tip rack")
        if well not in definition.wells:
            raise SessionError(f"{entry.load_name} has no well {well!r}")
        try:
            require_ok(self._api.pick_up_tip(entry.labware_id, well, verbose=False),
                       f"pick up tip from slot {slot} {well}")
            log.info("picked up a tip from slot %s %s", slot, well)
            self._used_tips.setdefault(entry.labware_id, set()).add(well)
        finally:
            self.read_tip()

    def drop_tip_in_place(self) -> None:
        """Let go of the tip where the pipette is. Moves nothing but the
        ejector."""
        self._require_robot()
        try:
            require_ok(self._api.drop_tip_in_place(verbose=False),
                       "drop tip in place")
            log.info("dropped the tip in place")
        finally:
            self.read_tip()

    def drop_tip_in_trash(self) -> None:
        """Drop the tip into the fixed trash. Moves the gantry to slot 12.

        The trash is an addressable area on this robot software, not
        labware, so this is `moveToAddressableAreaForDropTip` and then
        `dropTipInPlace` (`hardware.tips.drop_tip_in_trash`).
        """
        self._require_robot()
        try:
            if self.mock:
                self._api.move_to_trash()
                self._api.drop_tip_in_place()
            else:
                tips.drop_tip_in_trash(self._api)
            log.info("dropped the tip in the trash")
        finally:
            self.read_tip()

    def return_tip(self) -> None:
        """Put the tip back into the well the robot's record says it came
        from. Moves the gantry. Refused when the record has no rack for it."""
        self._require_robot()
        if not self.tip.returnable:
            raise SessionError("the robot's record has no rack to return this "
                               f"tip to ({self.tip.describe()}); use the "
                               "trash or drop it in place")
        slot, well = self.tip.slot, self.tip.well
        entry = self._labware_in(slot)
        try:
            require_ok(self._api.drop_tip(entry.labware_id, well, verbose=False),
                       f"return tip to slot {slot} {well}")
            log.info("returned the tip to slot %s %s", slot, well)
            self._used_tips.get(entry.labware_id, set()).discard(well)
        finally:
            self.read_tip()

    def tip_box_slots(self) -> list[int]:
        """The slots the robot session holds a tip box in, in order."""
        state = self.run_state
        if self.robot is None or state is None:
            return []
        out = []
        for slot, entry in state.labware.items():
            try:
                if resolve_definition(entry.load_name).is_tiprack:
                    out.append(int(slot))
            except (LabwareError, ValueError):
                continue
        return sorted(out)

    def used_tips(self, slot) -> set[str]:
        """The wells of the rack in `slot` a tip was taken from here and not
        put back. What this program saw: a tip taken by a notebook or the
        Opentrons app is not in it."""
        state = self.run_state
        entry = (state.labware.get(str(slot))
                 if self.robot is not None and state is not None else None)
        if entry is None:
            return set()
        return set(self._used_tips.get(entry.labware_id, ()))

    def _labware_in(self, slot) -> LoadedLabware:
        self._require_robot()
        entry = self.run_state.labware.get(str(slot)) if self.run_state else None
        if entry is None:
            raise SessionError(f"the robot session holds nothing in slot "
                               f"{slot}")
        return entry

    def _forget_tip(self) -> None:
        self.tip = Tip.unknown()
        self._set_in_tip(0.0)
        self.motion.tip_changed(self.tip)
        self.tip_changed.emit(self.tip)

    # -- liquid in the tip ---------------------------------------------------
    #
    # In place, where the tip is: Manual Work's Aspirate and Dispense, which
    # move it there first. Blocking, and moving nothing but the plunger.
    # What is in the tip is counted from these two, not read: the robot
    # keeps no such record. Another tip holds nothing; the same tip read
    # again keeps its count (`read_tip`).

    def aspirate(self, volume: float, flow_rate: float) -> float:
        """Aspirate `volume` µl at `flow_rate` µl/s; returns what is in the
        tip now. Refused unless the robot reports a tip on."""
        self._require_tip("aspirate")
        moves.aspirate(self.robot, volume, flow_rate)
        log.info("aspirated %g µl at %g µl/s", volume, flow_rate)
        self._set_in_tip(self.in_tip + float(volume))
        return self.in_tip

    def dispense(self, flow_rate: float, volume: float | None = None) -> float:
        """Dispense `volume` µl - all that is in the tip when None - at
        `flow_rate` µl/s; returns the volume dispensed.

        Refused with nothing in the tip, and for a part larger than what is
        in it: the plunger would push out air, and the count would go below
        nothing."""
        self._require_tip("dispense")
        held = self.in_tip
        amount = held if volume is None else float(volume)
        if held <= 0:
            raise SessionError("nothing is in the tip to dispense")
        if amount <= 0:
            raise SessionError("a dispense needs a volume above 0 µl")
        if amount > held + 1e-6:
            raise SessionError(f"the tip holds {held:g} µl, less than "
                               f"{amount:g} µl")
        moves.dispense(self.robot, amount, flow_rate)
        log.info("dispensed %g µl at %g µl/s", amount, flow_rate)
        self._set_in_tip(held - amount)
        return amount

    def _require_tip(self, what: str) -> None:
        self._require_robot()
        if self.tip.attached is not True:
            state = ("none" if self.tip.attached is False
                     else "that it cannot tell")
            raise SessionError(f"{what} needs a tip on the pipette, and the "
                               f"robot reports {state}")

    def _set_in_tip(self, ul: float) -> None:
        ul = round(max(0.0, float(ul)), 3)
        if ul == self.in_tip:
            return
        self.in_tip = ul
        self.in_tip_changed.emit(ul)

    # -- lights --------------------------------------------------------------

    def read_lights(self) -> bool | None:
        """The rail lights as the robot reports them. Blocking."""
        if self._api is None:
            raise SessionError("not connected")
        self.lights = lights_on(self._api)
        self.lights_changed.emit(self.lights)
        return self.lights

    def set_lights(self, on: bool) -> None:
        """Lights on or off, whatever they were. Blocking; moves nothing."""
        if self._api is None:
            raise SessionError("not connected")
        set_lights(self._api, bool(on))
        log.info("lights %s", "on" if on else "off")
        self.read_lights()

    def toggle_lights(self) -> None:
        """Flip the lights from what the robot reports, not from what the
        button last showed: a workflow may have switched them since."""
        state = self.read_lights()
        self.set_lights(not state)

    # -- stopping ------------------------------------------------------------

    def request_stop(self) -> None:
        """Ask every page to stop what it is running, at its next safe point:
        a picking or liquid run between two moves, a calibration sweep
        between two poses. The move in progress finishes. From the GUI
        thread; the pages own their runs."""
        log.warning("STOP pressed: stopping after the current move")
        self.stop_requested.emit()

    def hold_robot(self) -> None:
        """The second Stop: no command that moves is sent any more; the one
        in progress finishes. The robot session, its labware and the tip
        stay as they are. The robot server's only immediate stop ends the
        robot session itself, which loses all of that, so it is not used.
        `release_robot` lets commands through again."""
        if self._api is None:
            return
        self._stop_hold = True
        self._apply_hold()
        log.warning("robot held: no command until the jobs using it stop")

    def release_robot(self) -> None:
        """Let go of the second Stop's hold; a lock stays."""
        if not self._stop_hold:
            return
        self._stop_hold = False
        self._apply_hold()
        log.info("robot released")

    @property
    def robot_held(self) -> bool:
        return self._api is not None and self._api.hold.is_set()

    # -- lock ------------------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self.robot_lease.locked

    def set_locked(self, on: bool) -> bool:
        """Lock: nothing moves the robot until it is turned off, so the
        operator can work at the deck with their hands. The robot wrapper
        refuses every command that moves (as for the second Stop) and every
        claim of the lease is refused; the lights still switch. Enforced
        here, not only by the window's overlay. Refused (False) while a run
        has the robot: a run cut off mid-move by a lock is a run stopped
        nowhere safe, and Stop is the way to end one. A short job in
        progress finishes its command and has the rest refused."""
        on = bool(on)
        if on == self.locked:
            return True
        if on and self.robot_lease.holding_run:
            log.info("lock refused: %s has the robot",
                     self.robot_lease.holder)
            return False
        self.robot_lease.set_locked(on)
        self._apply_hold()
        log.warning("robot %s", "locked" if on else "unlocked")
        self.lock_changed.emit(on)
        return True

    def _apply_hold(self) -> None:
        """The wrapper's hold, from the lock and the second Stop together:
        letting go of one leaves the other."""
        if self._api is None:
            return
        if self.locked or self._stop_hold:
            self._api.hold.set()
        else:
            self._api.hold.clear()

    def refresh_lights(self) -> None:
        """Read the lights again after something switched them behind the
        session's back - a picking run darkens the bench and lights it again
        through the robot, the pipette calibration lights it. Never raises:
        a failed read must not fail the run that asked for it."""
        try:
            self.read_lights()
        except Exception as exc:                     # noqa: BLE001
            log.warning("could not read the lights: %s", exc)

    def _require_probe(self) -> RunState:
        if self._api is None or self.run_state is None:
            raise SessionError("probe the robot first")
        return self.run_state

    def _ready(self, origin: str) -> None:
        if origin == "new" and self._confirmed:
            # A new robot session: nothing confirmed in the last one holds.
            # Said here as well as by the id `is_confirmed` compares, for a
            # robot that hands out an id again (the mock keeps one).
            self._confirmed.clear()
            self.confirmations_changed.emit()
        self.robot = self._api
        self.motion.set_robot(self.robot)
        self.run_origin = origin
        self.released = None
        self.restarted = False
        self.run_generation += 1
        # Before anything is loaded through this session: the offset attaches
        # to labware at load time, so a module registered later is a module
        # the plates already on the deck know nothing about.
        self.register_deck_modules()
        self.robot_state_changed.emit(self.robot_state)
        # Asked, not assumed. A run carried on from before this application
        # started may have a tip on it, and that is the one to know about.
        self.read_tip()
        try:
            self.read_lights()
        except Exception as exc:                     # noqa: BLE001
            log.warning("could not read the lights: %s", exc)

    def connect_robot(self):
        """The whole sequence with the default decision made: carry on with a
        reusable run, otherwise start one - which homes. For callers with no
        operator to ask, such as a test; the window asks (Home and connect)."""
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
        self.motion.set_robot(None)
        self.run_state = self.run_origin = None
        self._forget_tip()
        self.lights = None
        self.lights_changed.emit(None)
        log.info("released the robot")
        self.robot_state_changed.emit(self.robot_state)

    def release_run(self) -> None:
        """Disconnect robot session (Settings, Advanced): let go of the
        robot and leave its session alone - the Opentrons app or a notebook
        can take it - until `continue_run`. Blocking; retracts Z, so it
        holds the robot's lease. The connection monitor would otherwise
        carry on with it at its next look."""
        state = self.run_state
        self.released = state.run_id if state is not None else None
        self.disconnect_robot()
        log.info("robot session %s let go", self.released)

    def continue_run(self) -> None:
        """Undo `release_run`: the monitor carries on with the session at
        its next look."""
        if self.released is None:
            return
        log.info("robot session %s to be continued", self.released)
        self.released = None
        self.robot_state_changed.emit(self.robot_state)

    def _retract(self) -> None:
        """Park the left Z before letting go, and never raise doing it."""
        try:
            self.robot.retract_axis("leftZ", verbose=False)
        except Exception as exc:                     # noqa: BLE001
            log.warning("could not retract leftZ: %s", exc)

    # -- cameras -------------------------------------------------------------

    def open_camera(self, label: str, resolution=None):
        """Open one of the profile's cameras. Blocking: run it in a Worker.

        `resolution` None takes the camera as it is, or opens it in the
        profile's mode. Given, a camera open in another mode is closed and
        opened again in that one: the picking run's clips want the lower
        camera fast, the pipette calibration wants it in the mode it was
        measured in."""
        if self.profile is None:
            raise SessionError("load a profile before opening a camera")
        existing = self._open.get(label)
        if existing is not None:
            if (resolution is None
                    or tuple(existing.resolution) == tuple(resolution)):
                return existing
            log.info("camera %r: reopening %dx%d -> %dx%d", label,
                     *existing.resolution, *resolution)
            self.close_camera(label)

        if self.mock:
            camera = self._open_mock_camera(label, resolution)
        else:
            if self.cameras is None:
                raise SessionError("no camera manager; load a profile first")
            camera = self.cameras.open(
                label, resolution=tuple(resolution) if resolution else None,
                verbose=False)

        self._open[label] = camera
        log.info("camera %r open at %dx%d%s", label, *camera.resolution,
                 f", view crop {camera.crop:g}" if camera.crop != 1.0 else "")
        self.camera_opened.emit(label)
        return camera

    def _open_mock_camera(self, label: str, resolution=None):
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
                f"no camera labeled {label!r} in profile {self.profile.name!r}; "
                f"known: {', '.join(sorted(self.profile.cameras)) or 'none'}")
        width, height = resolution or spec.default_resolution
        scene = MarkerScene(image_size=(width, height))
        camera = open_scene_camera(self.robot, scene, label=label)
        # crop is a view property carried by the camera, applied where a person
        # looks and nowhere else. The mock keeps the same contract.
        camera.crop = float(spec.crop)
        if spec.controls:
            camera.set_controls(dict(spec.controls))
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

    def assign_camera_devices(self, devices: dict[str, str]) -> None:
        """{label: device name}: which attached camera each of the
        profile's cameras is, written into the profile at once. A camera
        that is open is closed first - it is another device now - and the
        page that wants it opens it again. Blocking only on disk."""
        if self.profile is None:
            raise SessionError("load a profile before choosing its cameras")
        changed = {}
        for label, name in devices.items():
            spec = self.profile.cameras.get(label)
            if spec is None:
                raise SessionError(f"profile {self.profile.name!r} has no "
                                   f"camera {label!r}")
            if spec.device_name != name:
                changed[label] = (spec.device_name, name)
        if not changed:
            return
        for label in changed:
            if label in self._open:
                self.close_camera(label)
        for label, (_was, name) in changed.items():
            self.profile.cameras[label].device_name = name
            if self.cameras is not None:
                # In place: the manager holds the other camera open, and a
                # new one would not know to close it.
                self.cameras.cameras[label] = \
                    self.profile.cameras[label].model_dump()
        self.profile.save_cameras()
        for label, (was, name) in changed.items():
            log.info("camera %r of profile %r is now %r (was %r)", label,
                     self.profile.name, name, was)
        self.profile_changed.emit(self.profile)

    def add_camera(self, role: str, device_name: str,
                   sizes: list[tuple[int, int]]) -> str:
        """Give the profile its upper or lower camera, which it does not
        have yet - a profile started empty has none. The spec is the bench's
        (`new_camera_spec`) on the device chosen; `sizes` are what the
        device offers, so the modes listed are ones it can open in. Returns
        the new camera's label."""
        if self.profile is None:
            raise SessionError("load a profile before choosing its cameras")
        label, spec = new_camera_spec(role, device_name, sizes)
        n = 2
        base = label
        while label in self.profile.cameras:
            label, n = f"{base}_{n}", n + 1
        self.profile.cameras[label] = spec
        if self.cameras is not None:
            self.cameras.cameras[label] = spec.model_dump()
        self.profile.save_cameras()
        if role == "upper":
            # The pixel map belongs to this camera: said, not guessed from
            # the label (`upper_camera_label`).
            self.profile.meta.camera_label = label
            self.profile.save_meta()
        log.info("profile %r: %s camera %r is %r, opening at %dx%d",
                 self.profile.name, role, label, device_name,
                 *spec.default_resolution)
        self.profile_changed.emit(self.profile)
        return label

    def attached_cameras(self) -> list[str]:
        """The names of the cameras attached to this computer, in index
        order; under --mock, the mock ones. Raises DeviceError when the
        system cannot list them."""
        if self.mock:
            return [spec.device_name for spec in _mock_cameras().values()]
        return [device.name for device in camera_devices.list_devices()]

    def camera_device(self, role: str,
                      attached: list[str] | None = None) -> str | None:
        """The device the profile's `role` camera is saved on, or None
        while the profile has none for it. Given the `attached` cameras,
        the one of them it opens as when there is one (a saved name can
        be a vendor's shorter one, `find_device`)."""
        label = self._role_label(role)
        if label is None:
            return None
        return ((attached is not None and self._attached_as(label, attached))
                or self.profile.cameras[label].device_name)

    def choose_camera(self, role: str, device_name: str) -> str | None:
        """Make the attached camera `device_name` the profile's `role`
        camera (Robot setup's camera sheets). The other role's device is
        swapped in, and that device is returned; a profile without a
        camera for `role` gets one (`add_camera`). Refused while anything
        has the robot: a run reads the cameras."""
        if self.profile is None:
            raise SessionError("load a profile before choosing its cameras")
        if self.robot_lease.busy:
            raise SessionError(f"Robot busy: {self.robot_lease.holder}.")
        names = self.attached_cameras()
        label = self._role_label(role)
        other = self._role_label(OTHER_ROLE[role])
        others_device = other is not None and \
            self._attached_as(other, names) == device_name
        if label is None:
            if others_device:
                raise SessionError(f"{device_name} is already the "
                                   f"{OTHER_ROLE[role]} camera.")
            self.add_camera(role, device_name, self._device_sizes(device_name))
            return None
        current = self.camera_device(role, names)
        if current == device_name:
            return None
        assignment = {label: device_name}
        if others_device and other != label:
            assignment[other] = current
        self.assign_camera_devices(assignment)
        return current if len(assignment) > 1 else None

    def save_camera_focus(self, role: str, value: float) -> bool:
        """Write `value` as the focus of the profile's `role` camera (the
        camera sheet's Done), autofocus off with it: a focus means nothing
        while the device focuses by itself. Returns whether anything
        changed; an equal focus writes nothing."""
        if self.profile is None:
            raise SessionError("load a profile before saving a focus")
        label = self._role_label(role)
        if label is None:
            raise SessionError(f"profile {self.profile.name!r} has no "
                               f"{role} camera")
        spec = self.profile.cameras[label]
        controls = dict(spec.controls)
        controls["focus"] = float(value)
        if not isinstance(controls.get("autofocus"), str):
            controls["autofocus"] = 0.0
        if controls == spec.controls:
            return False
        spec.controls = controls
        if self.cameras is not None and label in self.cameras.cameras:
            self.cameras.cameras[label] = spec.model_dump()
        self.profile.save_cameras()
        log.info("camera %r focus saved to profile %r: %g", label,
                 self.profile.name, float(value))
        self.profile_changed.emit(self.profile)
        return True

    def preview_camera(self, role: str, device_name: str):
        """The attached camera `device_name`, open to be looked at before
        it is chosen as the `role` camera. Blocking: run it in a Worker.

        A device is opened once at a time: the profile's camera on it is
        handed back as `open_camera` gives it (and stays open); any other
        is opened as a camera of its own, the bench's `role` spec on it,
        open until `close_preview` or the next preview."""
        if self.profile is None:
            raise SessionError("load a profile before opening a camera")
        self.close_preview()
        names = self.attached_cameras()
        for label in self.profile.cameras:
            if self._attached_as(label, names) == device_name:
                return self.open_camera(label)
        if self.mock:
            if self.robot is None:
                raise SessionError(
                    f"connect the robot before opening {device_name!r}: the "
                    f"synthetic scene is rendered from the gantry pose")
            spec = _mock_camera(device_name)
            camera = open_scene_camera(
                self.robot, MarkerScene(image_size=spec.default_resolution),
                label=PREVIEW_LABEL)
            if spec.controls:
                camera.set_controls(dict(spec.controls))
        else:
            _label, spec = new_camera_spec(role, device_name,
                                           self._device_sizes(device_name))
            manager = CameraManager({PREVIEW_LABEL: spec.model_dump()})
            camera = manager.open(PREVIEW_LABEL, verbose=False)
        camera.crop = float(spec.crop)
        with self._preview_lock:
            self._preview = camera
        log.info("camera %r open to be looked at, %dx%d", device_name,
                 *camera.resolution)
        return camera

    def close_preview(self) -> None:
        """Close the camera `preview_camera` opened of its own, if any."""
        with self._preview_lock:
            camera, self._preview = self._preview, None
        if camera is not None:
            camera.close()

    def _role_label(self, role: str) -> str | None:
        return (self.upper_camera_label if role == "upper"
                else self.lower_camera_label)

    def _attached_as(self, label: str, names: list[str]) -> str | None:
        """The attached camera among `names` the profile's camera `label`
        resolves to, as it would at open, or None (none, or several)."""
        attached = [camera_devices.Device(i, n) for i, n in enumerate(names)]
        try:
            return camera_devices.find_device(
                self.profile.cameras[label].device_name,
                devices=attached).name
        except camera_devices.DeviceError:
            return None

    def _device_sizes(self, device_name: str) -> list[tuple[int, int]]:
        """The sizes the attached camera offers; none when unknown."""
        if self.mock:
            return [tuple(size)
                    for size in _mock_camera(device_name).resolutions]
        try:
            device = camera_devices.find_device(device_name)
        except camera_devices.DeviceError:
            return []
        return [(m.width, m.height)
                for m in camera_devices.device_modes(device.index)]

    def reset_setup(self) -> Path:
        """Reset setup (REDESIGN §12.8): the profile's folder zipped beside
        it, dated, then its cameras, camera calibration, platforms and the
        three Robot setup positions cleared (`store.Profile.reset_setup`).
        The deck and the picking settings are kept. Refused while anything
        has the robot: a run reads the positions and the cameras it would
        lose. Returns the archive."""
        if self.profile is None:
            raise SessionError("no profile is loaded")
        if self.robot_lease.busy:
            raise SessionError(f"Robot busy: {self.robot_lease.holder}.")
        for label in list(self._open):
            self.close_camera(label)
        archive = self.profile.reset_setup(SETUP_POSITIONS.values())
        self.cameras = CameraManager.from_profile(self.profile)
        log.warning("robot setup of profile %r reset; the profile before it "
                    "is in %s", self.profile.name, archive)
        if self._api is not None:
            self.register_deck_modules()
        self.profile_changed.emit(self.profile)
        if self.run_state is not None:
            self.labware_changed.emit(self.run_state)
        return archive

    # -- teardown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Leave nothing running. Called from the window's closeEvent, once
        the robot is free (`MainWindow.closeEvent`).

        Cameras first: each holds a grab thread, and a thread still calling
        read() while the interpreter tears down is the one way this exits
        badly. Then the axis is parked and the robot is let go.
        """
        for label in list(self._open):
            try:
                self.close_camera(label)
            except Exception as exc:                 # noqa: BLE001
                log.warning("could not close camera %r: %s", label, exc)
        self.close_preview()
        if self.robot is not None:
            self._retract()
        self._api = self.robot = None
        self.motion.set_robot(None)
        self.run_state = self.run_origin = None
        self._forget_tip()
        self.lights = None
        self.lights_changed.emit(None)
        self.robot_state_changed.emit(self.robot_state)
        log.info("session closed")


def _skipped_plate(slot, load_name: str) -> str:
    """The confirmation that the plate in `slot` is skipped (GLOSSARY,
    Skipped plate)."""
    return f"plate-skipped:{int(slot)}:{load_name}"
