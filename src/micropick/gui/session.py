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
from datetime import datetime
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
from ..config.labware import (LabwareDefinition,       # noqa: E402
                              LabwareError, resolve_definition)
from ..config.schema import (Calibration, CameraSpec,  # noqa: E402
                             DeckConfig, DeckModule, ModuleType,
                             PickingConfig, WellCentre,
                             ProfileMeta)
from ..hardware import labware                      # noqa: E402
from ..hardware import robot_clock                  # noqa: E402
from ..hardware.camera import CameraManager         # noqa: E402
from ..hardware.labware import LoadedLabware        # noqa: E402
from ..hardware.mock import (MarkerScene, MockRobot,  # noqa: E402
                             open_scene_camera)
from ..hardware import tips                          # noqa: E402
from ..hardware.protocols import (lights_on, require_ok,  # noqa: E402
                                  set_lights)
from ..workflows.jog import Limits                  # noqa: E402
from .widgets.deck_view import (SLOT_HEIGHT, SLOT_WIDTH,  # noqa: E402
                                slot_centre)
from .detector import DetectorService              # noqa: E402

__all__ = ["Session", "SessionError", "RunState", "Tip", "DeckProblem",
           "MOCK_PROFILE_NAME", "REUSABLE_STATUSES"]

log = logging.getLogger(__name__)

MOCK_PROFILE_NAME = "mock"

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

