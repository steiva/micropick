"""Stand-ins for the robot and the camera.

Everything above hardware/ programs against the protocols, so a workflow that
runs here runs unchanged on the bench. Being able to exercise the grab thread,
read_after and the recorder without a device is what makes the timing logic
testable at all: on real hardware a race shows up as an occasional bad
calibration point, which is nearly impossible to chase.
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

__all__ = ["FakeVideoCapture", "MockRobot", "open_mock_camera"]


class FakeVideoCapture:
    """Enough of cv2.VideoCapture to drive BackgroundCamera.

    Frames carry their own index drawn into the pixels, so a test can tell
    which frame it received and prove that read_after returned a later one.
    """

    def __init__(self, width=640, height=480, fps=30.0, render=None,
                 controls_honoured=True, fail_after=None):
        self._props = {
            cv2.CAP_PROP_FRAME_WIDTH: float(width),
            cv2.CAP_PROP_FRAME_HEIGHT: float(height),
            cv2.CAP_PROP_FPS: float(fps),
        }
        self.width, self.height = width, height
        self.fps = fps
        self.render = render
        self.controls_honoured = controls_honoured
        self.fail_after = fail_after
        self.index = 0
        self.released = False
        self._lock = threading.Lock()

    def isOpened(self):
        return not self.released

    def set(self, prop, value):
        if not self.controls_honoured and prop in (
                cv2.CAP_PROP_FOCUS, cv2.CAP_PROP_EXPOSURE,
                cv2.CAP_PROP_AUTO_EXPOSURE):
            return False
        self._props[prop] = float(value)
        return True

    def get(self, prop):
        return self._props.get(prop, 0.0)

    def read(self):
        if self.released:
            return False, None
        with self._lock:
            i = self.index
            self.index += 1
        if self.fail_after is not None and i >= self.fail_after:
            return False, None
        time.sleep(1.0 / self.fps)
        if self.render is not None:
            frame = self.render(i)
        else:
            frame = np.zeros((self.height, self.width, 3), np.uint8)
            frame[:] = (i % 256, 0, 0)
            cv2.putText(frame, str(i), (10, 40), cv2.FONT_HERSHEY_SIMPLEX,
                        1.2, (255, 255, 255), 2)
        # the frame index lives in pixel 0,0 so tests can recover it exactly
        frame[0, 0] = (i % 256, (i // 256) % 256, (i // 65536) % 256)
        return True, frame

    def release(self):
        self.released = True


def frame_index(frame) -> int:
    b, g, r = (int(v) for v in frame[0, 0])
    return b + g * 256 + r * 65536


class MockRobot:
    """Stands in for ot2_api.OpentronsAPI, same method names and signatures.

    Reports the pose reached with a small error rather than echoing the command.
    That is deliberate: workflows must read the pose back, and a mock returning
    exactly what was asked would hide the difference until it showed up on the
    bench as an occasional bad calibration point.
    """

    def __init__(self, position=(150.0, 150.0, 100.0), noise_mm=0.01,
                 speed_mm_s=200.0, backlash_mm=0.0, seed=0):
        self._pos = np.array(position, dtype=float)
        self.noise_mm = noise_mm
        self.speed = speed_mm_s
        self.backlash_mm = backlash_mm
        self._rng = np.random.default_rng(seed)
        self._last_dir = np.zeros(3)
        self.moves = 0
        self.log: list[tuple[float, float, float]] = []
        # ordered record of liquid-handling and well calls, so a workflow test
        # can assert what volumes went where and how far a batch got before a
        # stop interrupted it
        self.calls: list[tuple] = []

    def move_to_coordinates(self, coordinates, min_z_height=None,
                            force_direct=False, speed=None, verbose=True):
        target = np.asarray(coordinates, dtype=float)
        step = target - self._pos
        if self.speed:
            time.sleep(float(np.linalg.norm(step)) / self.speed)

        # direction-dependent lost motion, so a workflow that always approaches
        # from the same side can be shown to benefit from doing so
        pos = target.copy()
        if self.backlash_mm:
            direction = np.sign(step)
            reversed_axes = direction * self._last_dir < 0
            pos = pos - reversed_axes * direction * self.backlash_mm
            self._last_dir = np.where(direction != 0, direction, self._last_dir)

        pos[:2] += self._rng.normal(0, self.noise_mm, 2)
        self._pos = pos
        self.moves += 1
        self.log.append(tuple(float(v) for v in pos))
        if verbose:
            print(f"moved to {tuple(round(float(v), 3) for v in pos)}")

    def get_position(self, verbose=True):
        # (coordinates, response), matching the wrapper. The second element is
        # the raw HTTP response there and is never read, so None stands in.
        x, y, z = (float(v) for v in self._pos)
        if verbose:
            print(f"position: {x:.3f}, {y:.3f}, {z:.3f}")
        return ({"x": x, "y": y, "z": z}, None)

    def move_relative(self, axis, distance, verbose=True):
        idx = "xyz".index(axis)
        target = self._pos.copy()
        target[idx] += distance
        self.move_to_coordinates(target, verbose=verbose)

    def home_robot(self):
        self._pos = np.array([0.0, 0.0, 100.0])

    def toggle_lights(self, verbose=False):
        self.calls.append(("toggle_lights",))

    def retract_axis(self, axis, verbose=False):
        self.calls.append(("retract_axis", axis))

    def move_to_well(self, labware_id, well_name, well_location="top",
                     offset=(0, 0, 0), verbose=False, force_direct=False):
        self.calls.append(("move_to_well", labware_id, well_name, well_location))

    def aspirate_in_place(self, volume, flow_rate, verbose=False):
        self.calls.append(("aspirate_in_place", float(volume), float(flow_rate)))

    def dispense_in_place(self, volume, flow_rate, verbose=False):
        self.calls.append(("dispense_in_place", float(volume), float(flow_rate)))

    def dispense(self, labware_id, well_name, well_location="bottom",
                 offset=(0, 0, 0), volume=0.0, flow_rate=50.0, verbose=False):
        self.calls.append(("dispense", labware_id, well_name, float(volume),
                           float(flow_rate)))


def open_mock_camera(label="mock", width=640, height=480, fps=30.0, render=None):
    """A BackgroundCamera backed by FakeVideoCapture."""
    from .camera import BackgroundCamera
    cap = FakeVideoCapture(width, height, fps, render=render)
    return BackgroundCamera(cap, label=label, resolution=(width, height),
                            warmup=1)


# ---------------------------------------------------------------------------
# a scene the calibration workflow can actually be run against
# ---------------------------------------------------------------------------

class MarkerScene:
    """Renders a real ArUco marker as a gantry camera would see it.

    Deck geometry is projected with a known perspective and a known radial
    distortion, so a calibration run against this scene has ground truth: the
    fitted map can be compared with the optics that produced the frames. That
    is the only way to test the timing, the tracking and the fit together
    without a robot.
    """

    def __init__(self, *, image_size=(2592, 1944), mm_per_px=0.02633,
                 marker_id=1, marker_side_mm=6.8,
                 marker_xy=(181.5, 162.0), k1=-0.055, k2=0.010,
                 optical_centre=None, tilt=(0.010, 0.007),
                 noise_px=0.0, blur=True, dictionary=None):
        self.image_size = image_size
        self.mm_per_px = mm_per_px
        self.marker_id = marker_id
        self.marker_side_mm = marker_side_mm
        self.marker_xy = np.asarray(marker_xy, dtype=float)
        self.k1, self.k2 = k1, k2
        self.tilt = tilt
        self.noise_px = noise_px
        self.blur = blur
        w, h = image_size
        self.cx, self.cy = optical_centre or (w / 2 + 30, h / 2 - 25)
        self.norm = float(np.hypot(w, h) / 2)

        self.dictionary = dictionary or cv2.aruco.getPredefinedDictionary(
            cv2.aruco.DICT_6X6_250)
        side_px = max(48, int(round(marker_side_mm / mm_per_px)))
        bits = cv2.aruco.generateImageMarker(self.dictionary, marker_id, side_px)
        self.marker_img = cv2.cvtColor(bits, cv2.COLOR_GRAY2BGR)
        self._src = np.array([[0, 0], [side_px - 1, 0],
                              [side_px - 1, side_px - 1], [0, side_px - 1]],
                             dtype=np.float32)
        half = marker_side_mm / 2
        # corner order matches what detectMarkers returns: top-left clockwise
        self.corners_mm = np.array([[-half, -half], [half, -half],
                                    [half, half], [-half, half]]) + self.marker_xy

    def project(self, deck_xy, gantry_xy) -> np.ndarray:
        """Deck point plus gantry pose to pixel, with tilt and distortion."""
        off = np.atleast_2d(np.asarray(deck_xy, float)) - np.asarray(gantry_xy, float)[:2]
        tx, ty = self.tilt
        ideal = np.column_stack([
            self.cx - off[:, 0] / self.mm_per_px + tx * off[:, 1] / self.mm_per_px,
            self.cy - off[:, 1] / self.mm_per_px + ty * off[:, 0] / self.mm_per_px])
        dx = (ideal[:, 0] - self.cx) / self.norm
        dy = (ideal[:, 1] - self.cy) / self.norm
        r2 = dx ** 2 + dy ** 2
        f = 1 + self.k1 * r2 + self.k2 * r2 ** 2
        return np.column_stack([self.cx + dx * f * self.norm,
                                self.cy + dy * f * self.norm])

    def render(self, gantry_xy) -> np.ndarray:
        w, h = self.image_size
        frame = np.full((h, w, 3), 235, np.uint8)
        dst = self.project(self.corners_mm, gantry_xy).astype(np.float32)
        if self.noise_px:
            dst = dst + np.random.normal(0, self.noise_px, dst.shape).astype(np.float32)

        pad = max(self.marker_img.shape) * 2
        if (dst[:, 0].min() < -pad or dst[:, 0].max() > w + pad
                or dst[:, 1].min() < -pad or dst[:, 1].max() > h + pad):
            return frame                        # marker is off screen

        M = cv2.getPerspectiveTransform(self._src, dst)
        warped = cv2.warpPerspective(self.marker_img, M, (w, h),
                                     borderValue=(235, 235, 235))
        mask = cv2.warpPerspective(
            np.full(self.marker_img.shape[:2], 255, np.uint8), M, (w, h))
        frame[mask > 128] = warped[mask > 128]
        if self.blur:
            frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame


class SceneCamera:
    """A BackgroundCamera fed by a MarkerScene that follows a MockRobot.

    Frames are rendered from the robot's current pose at grab time, so the same
    staleness the real system has is reproduced: a frame grabbed during a move
    shows the marker mid-flight, which is exactly what read_after has to filter
    out.
    """

    def __init__(self, robot, scene: MarkerScene, fps: float = 30.0):
        self.robot = robot
        self.scene = scene
        self.fps = fps
        self._props = {
            cv2.CAP_PROP_FRAME_WIDTH: float(scene.image_size[0]),
            cv2.CAP_PROP_FRAME_HEIGHT: float(scene.image_size[1]),
            cv2.CAP_PROP_FPS: float(fps),
        }
        self.released = False
        self.grabs = 0

    def isOpened(self):
        return not self.released

    def set(self, prop, value):
        self._props[prop] = float(value)
        return True

    def get(self, prop):
        return self._props.get(prop, 0.0)

    def read(self):
        if self.released:
            return False, None
        time.sleep(1.0 / self.fps)
        self.grabs += 1
        pose = self.robot.get_position(verbose=False)[0]
        return True, self.scene.render((pose["x"], pose["y"]))

    def release(self):
        self.released = True


def open_scene_camera(robot, scene: MarkerScene, fps: float = 30.0,
                      label: str = "scene"):
    from .camera import BackgroundCamera
    return BackgroundCamera(SceneCamera(robot, scene, fps), label=label,
                            resolution=scene.image_size, warmup=1)
