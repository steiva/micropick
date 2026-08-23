"""Reading and writing profiles.

A profile is a directory under the profiles root:

    profiles/<name>/
        profile.json        metadata and schema version, read first
        calibration.json    written by the calibration routine
        picking.json        edited by the operator
        history/            timestamped copies of previous calibrations

Files are split by what writes them rather than by topic, so re-running a
calibration cannot clobber hand-edited picking parameters.

Writes are atomic: content goes to a temporary file in the same directory and is
then renamed over the target. A crash mid-write leaves the previous file intact
rather than a truncated one, which matters because a half-written calibration
would be loaded on the next run and silently misdirect the robot.

This module imports no hardware and no numpy: it deals in validated models only.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .. import paths
from .schema import (SCHEMA_VERSION, Calibration, CameraSpec, PickingConfig,
                     ProfileMeta)

__all__ = ["Profile", "ProfileError", "LegacyProfileError",
           "list_profiles", "create_profile", "load_profile", "profile_dir"]

META_FILE = "profile.json"
CALIBRATION_FILE = "calibration.json"
PICKING_FILE = "picking.json"
CAMERAS_FILE = "cameras.json"
POSITIONS_FILE = "positions.json"
HISTORY_DIR = "history"
KEEP_HISTORY = 10

# Keys that only ever appeared in the pre-rewrite format.
LEGACY_KEYS = {"tf_mtx", "size_conversion_ratio", "one_d_ratio", "camera_mtx"}


class ProfileError(RuntimeError):
    """A profile could not be read or written."""


class LegacyProfileError(ProfileError):
    """The directory holds a profile from the previous repository.

    Raised rather than attempted, because the old affine transform and the new
    pixel map are not convertible: the old one has no distortion term and was
    fitted over a small part of the frame. The only correct action is a new
    sweep.
    """


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def profile_dir(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ProfileError(f"invalid profile name: {name!r}")
    return paths.profiles_dir() / name


def list_profiles() -> list[str]:
    root = paths.profiles_dir()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and (d / META_FILE).is_file())


# ---------------------------------------------------------------------------
# low level io
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{path} is not valid JSON: {exc}") from exc


def _write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_model(path: Path, model: BaseModel) -> None:
    _write_atomic(path, model.model_dump_json(indent=2) + "\n")


def _validate(model_cls, data: dict, path: Path):
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(f"{path} does not match the expected format:\n{exc}") from exc


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

class Profile:
    """One installation's configuration.

    Sections are saved independently. Calibration and picking are always
    present as objects; an uncalibrated profile simply has empty fields inside
    Calibration, which keeps callers from having to handle None at the top
    level.
    """

    def __init__(self, meta: ProfileMeta, calibration: Calibration,
                 picking: PickingConfig, cameras: dict[str, CameraSpec],
                 positions: dict[str, tuple[float, float, float]], path: Path):
        self.meta = meta
        self.calibration = calibration
        self.picking = picking
        self.cameras = cameras
        self.positions = positions
        self.path = path

    # -- convenience --------------------------------------------------------

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def pixel_map(self):
        """Serialised map, or None. Wrap with
        micropick.core.calibration.pixel_map.PixelMap to apply it."""
        return self.calibration.pixel_map

    def floater_baseline(self):
        """The stored noise floor as a runtime `floaters.Baseline`, or None.

        Unlike `pixel_map`, which hands back the stored model and leaves
        `PixelMap.from_config` to the caller, the conversion happens here:
        `Baseline` is a plain dataclass with no constructor of its own, and
        core/vision/floaters.py is deliberately free of anything that knows what
        a profile is. The import is local for the same reason — config does not
        depend on core at module level, and this is the one place it needs a
        type from it.

        Refuses a baseline measured with a different window geometry rather than
        applying it. The floor is only the number it was measured to be for the
        pad and taper it was measured at; a matrix used in the wrong camera mode
        taught us what a calibration silently applied under changed parameters
        costs.
        """
        stored = self.calibration.floater_baseline
        if stored is None:
            return None

        cfg = self.picking
        drift = [(name, was, now)
                 for name, was, now in (("pad_frac", stored.pad_frac, cfg.floater_pad_frac),
                                        ("sigma_frac", stored.sigma_frac, cfg.floater_sigma_frac))
                 if abs(was - now) > 1e-9]
        if drift:
            detail = "; ".join(f"{n}: measured at {w:g}, configured {c:g}"
                               for n, w, c in drift)
            raise ProfileError(
                f"profile {self.name!r} has a floater baseline measured with a "
                f"different window geometry ({detail}). The noise floor does not "
                f"carry across: either restore the values it was measured at, or "
                f"re-measure the baseline."
            )

        from ..core.vision.floaters import Baseline
        return Baseline(
            rms_p50_um=stored.rms_p50_um, rms_p95_um=stored.rms_p95_um,
            rms_max_um=stored.rms_max_um, n_objects=stored.n_objects,
            n_clips=stored.n_clips, window_s=stored.window_s,
            n_frames=stored.n_frames)

    def require_calibration(self):
        """Fail early with a useful message rather than at the first bad move."""
        if self.calibration.pixel_map is None:
            raise ProfileError(
                f"profile '{self.name}' has no pixel map, run the calibration sweep"
            )
        if self.calibration.pipette_offset is None:
            raise ProfileError(
                f"profile '{self.name}' has no pipette offset, run the pipette "
                f"calibration"
            )
        return self.calibration

    # -- saving -------------------------------------------------------------

    def save(self) -> None:
        self.save_meta()
        self.save_calibration()
        self.save_picking()
        self.save_cameras()
        self.save_positions()

    def save_meta(self) -> None:
        _write_model(self.path / META_FILE, self.meta)

    def save_calibration(self, *, backup: bool = True) -> None:
        """A sweep costs minutes of robot time, so the previous calibration is
        archived before being replaced."""
        target = self.path / CALIBRATION_FILE
        if backup and target.is_file():
            self._archive(target)
        _write_model(target, self.calibration)

    def save_picking(self) -> None:
        _write_model(self.path / PICKING_FILE, self.picking)

    def save_positions(self) -> None:
        _write_atomic(self.path / POSITIONS_FILE,
                      json.dumps({k: list(v) for k, v in self.positions.items()},
                                 indent=2) + "\n")

    def remember(self, name: str, position) -> tuple[float, float, float]:
        """Record a named pose. Deck landmarks are re-used across sessions and
        across routines, so they belong in the profile rather than in whichever
        notebook happened to find them."""
        self.positions[name] = tuple(float(v) for v in position)
        self.save_positions()
        return self.positions[name]

    def where(self, name: str) -> tuple[float, float, float]:
        try:
            return self.positions[name]
        except KeyError:
            raise ProfileError(
                f"profile {self.name!r} has no position {name!r}; "
                f"known: {', '.join(sorted(self.positions)) or 'none'}"
            ) from None

    def save_cameras(self) -> None:
        payload = {k: v.model_dump() for k, v in self.cameras.items()}
        _write_atomic(self.path / CAMERAS_FILE,
                      json.dumps(payload, indent=2) + "\n")

    def _archive(self, target: Path) -> None:
        hist = self.path / HISTORY_DIR
        hist.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        shutil.copy2(target, hist / f"{target.stem}_{stamp}{target.suffix}")
        old = sorted(hist.glob(f"{target.stem}_*{target.suffix}"))
        for path in old[:-KEEP_HISTORY]:
            path.unlink(missing_ok=True)

    def __repr__(self) -> str:
        pm = self.calibration.pixel_map
        state = "uncalibrated" if pm is None else f"degree {pm.degree}, {pm.n_poses} poses"
        return f"<Profile {self.name!r} ({state}) at {self.path}>"


# ---------------------------------------------------------------------------
# loading and creation
# ---------------------------------------------------------------------------

def _check_not_legacy(path: Path) -> None:
    legacy = path / CALIBRATION_FILE
    if not legacy.is_file():
        return
    try:
        data = _read_json(legacy)
    except ProfileError:
        return
    hit = LEGACY_KEYS.intersection(data)
    if hit:
        raise LegacyProfileError(
            f"{path} is a profile from the previous repository "
            f"(found {sorted(hit)}). Formats are not convertible: the old "
            f"transform carries no distortion term. Create a new profile and "
            f"run a calibration sweep. The old profile is left untouched."
        )


def load_profile(name: str, *, directory: Path | None = None) -> Profile:
    """Load a profile by name, or from a directory given outright.

    `directory` names the profile's own folder and bypasses the profiles root.
    A profile is a directory of JSON files and nothing about it requires living
    under `profiles/`; the GUI keeps a scratch profile for its mock mode under
    `outputs/`, where it cannot be mistaken for an installation. The naming
    follows `config.labware`, which takes a `directory` for the same reason.
    """
    path = Path(directory) if directory is not None else profile_dir(name)
    if not path.is_dir():
        if directory is not None:
            raise ProfileError(f"no profile directory at {path}")
        known = list_profiles()
        raise ProfileError(
            f"no profile {name!r} under {paths.profiles_dir()}"
            + (f"; available: {', '.join(known)}" if known else "")
        )

    meta_path = path / META_FILE
    if not meta_path.is_file():
        _check_not_legacy(path)
        raise ProfileError(f"{path} has no {META_FILE}, so it is not a valid profile")

    raw = _read_json(meta_path)
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ProfileError(
            f"{meta_path} has schema_version {version!r}, this build reads "
            f"{SCHEMA_VERSION}. Refusing to guess at the format."
        )
    meta = _validate(ProfileMeta, raw, meta_path)

    cal_path = path / CALIBRATION_FILE
    calibration = (_validate(Calibration, _read_json(cal_path), cal_path)
                   if cal_path.is_file() else Calibration())

    pick_path = path / PICKING_FILE
    picking = (_validate(PickingConfig, _read_json(pick_path), pick_path)
               if pick_path.is_file() else PickingConfig())

    cam_path = path / CAMERAS_FILE
    cameras: dict[str, CameraSpec] = {}
    if cam_path.is_file():
        for label, spec in _read_json(cam_path).items():
            cameras[label] = _validate(CameraSpec, spec, cam_path)

    pos_path = path / POSITIONS_FILE
    positions = {}
    if pos_path.is_file():
        for name, value in _read_json(pos_path).items():
            if len(value) != 3:
                raise ProfileError(f"{pos_path}: position {name!r} is not x, y, z")
            positions[name] = tuple(float(v) for v in value)

    return Profile(meta, calibration, picking, cameras, positions, path)


def create_profile(name: str, *, camera_label: str | None = None,
                   notes: str = "", exist_ok: bool = False,
                   directory: Path | None = None) -> Profile:
    """Create a profile directory with defaults. Does not touch the robot.

    `directory` overrides the location, as in `load_profile`.
    """
    path = Path(directory) if directory is not None else profile_dir(name)
    if path.exists():
        _check_not_legacy(path)
        if not exist_ok:
            raise ProfileError(f"profile {name!r} already exists at {path}")
        return load_profile(name, directory=directory)

    profile = Profile(
        meta=ProfileMeta(name=name, camera_label=camera_label, notes=notes,
                         created_at=datetime.now(timezone.utc)),
        calibration=Calibration(),
        picking=PickingConfig(),
        cameras={},
        positions={},
        path=path,
    )
    path.mkdir(parents=True)
    profile.save()
    return profile