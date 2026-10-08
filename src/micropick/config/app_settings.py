"""Settings of this installation of the application, not of a profile.

A profile is the bench: its cameras, its calibration, its positions. Some
things are neither - how this computer reaches the robot, and where what
the application produces is put - and they belong to the computer the GUI
runs on. The robot's address is the clearest case: 169.254.x.x over a
direct cable and something else over Wi-Fi, with the same robot and the
same calibration either way.

The pickup clips' camera mode is here too: how many frames a second this
computer and its camera can record is about the computer, and the profile's
camera mode is the one the calibrations were measured in.

They are one small file, `settings.json` in the root (`paths.root()`),
beside `profiles/`, written atomically as the profiles are. A missing file
is the defaults; an unreadable one is the defaults too, with the reason
returned so the Settings page can show it rather than refuse to start.

`apply` hands the folders to `paths`, which every writer already asks, so a
changed folder takes effect for the next file without anything else
knowing settings exist.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import paths
from .store import _write_atomic

__all__ = ["AppSettings", "SETTINGS_FILE", "settings_path", "load_settings",
           "save_settings", "apply"]

SETTINGS_FILE = "settings.json"


class AppSettings(BaseModel):
    """`robot_host` None is the robot wrapper's own default (the OT2_HOST
    environment variable, else the link-local 169.254.241.245). A folder
    None is the default under the root."""

    model_config = ConfigDict(extra="forbid")

    robot_host: str | None = None
    robot_port: int = Field(default=31950, ge=1, le=65535)
    outputs_dir: str | None = None
    logs_dir: str | None = None
    images_dir: str | None = None
    # The lower camera's mode while a picking run records pickup clips, and
    # the centre of the frame kept: fewer pixels is more frames a second, and
    # the pickup happens in the middle. Calibrations open the camera in the
    # profile's own mode, whatever this says.
    clip_resolution: tuple[int, int] = (2000, 1500)
    clip_crop: float = Field(default=0.5, gt=0.0, le=1.0)
    # The camera calibration sweep: the marker printed for this bench and
    # the ArUco dictionary it is printed in, and the grid and degree the
    # first real sweep was judged at (DESIGN section 3). The bench's, like
    # the marker on it, not a profile's.
    sweep_marker_side_mm: float = Field(default=6.8, ge=1.0, le=60.0)
    sweep_dictionary: str = "DICT_6X6_250"
    sweep_grid_n: int = Field(default=7, ge=4, le=15)
    # Below 3 is refused by fit_pixel_map rather than silently useless:
    # radial distortion is cubic in image coordinates.
    sweep_degree: int = Field(default=3, ge=3, le=5)
    # The tip calibration: the tip type recorded with the offset when the
    # robot's record does not name the rack ("" is "from the rack"), frames
    # per reading, the check after the correction, and the manual
    # adjustment at the end.
    tip_cal_tip_type: str = ""
    tip_cal_frames: int = Field(default=7, ge=1, le=30)
    tip_cal_verify: bool = True
    tip_cal_touch_up: bool = True


def settings_path() -> Path:
    return paths.root() / SETTINGS_FILE


def load_settings() -> tuple[AppSettings, str]:
    """The settings, and "" or why the file could not be used."""
    path = settings_path()
    if not path.is_file():
        return AppSettings(), ""
    try:
        return AppSettings.model_validate(
            json.loads(path.read_text(encoding="utf-8"))), ""
    except (json.JSONDecodeError, ValidationError, OSError) as exc:
        return AppSettings(), f"{path} could not be read, so the defaults " \
                              f"are used: {exc}"


def save_settings(settings: AppSettings) -> Path:
    path = settings_path()
    _write_atomic(path, settings.model_dump_json(indent=2) + "\n")
    return path


def apply(settings: AppSettings) -> None:
    """The folders into `paths`; None keeps a folder's default."""
    paths.set_overrides(outputs=settings.outputs_dir, logs=settings.logs_dir,
                        images=settings.images_dir)
