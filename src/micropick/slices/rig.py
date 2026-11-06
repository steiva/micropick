"""The robot and the microscope, for Slice picking. No Qt: blocking calls, run
by the window on a worker under the robot's lease.

What `gui.session` is to the main program, cut down to one page. Moves go
through `workflows.motion.Motion`, and a click through `Motion.slide_to`
after `calibration.click_refusal`. A robot session is carried on with when
the robot has one that can be; a new one homes, so it waits for the
operator (`new_session`).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from ..config.labware import LabwareError, resolve_definition
from ..config.schema import DeckModule
from ..hardware import labware, robot_clock, tips
from ..hardware.camera import CameraManager
from ..hardware.protocols import prepare_to_aspirate, require_ok, xyz
from ..hardware.robot_lease import RobotLease
from ..workflows import manual as moves
from ..workflows.jog import JogController, Reached
from ..workflows.motion import Motion
from . import calibration
from .calibration import CalibrationRefused, PixelToRobot
from .store import SliceState, save_state

__all__ = ["Mirrored", "Rig", "SliceRefused", "Tip", "RESOLUTION",
           "open_microscope"]

log = logging.getLogger(__name__)

RESOLUTION = (1600, 1200)
# A robot session that can take commands.
REUSABLE_STATUSES = ("idle", "running", "paused")
BLOW_OUT_RATE = 50.0
RETURN_ABOVE_MM = 10.0
DEFAULT_TIP_UL = 300.0
# The notebook's wait after a move: at 4 µm a pixel the arm is still
# settling when the move ends.
SETTLE_S = 1.0
FRAMES_AFTER_SETTLING = 2


class SliceRefused(RuntimeError):
    """Why something was not done, as a sentence for the operator."""


@dataclass(frozen=True)
class Tip:
    """As `gui.session.Tip`: `attached` None is "the robot's record could
    not be read", which is never "no tip"."""

    attached: bool | None
    slot: str | None = None
    well: str | None = None
    pickup: str | None = None
    length: float | None = None


class Mirrored:
    """A camera whose frames are mirrored left to right, as the microscope
    is shown and calibrated. One frame flipped once: `CameraView` knows a
    new frame by its being another object."""

    crop = 1.0

    def __init__(self, camera):
        self._camera = camera
        self.label = camera.label
        self._source = None
        self._flipped = None

    @property
    def resolution(self) -> tuple[int, int]:
        return self._camera.resolution

    @property
    def lost(self):
        return self._camera.lost

    def _mirror(self, frame):
        if frame is not self._source:
            self._source, self._flipped = frame, np.ascontiguousarray(
                frame[:, ::-1])
        return self._flipped

    def read(self):
        ok, frame = self._camera.read()
        return (True, self._mirror(frame)) if ok else (False, None)

    def read_after(self, t: float, *, skip: int = 0, timeout: float = 2.0):
        frame = self._camera.read_after(t, skip=skip, timeout=timeout)
        return np.ascontiguousarray(frame[:, ::-1])

    def close(self) -> None:
        self._camera.close()


def open_microscope(device_name: str) -> Mirrored:
    """The device, in the mode the map is measured in. Blocking. No fourcc:
    as the notebook opened it."""
    manager = CameraManager({"microscope": {
        "device_name": device_name, "default_resolution": list(RESOLUTION),
        "fourcc": None}})
    return Mirrored(manager.open("microscope", verbose=False))


