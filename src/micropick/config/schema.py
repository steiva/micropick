"""Profile schema.

A profile describes one physical installation: its camera calibration, its
pipette offset, and its picking parameters. Profiles carry a schema version and
are validated on load, so a format mismatch fails at load time rather than
surfacing later as a positioning error.

Files inside a profile directory are split by what writes them, not by topic:
the calibration routine owns calibration.json, the operator owns picking.json.
Splitting this way means re-running a calibration cannot clobber hand-edited
picking parameters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (AliasChoices, BaseModel, ConfigDict, Field,
                      computed_field, field_validator, model_validator)

SCHEMA_VERSION = 2

Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]


def _n_basis_terms(degree: int) -> int:
    """Monomials of total degree 1..degree in two variables. No constant term."""
    return sum(d + 1 for d in range(1, degree + 1))


class PixelMap(BaseModel):
    """Pixel to millimetre offset map, zero at the reference pixel.

    Fitted from a robot-driven sweep of a static marker. Absorbs lens
    distortion, scale and perspective in one model, so no camera intrinsics and
    no separate undistortion stage are involved.

    Valid only for the optical configuration and the object plane it was fitted
    at. Touching focus, zoom or camera height invalidates it, as does changing
    the height of the plane being observed.
    """

    model_config = ConfigDict(extra="forbid")

    degree: int = Field(ge=1, le=5)
    cu: float
    cv: float
    s: float = Field(gt=0)
    coef: list[Vec2]
    zero: Vec2
    ref: Vec2
    bounds: Annotated[list[float], Field(min_length=4, max_length=4)]
    image_size: Annotated[list[int], Field(min_length=2, max_length=2)]

    sweep_z: float
    camera_label: str | None = None
    camera_controls: dict[str, float | str] = Field(default_factory=dict)
    marker_side_mm: float | None = None
    marker_side_measured_mm: float | None = None
    holdout_mean_um: float | None = None
    holdout_max_um: float | None = None
    n_poses: int | None = None
    fitted_at: datetime | None = None

    @model_validator(mode="after")
    def _check_shape(self):
        expected = _n_basis_terms(self.degree)
        if len(self.coef) != expected:
            raise ValueError(
                f"degree {self.degree} needs {expected} basis terms, got {len(self.coef)}"
            )
        u0, v0, u1, v1 = self.bounds
        if u1 <= u0 or v1 <= v0:
            raise ValueError(f"degenerate bounds {self.bounds}")
        return self

    @model_validator(mode="after")
    def _warn_on_marker_mismatch(self):
        """The measured marker side is an independent check on the fit: the map
        never uses the marker size, so a large disagreement means bad data."""
        if self.marker_side_mm and self.marker_side_measured_mm:
            rel = abs(self.marker_side_measured_mm - self.marker_side_mm) / self.marker_side_mm
            if rel > 0.05:
                raise ValueError(
                    f"measured marker side {self.marker_side_measured_mm:.4f} mm differs "
                    f"from nominal {self.marker_side_mm:.4f} mm by {rel*100:.1f} %, "
                    f"the sweep is probably bad"
                )
        return self

    def check_camera(self, resolution, controls: dict | None = None) -> list[str]:
        """Reasons the map may not apply to the camera as currently configured.

        Resolution is fatal: fitting at 2592 and running at 1920 misplaces
        everything by a third of the field with no other symptom. Focus and
        zoom changes are equally fatal but can only be detected for controls
        the driver reports back.
        """
        problems = []
        if tuple(resolution) != tuple(self.image_size):
            problems.append(
                f"map was fitted at {tuple(self.image_size)} but the camera is "
                f"at {tuple(resolution)}"
            )
        for key, was in (self.camera_controls or {}).items():
            now = (controls or {}).get(key)
            if now is not None and str(now) != str(was):
                problems.append(f"{key} was {was} at calibration, now {now}")
        return problems

    def covers(self, u: float, v: float, margin_px: float = 0.0) -> bool:
        """True if a pixel lies inside the swept area, so no extrapolation."""
        u0, v0, u1, v1 = self.bounds
        return (u0 - margin_px <= u <= u1 + margin_px
                and v0 - margin_px <= v <= v1 + margin_px)



class CameraSpec(BaseModel):
    """One camera of an installation.

    The device index is deliberately absent: it changes between reboots and USB
    ports, while the name does not, so the index is resolved at open time.

    controls are re-applied on every open. That is what keeps a pixel map valid
    across restarts, since the map is only correct for the focus it was fitted
    at.
    """

    model_config = ConfigDict(extra="forbid")

    device_name: str
    resolutions: list[Annotated[list[int], Field(min_length=2, max_length=2)]] = []
    default_resolution: Annotated[list[int], Field(min_length=2, max_length=2)]
    fps: int | None = None
    fourcc: str | None = "MJPG"
    controls: dict[str, float | str] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="after")
    def _default_is_listed(self):
        if self.resolutions:
            listed = [tuple(r) for r in self.resolutions]
            if tuple(self.default_resolution) not in listed:
                raise ValueError(
                    f"default_resolution {self.default_resolution} is not in "
                    f"resolutions {self.resolutions}"
                )
        return self


class PipetteOffset(BaseModel):
    """Offset from the camera reference point to the pipette tip.

    Independent of the optical calibration: produced by the tip calibration
    module and replaceable on its own. A new tip, or a tip seated differently,
    changes this and nothing else.

    On a fresh installation the values must be filled in by hand, roughly, with
    a ruler. The automatic routine drives the tip to where it believes the
    target is before looking for it, so an offset that is wrong by tens of
    millimetres puts the tip outside the lower camera's view and the run cannot
    recover.
    """

    model_config = ConfigDict(extra="forbid")

    dx: float
    dy: float
    tip_type: str | None = None
    measured_at: datetime | None = None
    method: Literal["manual", "auto", "auto+manual"] = "manual"
    residual_mm: float | None = None
    n_samples: int | None = None
    spread_mm: float | None = None


class TipTarget(BaseModel):
    """The crosshair disc used for tip calibration.

    axes maps a unit pixel displacement in the lower camera onto a robot
    displacement, as a 2x2 matrix in row-major order. Scale is not included:
    it is recomputed from the crosshair spacing on every run.

    The default swaps x and y. That follows from the module geometry: the lower
    camera is turned ninety degrees relative to the upper one and looks up from
    the opposite side, so the transform is a rotation combined with a mirror.
    Its determinant is therefore -1, and a matrix with determinant +1 here would
    mean the mirror had been forgotten. Fixed by the hardware, so this should
    never need editing; it lives in the profile because the previous version
    expressed it as two lines of code with the variable names crossed over,
    where it was invisible.
    """

    model_config = ConfigDict(extra="forbid")

    spacing_mm: float = 20.25
    module_height: float = 67.1
    approach_offset: Vec2 = [3.0, 0.0]
    axes: Annotated[list[Vec2], Field(min_length=2, max_length=2)] = [[0.0, 1.0],
                                                                     [1.0, 0.0]]
    axes_measured_at: datetime | None = None
    model_file: str = "tip_detector_v1.pt"
    imgsz: int = 2016
    conf: float = 0.25

    @model_validator(mode="after")
    def _axes_are_sane(self):
        import math
        a, b = self.axes
        det = a[0] * b[1] - a[1] * b[0]
        if abs(abs(det) - 1.0) > 0.05:
            raise ValueError(
                f"axes has determinant {det:.3f}; it should be a pure rotation "
                f"or reflection with determinant +/-1, since scale comes from "
                f"the crosshair spacing"
            )
        if self.spacing_mm <= 0:
            raise ValueError("spacing_mm must be positive")
        return self


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pixel_map: PixelMap | None = None
    pipette_offset: PipetteOffset | None = None
    tip_target: TipTarget = Field(default_factory=lambda: TipTarget())

    @property
    def is_ready(self) -> bool:
        return self.pixel_map is not None and self.pipette_offset is not None


class PickingConfig(BaseModel):
    """Picking, detection and deposition parameters.

    Loaded from and dumped to plain dicts so it stays convenient in a notebook,
    but validated on the way in: an unknown key or an out-of-order window is an
    error rather than a silently ignored value.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    vol: float = 10.0
    dish_bottom: float = 66.1          # 10.60 for 300 ul, 9.5 for 200 ul
    pickup_offset: float = 0.5
    flow_rate: float = 50.0
    # accepts the historical misspelling "cuboid_size_theshold" from saved dicts
    cuboid_size_threshold: tuple[int, int] = Field(
        default=(250, 500),
        validation_alias=AliasChoices("cuboid_size_threshold", "cuboid_size_theshold"),
    )
    failure_threshold: float = 0.5
    minimum_distance: float = 1.7
    wait_time_after_deposit: float = 0.5
    one_by_one: bool = False

    # ---------------------- deposit ----------------------
    well_offset_x: float = 0.0        # 384 well plate
    well_offset_y: float = 0.0        # 384 well plate
    deposit_offset_z: float = 0.5
    destination_slot: int = 5
    deposit_z_optional: float = 67.0

    # ---------------------- video ----------------------
    circle_center: tuple[int, int] = (1296, 972)
    circle_radius: int = 900

    # ---------------------- YOLO detection ----------------------
    yolo_imgsz: int = 1536             # must match the training size
    yolo_conf: float = 0.25            # low: recall first, shape filters later
    yolo_iou: float = 0.80             # above default: touching cuboids labelled singly
    yolo_max_det: int = 600

    # ---------------------- picking loop ----------------------
    max_batch: int = 10                # cap on cuboids aspirated before a deposit
    max_shake_retries: int = 3         # shakes with no isolated cuboids before
                                       # handing back to the operator
    lift_mm: float = 20.0              # clearance raised above pickup_height and
                                       # after each aspirate
    capture_settle_s: float = 0.3      # pause after parking before a frame
    verify_settle_s: float = 0.75      # pause after parking before the check frame
    # what to do with a partial miss. keep_successful deposits the held cuboids
    # into the well and returns only the missed volume to the dish, so the
    # per-well concentration stays constant; return_all sends everything back.
    miss_policy: Literal["return_all", "keep_successful"] = "keep_successful"

    # ---------------------- Otsu and shape ----------------------
    otsu_pad: int = 6                  # margin around the box, Otsu needs background
    otsu_open_k: int = 3               # breaks bridges to the rim or a neighbour
    aspect_ratio_window: tuple[float, float] = (1.0, 1.30)   # minAreaRect: always >= 1
    circularity_window: tuple[float, float] = (0.60, 1.00)
    min_solidity: float = 0.90         # catches concave and merged blobs
    max_radial_cv: float = 0.22        # symmetry: accepts both circle and square

    # ---------------------- floater detection ----------------------
    floater_check_interval: int = 3    # recompute every N picking cycles
    floater_clip_sec: float = 2.5
    floater_min_area: int = 15
    floater_zone_radius_px: int = 75
    floater_mad_k: float = 9.0

    @computed_field
    @property
    def pickup_height(self) -> float:
        """Derived, not stored. In the old dataclass this was a field whose
        default was evaluated once at class definition, so it never tracked
        dish_bottom except inside from_dict."""
        return self.dish_bottom + self.pickup_offset

    @model_validator(mode="before")
    @classmethod
    def _drop_derived(cls, data):
        """pickup_height is serialised for readability but is derived, so it is
        accepted and discarded on the way in. This has to sit here rather than
        in from_dict, because model_validate_json is also an entry path."""
        if isinstance(data, dict) and "pickup_height" in data:
            data = {k: v for k, v in data.items() if k != "pickup_height"}
        return data

    @field_validator("cuboid_size_threshold", "aspect_ratio_window",
                     "circularity_window")
    @classmethod
    def _ordered(cls, v):
        if v[0] >= v[1]:
            raise ValueError(f"expected (min, max) with min < max, got {v}")
        return v

    # ---------------------- notebook convenience ----------------------

    @classmethod
    def from_dict(cls, data: dict) -> "PickingConfig":
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        return self.model_dump()


class ProfileMeta(BaseModel):
    """profile.json. Read first, before anything else in the directory."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = SCHEMA_VERSION
    name: str
    created_at: datetime | None = None
    camera_label: str | None = None
    notes: str = ""