# A module may stand a little past its slot opening; the raised floor over
# it reaches this much further on every side.
MODULE_MARGIN_MM = 3.0


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
    slot; a tip from labware since moved off the deck has neither.
    """

    attached: bool | None
    slot: str | None = None
    well: str | None = None

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
    # The rail lights, as last read from the robot; carries bool or None.
    lights_changed = Signal(object)
    # The routine a run would deliver into, or None. Set by the Plate plan page
    # and read by the Picking page: the two never see each other, and the
    # session is what a session is attached to.
    routine_changed = Signal(object)
    camera_opened = Signal(str)
    camera_closed = Signal(str)
    # The application's settings were saved; carries the AppSettings.
    settings_changed = Signal(object)
    error = Signal(str)
    # Stop was pressed (the status bar's button, or Esc): every page stops
    # what it runs at its next safe point. See `request_stop`.
    stop_requested = Signal()

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
        self.routine = None                      # see routine_changed
        self.tip = Tip.unknown()                 # see tip_changed
        self.lights: bool | None = None          # see lights_changed
        self.cameras: CameraManager | None = None
        self._open: dict[str, object] = {}
        # The cuboid detector, one for the application: Picking and Manual
        # control both run it, and the weights are seconds to load and
        # hundreds of megabytes to hold twice.
        self.detector = DetectorService(self)
        # This computer's settings (config.app_settings): the robot's address
        # and where outputs go. Applied at once, so every writer asks paths
        # for the right folder from the first file.
        self.settings, self.settings_note = app_settings.load_settings()
        app_settings.apply(self.settings)
        if self.settings_note:
            log.error("%s", self.settings_note)

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
        """Probed or connected: the robot client exists, so its address is
        fixed until Disconnect."""
        return self._api is not None

    @property
    def options_host(self) -> str | None:
        return getattr(self.options, "robot_host", None)

    def set_settings(self, settings) -> None:
        """Save the application's settings and apply them. The robot's
        address is used at the next Connect."""
        app_settings.save_settings(settings)
        app_settings.apply(settings)
        self.settings, self.settings_note = settings, ""
        log.info("settings saved to %s", app_settings.settings_path())
        self.settings_changed.emit(settings)

    @property
    def open_cameras(self) -> list[str]:
        return sorted(self._open)

    @property
    def jog_limits(self) -> Limits:
        # The profile schema has no limits section. When it grows one, this is
        # the only place that changes; the manual page asks the session and
        # knows nothing about where the numbers came from.
        #
        # Each deck module raises the floor over its slot to its own top, so
        # a jog or a click cannot drive the tip into it. The labware it
        # carries sits above that top, so nothing a step needs is below it.
        floors = []
        modules = self.profile.deck.modules if self.profile is not None else []
        for module in modules:
            for slot in module.slots:
                cx, cy = slot_centre(slot)
                half_x = SLOT_WIDTH / 2 + MODULE_MARGIN_MM
                half_y = SLOT_HEIGHT / 2 + MODULE_MARGIN_MM
                name = f"{module.name or 'module'} in slot {slot}"
                floors.append((cx - half_x, cy - half_y, cx + half_x,
                               cy + half_y, float(module.height_mm), name))
        return Limits(x=(0.0, 380.0), y=(0.0, 350.0), z=(0.1, 150.0),
                      floors=tuple(floors))

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
        up from it (`PickingConfig.pickup_height`)."""
        if self.profile is None:
            raise SessionError("no profile is loaded, so there is nowhere to "
                               "save the dish bottom")
        before = self.profile.picking.dish_bottom
        self.profile.picking = self.profile.picking.model_copy(
            update={"dish_bottom": round(float(z), 2)})
        self.profile.save_picking()
        log.info("dish bottom %.2f -> %.2f mm in profile %r", before,
                 self.profile.picking.dish_bottom, self.profile.name)
        self.profile_changed.emit(self.profile)

    def forget(self, name: str) -> None:
        """Drop a named position from the profile."""
        if self.profile is None:
            raise SessionError("no profile is loaded")
        self.profile.forget(name)
        log.info("forgot position %r in profile %r", name, self.profile.name)
        self.profile_changed.emit(self.profile)

    def rename_position(self, old: str, new: str) -> None:
        if self.profile is None:
            raise SessionError("no profile is loaded")
        self.profile.rename(old, new)
        log.info("renamed position %r to %r in profile %r", old, new,
                 self.profile.name)
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
                host = self.options_host or self.settings.robot_host
                self._api = ot2_api.OpentronsAPI(
                    host=host,
                    port=None if host and ":" in host
                    else self.settings.robot_port)
                log.info("connecting to the robot at %s", self._api.BASE_URL)
                # Before the run is read: a session created from now on is
                # stamped right (`hardware.robot_clock`). Never raises.
                log.info("%s", robot_clock.sync_clock(self._api))
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
                                       well_centres=list(deck.well_centres))
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
                                       well_centres=list(deck.well_centres))
        self.profile.save_deck()
        log.info("module types in profile %r: %s", self.profile.name,
                 ", ".join(f"{t.name} {t.height_mm:g} mm" for t in types)
                 or "none")
        self.profile_changed.emit(self.profile)

    def set_well_centre(self, slot, load_name: str, offset, well: str,
                        depth_mm: float | None = None) -> None:
        """Save where the wells of the plate in `slot` really are (see
        `WellCentre`), replacing any earlier measurement of it. The depth
        set for it before is kept; `depth_mm` is used when there was none."""
        if self.profile is None:
            raise SessionError("no profile to save the well centre into")
        deck = self.profile.deck
        before = deck.well_centre(slot, load_name)
        if before is not None and before.depth_mm is not None:
            depth_mm = before.depth_mm
        centre = WellCentre(slot=int(slot), load_name=load_name,
                            offset=[round(float(v), 3) for v in offset],
                            well=well, measured_at=datetime.now(),
                            depth_mm=depth_mm)
        deck.well_centres = [c for c in deck.well_centres
                             if (c.slot, c.load_name) != (centre.slot,
                                                          load_name)]
        deck.well_centres.append(centre)
        self.profile.save_deck()
        log.info("well centre of %s in slot %s: %s", load_name, slot,
                 centre.describe())
        self.profile_changed.emit(self.profile)

    def set_well_depth(self, slot, load_name: str, depth_mm: float) -> None:
        """The depth of the measured plate's wells, for its measured
        bottom."""
        centre = (self.profile.deck.well_centre(slot, load_name)
                  if self.profile is not None else None)
        if centre is None:
            raise SessionError("measure the well centre first")
        depth = round(float(depth_mm), 3)
        if centre.depth_mm == depth:
            return
        centre.depth_mm = depth
        self.profile.save_deck()
        log.info("well depth of %s in slot %s: %g mm", load_name, slot, depth)
        self.profile_changed.emit(self.profile)

    def forget_well_centre(self, slot, load_name: str) -> None:
        if self.profile is None:
            return
        deck = self.profile.deck
        kept = [c for c in deck.well_centres
                if (c.slot, c.load_name) != (int(slot), load_name)]
        if len(kept) == len(deck.well_centres):
            return
        deck.well_centres = kept
        self.profile.save_deck()
        log.info("well centre of %s in slot %s forgotten", load_name, slot)
        self.profile_changed.emit(self.profile)

    def remember_labware(self, load_name: str) -> None:
        """Note a load name as just used, for the Robot & Deck page's
        Recently used. Saved, but announced to nobody: only that list
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
            state = (tips.TipState(True, held[0], held[1]) if held
                     else tips.TipState(False))
        else:
            try:
                state = tips.tip_state(self._api, self.run_state.run_id)
            except Exception as exc:                 # noqa: BLE001
                log.warning("could not read the tip state: %s", exc)
                state = tips.TipState.unknown()
        slot = well = None
        if state.attached and state.labware_id:
            for entry in (self.run_state.labware if self.run_state else {}).values():
                if entry.labware_id == state.labware_id:
                    slot, well = entry.slot, state.well
        tip = Tip(state.attached, slot, well)
        if tip != self.tip:
            log.info("robot reports: %s", tip.describe())
        self.tip = tip
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
        finally:
            self.read_tip()

    def _labware_in(self, slot) -> LoadedLabware:
        self._require_robot()
        entry = self.run_state.labware.get(str(slot)) if self.run_state else None
        if entry is None:
            raise SessionError(f"the robot session holds nothing in slot "
                               f"{slot}")
        return entry

    def _forget_tip(self) -> None:
        self.tip = Tip.unknown()
        self.tip_changed.emit(self.tip)

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

    def halt_robot(self) -> None:
        """Stop the robot where it is, at once. Blocking (one request).

        The robot server's only immediate stop is to stop the robot session
        itself (POST .../actions stop): the move in progress is cut short,
        and the session takes no commands afterwards. So the robot is let go
        of here as far as the pages are concerned - they grey out - while
        the connection stays, and the Profile page offers New robot session
        + home, which lifts the tip and homes. The labware has to be loaded
        again into the new session."""
        if self._api is None:
            raise SessionError("not connected")
        if self.mock:
            log.warning("robot halted (mock: nothing to cut short)")
        else:
            self._api.control_run("stop")
            log.warning("robot halted: robot session %s stopped",
                        getattr(self._api, "run_id", "?"))
        try:
            self.run_state = _run_state(self._api)
        except Exception as exc:                     # noqa: BLE001
            log.warning("could not read the robot session after the stop: %s",
                        exc)
        if self.mock and self.run_state is not None:
            # The mock's run never ends by itself; say what a robot would.
            self.run_state.status = "stopped"
        self.robot = None
        self.run_origin = None
        self._forget_tip()
        self.robot_state_changed.emit(self.robot_state)

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
        self.robot = self._api
        self.run_origin = origin
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
        self._forget_tip()
        self.lights = None
        self.lights_changed.emit(None)
        log.info("released the robot")
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
                f"no camera labelled {label!r} in profile {self.profile.name!r}; "
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

    def save_camera_controls(self, label: str) -> dict[str, float]:
        """Write the open camera's live control values into the profile.

        Only controls the profile already names are written, and only the
        numeric ones: `auto_exposure` is a word in the profile and a number
        on the device, and a focus tuned on the feed is exactly the value
        that should survive the next open. Returns what was written.
        """
        if self.profile is None:
            raise SessionError("no profile to save camera controls into")
        camera = self._open.get(label)
        if camera is None:
            raise SessionError(f"camera {label!r} is not open")
        spec = self.profile.cameras.get(label)
        if spec is None:
            raise SessionError(f"profile {self.profile.name!r} has no camera "
                               f"{label!r}")
        written: dict[str, float] = {}
        for name, value in camera.controls.applied.items():
            if name in spec.controls and not isinstance(spec.controls[name], str):
                spec.controls[name] = float(value)
                written[name] = float(value)
        if written:
            self.profile.save_cameras()
            log.info("camera %r controls saved to profile %r: %s", label,
                     self.profile.name,
                     ", ".join(f"{k}={v:g}" for k, v in written.items()))
            self.profile_changed.emit(self.profile)
        return written

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
        self._forget_tip()
        self.lights = None
        self.lights_changed.emit(None)
        self.robot_state_changed.emit(self.robot_state)
        log.info("session closed")