class Rig:
    """See the module docstring."""

    def __init__(self, state: SliceState, *, api=None, host=None, port=31950,
                 mock: bool = False):
        self.state = state
        self.mock = mock
        self._host, self._port = host, port
        self.api = api
        self.motion = Motion(modules=self.platforms)
        self.lease = RobotLease()
        self.jog: JogController | None = None
        self.ready = False
        self.run_id: str | None = None
        self.labware: dict = {}
        self.tip = Tip(attached=None)
        self.aspirated_at: tuple | None = None
        self._blown_out = False
        self._lock = threading.Lock()

    # -- the robot session ---------------------------------------------------

    def _client(self):
        if self.api is None:
            from opentrons_api import ot2_api
            self.api = ot2_api.OpentronsAPI(
                host=self._host,
                port=None if self._host and ":" in self._host else self._port)
            log.info("connecting to the robot at %s", self.api.BASE_URL)
        return self.api

    def _current_run(self) -> dict | None:
        data = json.loads(self._client().get_all_runs().text)["data"]
        return next((run for run in data if run.get("current")), None)

    def look(self) -> str:
        """"ready" or "needs home", carrying on with a session that can be.
        Moves nothing. Raises when the robot does not answer."""
        run = self._current_run()
        usable = run is not None and run.get("status") in REUSABLE_STATUSES
        if self.ready and (not usable or run.get("id") != self.run_id):
            log.info("the robot's session changed: starting over")
            self.forget()
        if self.ready:
            return "ready"
        if not usable or self.lease.busy:
            return "needs home"
        if not self.mock:
            self.api.get_run_info()
            labware.ensure_definitions(self.api, verbose=False)
            if not run.get("pipettes"):
                self.api.load_pipette()
        self._start(run)
        return "ready"

    def new_session(self) -> Reached:
        """A new robot session, homed, with the deck of the last one.
        Blocking; **moves the gantry**."""
        api = self._client()
        if self.mock:
            api.home_robot()
        else:
            log.info("%s", robot_clock.sync_clock(api))
            api.create_run(verbose=False)
            labware.ensure_definitions(api, verbose=False)
            api.load_pipette(verbose=False)
            api.home_robot(verbose=False)
        self.forget()
        self._start(self._current_run())
        wanted = dict(self.state.deck)
        for slot, load_name in wanted.items():
            if slot not in self.labware:
                try:
                    self.load(load_name, int(slot))
                except (LabwareError, SliceRefused) as exc:
                    log.error("slot %s: %s", slot, exc)
        return Reached(xyz(api), "Robot homed.")

    def _start(self, run: dict) -> None:
        self.run_id = run.get("id")
        self.motion.set_robot(self.api)
        self.jog = JogController(self.api, limits=self.motion.envelope)
        self._register_platforms()
        self.labware = labware.loaded_labware(self.api)
        self.read_tip()
        self.ready = True
        log.info("robot session %s: %s", self.run_id,
                 ", ".join(f"slot {s} {e.load_name}"
                           for s, e in sorted(self.labware.items())) or "empty")

    def forget(self) -> None:
        self.ready = False
        self.run_id = None
        self.jog = None
        self.labware = {}
        self.tip = Tip(attached=None)
        self.aspirated_at = None

    def pose(self) -> tuple[float, float, float]:
        return xyz(self.api)

    def _reached(self, said: str | None = None) -> Reached:
        return Reached(self.pose(), said)

    # -- platforms and labware -----------------------------------------------

    def platforms(self) -> list[DeckModule]:
        slots = self.state.platform_slots
        height = self.state.platform_height_mm
        if not slots or height <= 0:
            return []
        return [DeckModule(slots=list(slots), offset=[0.0, 0.0, height],
                           name="Platform")]

    def _register_platforms(self) -> None:
        # Replaced, not added to: the wrapper refuses a slot it already has.
        self.api.slot_offsets = {"data": []}
        for module in self.platforms():
            self.api.add_slot_offsets(list(module.slots), tuple(module.offset))
        if self.jog is not None:
            self.jog.limits = self.motion.envelope

    def set_platforms(self, slots: list[int], height_mm: float) -> None:
        """A platform's height attaches to labware as it is loaded, so what
        stands in a slot that changed is loaded again. Moves nothing."""
        changed = set(slots) ^ set(self.state.platform_slots)
        if height_mm != self.state.platform_height_mm:
            changed |= set(slots) | set(self.state.platform_slots)
        self.state.platform_slots = sorted(slots)
        self.state.platform_height_mm = float(height_mm)
        self._save()
        self._register_platforms()
        for slot in sorted(changed):
            entry = self.labware.get(str(slot))
            if entry is not None:
                self.load(entry.load_name, slot)

    def _rack_in_use(self, slot: int) -> None:
        if self.tip.attached and self.tip.slot == str(slot):
            raise SliceRefused(f"The tip on came from slot {slot}: drop it "
                               f"first.")

    def load(self, load_name: str, slot: int) -> None:
        """Tell the robot session what stands in `slot`, replacing what it
        held. Moves nothing."""
        if not 1 <= slot <= 11:
            raise SliceRefused(f"Slot {slot} takes no labware.")
        self._rack_in_use(slot)
        definition = resolve_definition(load_name)
        current = self.labware.get(str(slot))
        if current is not None:
            self.api.move_labware(current.labware_id, "offDeck", verbose=False)
        if definition.source == "local" and not self.mock:
            labware.upload_definition(self.api, definition)
        self._register_platforms()
        labware.load_labware(self.api, load_name, slot,
                             namespace=definition.namespace,
                             version=definition.version, verbose=False)
        self.labware = labware.loaded_labware(self.api)
        entry = self.labware.get(str(slot))
        if entry is None:
            raise SliceRefused(f"The robot does not report {load_name} in "
                               f"slot {slot}.")
        if slot in self.state.platform_slots and (
                entry.offset is None or abs(
                    entry.offset[2] - self.state.platform_height_mm) > 1e-6):
            raise SliceRefused(f"The platform's height did not attach to "
                               f"{load_name} in slot {slot}: do not use it.")
        self.state.deck[str(slot)] = load_name
        self._save()
        log.info("loaded %s into slot %d", load_name, slot)

    def unload(self, slot: int) -> None:
        self._rack_in_use(slot)
        current = self.labware.get(str(slot))
        if current is not None:
            self.api.move_labware(current.labware_id, "offDeck", verbose=False)
        self.labware = labware.loaded_labware(self.api)
        self.state.deck.pop(str(slot), None)
        self._save()

    def tip_racks(self) -> list:
        """(slot, entry) of each tip rack on the deck."""
        return [(slot, entry) for slot, entry in sorted(self.labware.items())
                if resolve_definition(entry.load_name).is_tiprack]

    def plates(self) -> list:
        """As `Session.plates`: (slot, entry, definition) of all but racks."""
        held = ((slot, entry, resolve_definition(entry.load_name))
                for slot, entry in sorted(self.labware.items()))
        return [item for item in held if not item[2].is_tiprack]

    # -- the tip -------------------------------------------------------------

    def read_tip(self) -> Tip:
        """From the robot's command log, as `Session.read_tip`."""
        if self.mock:
            held = self.api.tip
            count = sum(1 for call in self.api.calls
                        if call[0] == "pick_up_tip")
            state = (tips.TipState(True, held[0], held[1], f"mock-{count}")
                     if held else tips.TipState(False))
        else:
            try:
                state = tips.tip_state(self.api, self.run_id)
            except Exception as exc:                 # noqa: BLE001
                log.warning("could not read the tip state: %s", exc)
                state = tips.TipState.unknown()
        slot = well = None
        length = state.length
        if state.attached and state.labware_id:
            for entry in self.labware.values():
                if entry.labware_id == state.labware_id:
                    slot, well = entry.slot, state.well
                    if length is None:
                        length = tips.nominal_tip_length(entry.load_name)
        self.tip = Tip(state.attached, slot, well, state.pickup, length)
        # A record not read says nothing of the count.
        if state.attached is not None:
            if not state.attached or state.pickup != self.state.tip_pickup:
                self.state.in_tip_ul = 0.0
                self.aspirated_at = None
            self.state.tip_pickup = state.pickup if state.attached else None
            self._save()
        self.motion.tip_changed(self.tip)
        if self.jog is not None:
            self.jog.limits = self.motion.envelope
        return self.tip

    @property
    def tip_capacity(self) -> float:
        """What the tip on holds, by its rack; 300 µl when not known."""
        entry = self.labware.get(self.tip.slot or "")
        if entry is None:
            return DEFAULT_TIP_UL
        wells = resolve_definition(entry.load_name).data.get("wells", {})
        volume = next(iter(wells.values()), {}).get("totalLiquidVolume")
        return float(volume) if volume else DEFAULT_TIP_UL

    def pick_up(self, slot: str, well: str) -> Reached:
        """Refused over a tip, or a tip state not known: a second tip
        pressed onto the first is a crash into the rack."""
        if self.read_tip().attached is not False:
            raise SliceRefused("Drop the tip first." if self.tip.attached
                               else "The tip state cannot be read.")
        entry = self.labware.get(str(slot))
        if entry is None or not resolve_definition(entry.load_name).is_tiprack:
            raise SliceRefused(f"Slot {slot} holds no tip rack.")
        if well not in resolve_definition(entry.load_name).wells:
            raise SliceRefused(f"The rack has no tip {well}.")
        self.motion.raise_tip()
        try:
            require_ok(self.api.pick_up_tip(entry.labware_id, well,
                                            verbose=False),
                       f"pick up tip {well}")
        finally:
            self.read_tip()
        self.state.tip_well = well
        self._save()
        return self._reached(f"Tip {well} is on.")

    def drop_in_place(self) -> Reached:
        try:
            require_ok(self.api.drop_tip_in_place(verbose=False), "drop tip")
        finally:
            self.read_tip()
        return self._reached("Tip dropped.")

    def drop_in_trash(self) -> Reached:
        self.motion.raise_tip()
        try:
            if self.mock:
                self.api.move_to_trash()
                self.api.drop_tip_in_place()
            else:
                tips.drop_tip_in_trash(self.api)
        finally:
            self.read_tip()
        return self._reached("Tip dropped in the trash.")

    # -- liquid --------------------------------------------------------------

    def _need_tip(self) -> None:
        if self.tip.attached is not True:
            raise SliceRefused("Put a tip on first.")

    def aspirate(self, volume: float, flow_rate: float) -> Reached:
        self._need_tip()
        held = self.state.in_tip_ul
        room = self.tip_capacity - held
        if volume > room + 1e-6:
            raise SliceRefused(f"Only {room:g} µl more fit in the tip.")
        if self._blown_out:
            prepare_to_aspirate(self.api)
            self._blown_out = False
        moves.aspirate(self.api, volume, flow_rate)
        self._set_in_tip(held + volume)
        self.aspirated_at = self.pose()
        return Reached(self.aspirated_at, f"Aspirated {volume:g} µl.")

    def dispense(self, volume: float, flow_rate: float) -> Reached:
        self._need_tip()
        held = self.state.in_tip_ul
        if held <= 0:
            raise SliceRefused("The tip is empty.")
        if volume > held + 1e-6:
            raise SliceRefused(f"Only {held:g} µl in the tip.")
        moves.dispense(self.api, volume, flow_rate)
        self._set_in_tip(held - volume)
        return self._reached(f"Dispensed {volume:g} µl.")

    def blow_out(self) -> Reached:
        self._need_tip()
        require_ok(self.api.blow_out_in_place(BLOW_OUT_RATE), "blow out")
        self._blown_out = True
        self._set_in_tip(0.0)
        return self._reached("Blown out.")

    def _set_in_tip(self, ul: float) -> None:
        self.state.in_tip_ul = max(0.0, float(ul))
        self._save()

    # -- moves ---------------------------------------------------------------

    def _checked(self, pose) -> None:
        problem = self.motion.refused(pose)
        if problem:
            raise SliceRefused(problem)

    def return_up(self) -> Reached:
        """Back over where the tip last aspirated."""
        if self.aspirated_at is None:
            raise SliceRefused("Aspirate first.")
        x, y, z = self.aspirated_at
        self._checked((x, y, z + RETURN_ABOVE_MM))
        return Reached(tuple(self.motion.tip_to((x, y), z + RETURN_ABOVE_MM)),
                       "Back over the aspirate point.")

    def to_sample(self) -> Reached:
        sample = self.state.sample
        if sample is None:
            raise SliceRefused("Save a sample position first.")
        self._checked(sample)
        return Reached(tuple(self.motion.tip_to(sample[:2], sample[2])),
                       "At the sample.")

    def save_sample(self) -> Reached:
        pose = self.pose()
        self.state.sample = tuple(float(v) for v in pose)
        self._save()
        return Reached(pose, "Sample position saved.")

    def to_well(self, labware_id: str, well: str, offset) -> Reached:
        self.motion.to_well(labware_id, well, "top", offset)
        return self._reached(f"Over {well}.")

    # -- the microscope ------------------------------------------------------

    def pixel_map(self, device: str | None,
                  size) -> PixelToRobot | None:
        """The stored map, if it is of this device in this mode and still
        looks like one."""
        state = self.state
        if (state.matrix is None or device != state.map_device
                or tuple(size) != tuple(state.map_resolution or ())):
            return None
        pixel_map = PixelToRobot(tuple(map(tuple, state.matrix)))
        problem = calibration.check(pixel_map)
        if problem:
            log.error("the stored map is not used: %s", problem)
            return None
        return pixel_map

    def to_pixel(self, device: str, size, u: float, v: float) -> Reached:
        """The tool's point to pixel (u, v), straight across at its height."""
        pixel_map = self.pixel_map(device, size)
        if pixel_map is None:
            raise SliceRefused("Calibrate first.")
        pose = self.pose()
        target = pixel_map.robot(u, v)
        problem = (calibration.click_refusal(pixel_map, pose[:2], target, size)
                   or self.motion.refused((*target, pose[2])))
        if problem:
            raise SliceRefused(problem)
        return Reached(tuple(self.motion.slide_to(target)))

    def _blade_at(self, camera, template, settle_s: float = SETTLE_S):
        time.sleep(settle_s)
        frame = camera.read_after(time.monotonic(), skip=FRAMES_AFTER_SETTLING)
        return calibration.find_blade(frame, template)

    def calibrate(self, camera, device: str, spacing_mm: float, *,
                  cancel: threading.Event) -> Reached:
        """The tool round four points about where it stands, at its height,
        and the map from what was seen. See `calibration`. It ends back at
        the start, however it ends. Stores nothing unless all of it passes."""
        template = calibration.load_template()
        start = np.array(self.pose())
        match = self._blade_at(camera, template, settle_s=0.0)
        if match is None:
            raise CalibrationRefused("The blade is not in the picture.")
        current = self.pixel_map(device, camera.resolution)
        scale = (current.mm_per_px if current
                 else calibration.NOMINAL_MM_PER_PX)
        problem = calibration.room_refusal(match, camera.resolution,
                                           spacing_mm, scale)
        targets = [start[:2] + offset
                   for offset in calibration.offsets(spacing_mm)]
        problem = problem or next(
            filter(None, (self.motion.refused((*xy, start[2]))
                          for xy in targets)), "")
        if problem:
            raise CalibrationRefused(problem)

        pairs = []
        try:
            for number, xy in enumerate(targets, start=1):
                if cancel.is_set():
                    raise CalibrationRefused("Stopped. Nothing was saved.")
                reached = self.motion.slide_to(xy)
                seen = self._blade_at(camera, template)
                if seen is None:
                    raise CalibrationRefused(
                        f"The blade was lost at point {number}. Nothing was "
                        f"saved.")
                log.info("point %d: robot (%.3f, %.3f), pixel (%.1f, %.1f), "
                         "score %.2f", number, reached[0], reached[1],
                         *seen.point, seen.score)
                pairs.append((tuple(reached[:2]), seen.point))
        finally:
            try:
                self.motion.slide_to(start[:2])
            except Exception as exc:                 # noqa: BLE001
                log.warning("could not go back to the start: %s", exc)

        pixel_map, residual = calibration.judge(pairs)
        back = self._blade_at(camera, template)
        if back is None:
            raise CalibrationRefused("The blade was lost back at the start. "
                                     "Nothing was saved.")
        pose = self.pose()
        gap = calibration.verify(pixel_map, pose[:2], back.point)

        state = self.state
        state.matrix = [list(row) for row in pixel_map.matrix]
        state.map_device = device
        state.map_resolution = tuple(camera.resolution)
        state.map_measured_at = datetime.now(timezone.utc)
        self._save()
        log.info("calibrated: %.2f µm per pixel, fit %.3f mm, back at the "
                 "start %.3f mm", pixel_map.mm_per_px * 1000, residual, gap)
        return Reached(pose, "Calibrated and saved.")

    def _save(self) -> None:
        with self._lock:
            save_state(self.state)
