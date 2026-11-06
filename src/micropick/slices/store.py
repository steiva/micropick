"""What Slice picking remembers: `slices.json`, beside `settings.json`.

The computer's, like the settings, and no profile's: the map belongs to the
microscope on this bench. A map is kept with the device and the resolution
it was measured on, and used on no other (`Rig.pixel_map`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config.app_settings import settings_path

__all__ = ["SliceState", "load_state", "save_state", "state_path"]

log = logging.getLogger(__name__)


class SliceState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device: str | None = None
    matrix: list[list[float]] | None = None         # 2 x 3, see calibration
    map_device: str | None = None
    map_resolution: tuple[int, int] | None = None
    map_measured_at: datetime | None = None

    sample: tuple[float, float, float] | None = None
    volume_ul: float = 100.0
    flow_rate: float = 280.0
    well_offset: tuple[float, float, float] = (-1.9, 0.7, 7.0)
    folder: str = ""

    platform_slots: list[int] = Field(default_factory=lambda: [5, 8, 9])
    platform_height_mm: float = 64.2
    # What a new robot session is loaded with: slot -> load name.
    deck: dict[str, str] = Field(default_factory=lambda: {
        "11": "opentrons_96_tiprack_300ul",
        "5": "corning_96_wellplate_360ul_flat"})
    tip_well: str = "A1"

    # The count of what is in the tip, for the tip it was counted in.
    in_tip_ul: float = 0.0
    tip_pickup: str | None = None


def state_path() -> Path:
    return settings_path().with_name("slices.json")


def load_state() -> SliceState:
    """The file, or the defaults when it is missing or cannot be read."""
    path = state_path()
    try:
        return SliceState.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SliceState()
    except (OSError, ValueError, ValidationError) as exc:
        log.error("%s cannot be read, starting from the defaults: %s",
                  path, exc)
        return SliceState()


def save_state(state: SliceState) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state.model_dump(mode="json"), indent=2)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
