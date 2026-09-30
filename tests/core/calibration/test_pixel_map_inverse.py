"""`PixelMap.to_pixel` undoes `to_robot`, on a map that is not linear."""
from __future__ import annotations

import numpy as np

from micropick.config.schema import PixelMap as PixelMapConfig
from micropick.core.calibration.pixel_map import PixelMap, basis

W, H = 2592, 1944
CU, CV, S = W / 2, H / 2, W / 2
REF = (1200.0, 1000.0)


def _map() -> PixelMap:
    # Degree 3: terms v, u, v², uv, u², v³, uv², u²v, u³ (normalised). About
    # 0.026 mm per pixel with a rotation, a shear and a little distortion.
    coef = np.zeros((9, 2))
    coef[0] = [0.4 * S * 0.0026, 26.0 * S * 0.001]
    coef[1] = [26.0 * S * 0.001, -0.3 * S * 0.0026]
    coef[4] = [0.6, 0.2]
    coef[8] = [0.3, -0.1]
    zero = basis(REF[0], REF[1], CU, CV, S, 3) @ coef
    cfg = PixelMapConfig(degree=3, cu=CU, cv=CV, s=S, coef=coef.tolist(),
                         zero=zero[0].tolist(), ref=list(REF),
                         bounds=[0.0, 0.0, float(W), float(H)],
                         image_size=[W, H], sweep_z=0.0)
    return PixelMap(cfg)


def test_to_pixel_inverts_to_robot_across_the_frame():
    pmap, gantry = _map(), (150.0, 120.0)
    for u, v in [(100, 100), (2500, 1900), REF, (1800, 300)]:
        x, y = pmap.to_robot(u, v, gantry)
        back = pmap.to_pixel(x, y, gantry)
        assert back is not None
        assert np.hypot(back[0] - u, back[1] - v) < 0.05


def test_the_gantry_pose_puts_its_own_point_at_the_reference_pixel():
    pmap = _map()
    u, v = pmap.to_pixel(150.0, 120.0, (150.0, 120.0))
    assert abs(u - REF[0]) < 0.05 and abs(v - REF[1]) < 0.05
