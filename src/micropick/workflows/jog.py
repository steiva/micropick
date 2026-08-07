"""Driving the robot by hand.

Split in two. JogController holds the rules: step sizes, limits, saved
positions, undo. It touches no keyboard and no window, so it can be tested
without either. The backends below turn key presses into calls on it.

Limits let the robot escape
---------------------------
The previous version refused any move whose target fell outside the working
box. That is correct while the robot is inside it and useless once it is not:
after homing, or after a routine parks the gantry, every direction is refused
and the only way out is to disable the guard. Here a move is judged by whether
it makes the excursion worse. Outside the box, moving back toward it is always
allowed; moving further out never is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..hardware.protocols import Robot, xyz

__all__ = ["Limits", "MoveResult", "JogController", "jog_in_window",
           "jog_with_hotkeys", "DEFAULT_STEPS"]

DEFAULT_STEPS = [0.01, 0.05, 0.1, 0.5, 1, 3, 5, 10, 30, 50]
AXES = "xyz"


@dataclass
class Limits:
    """Soft working envelope. Deliberately not the machine's own limits: this
    exists to stop a slip of the hand, not to replace the firmware."""

    x: tuple[float, float] = (0.0, 380.0)
    y: tuple[float, float] = (0.0, 350.0)
    z: tuple[float, float] = (0.1, 150.0)
    # Clamping lands on the boundary through floating point subtraction, which
    # can leave the value a few ulps outside. Without slack the axis then
    # reports itself out of bounds and refuses the next step in that direction.
    tol: float = 1e-6

    def bounds(self, axis: str) -> tuple[float, float]:
        return getattr(self, axis)

    def contains(self, position) -> bool:
        return all(lo - self.tol <= v <= hi + self.tol for v, (lo, hi)
                   in zip(position, (self.x, self.y, self.z)))

    def excursion(self, position) -> dict[str, float]:
        """How far outside the box each axis is, zero when inside."""
        out = {}
        for axis, value in zip(AXES, position):
            lo, hi = self.bounds(axis)
            d = max(0.0, lo - value, value - hi)
            out[axis] = 0.0 if d <= self.tol else d
        return out


@dataclass
class MoveResult:
    ok: bool
    axis: str
    requested: float
    performed: float = 0.0
    reason: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def clamped(self) -> bool:
        return self.ok and abs(self.performed) < abs(self.requested) - 1e-9


class JogController:
    """Step control, limits, saved positions and undo. No input handling."""

    def __init__(self, robot: Robot, *, limits: Limits | None = None,
                 steps: list[float] | None = None, step: float = 1.0,
                 clamp: bool = True):
        self.robot = robot
        self.limits = limits or Limits()
        self.steps = sorted(steps or DEFAULT_STEPS)
        self.step = min(self.steps, key=lambda s: abs(s - step))
        self.clamp = clamp
        self.saved: dict[str, tuple[float, float, float]] = {}
        self._history: list[tuple[str, float]] = []
        self._busy = False

    # -- state --------------------------------------------------------------

    @property
    def position(self) -> tuple[float, float, float]:
        return xyz(self.robot)

    @property
    def busy(self) -> bool:
        """True while a move is in flight. Backends drop key repeats during
        this, otherwise a held arrow queues moves that keep running after the
        key is released."""
        return self._busy

    # -- the rule that matters ----------------------------------------------

    def allowed(self, axis: str, delta: float,
                position=None) -> tuple[float, str]:
        """How much of a requested step is permitted, and why not more.

        Inside the box, a step is capped at the boundary. Outside it, a step
        toward the box passes in full and a step away is refused, so the robot
        is never trapped by its own guard.
        """
        pos = list(position if position is not None else self.position)
        i = AXES.index(axis)
        current, target = pos[i], pos[i] + delta
        lo, hi = self.limits.bounds(axis)
        tol = self.limits.tol

        if lo - tol <= target <= hi + tol:
            return delta, ""

        if target > hi:
            if current > hi + tol:
                if target < current:
                    return delta, ""             # coming back down
                return 0.0, (f"{axis} is already {current - hi:.2f} mm above "
                             f"the limit {hi:g}, only moves back are allowed")
            return ((hi - current, f"clamped at {axis} limit {hi:g}")
                    if self.clamp and hi > current else
                    (0.0, f"{axis} limit {hi:g} reached"))

        if current < lo - tol:
            if target > current:
                return delta, ""                 # coming back up
            return 0.0, (f"{axis} is already {lo - current:.2f} mm below "
                         f"the limit {lo:g}, only moves back are allowed")
        return ((lo - current, f"clamped at {axis} limit {lo:g}")
                if self.clamp and lo < current else
                (0.0, f"{axis} limit {lo:g} reached"))

    # -- moving -------------------------------------------------------------

    def move(self, axis: str, direction: int = 1,
             distance: float | None = None, *, record: bool = True) -> MoveResult:
        if axis not in AXES:
            raise ValueError(f"axis must be one of {AXES}, got {axis!r}")
        if self._busy:
            return MoveResult(False, axis, 0.0, reason="a move is already running")

        requested = (self.step if distance is None else distance) * direction
        allowed, reason = self.allowed(axis, requested)
        if allowed == 0.0:
            return MoveResult(False, axis, requested, reason=reason,
                              position=self.position)

        self._busy = True
        try:
            self.robot.move_relative(axis, allowed, verbose=False)
        except TypeError:                        # wrappers without the kwarg
            self.robot.move_relative(axis, allowed)
        finally:
            self._busy = False

        if record:
            self._history.append((axis, allowed))
        return MoveResult(True, axis, requested, allowed, reason, self.position)

    def move_to(self, position) -> MoveResult:
        """Absolute move, subject to the same rule on every axis."""
        current = self.position
        for axis, target, now in zip(AXES, position, current):
            allowed, reason = self.allowed(axis, target - now, current)
            if abs(allowed - (target - now)) > 1e-9:
                return MoveResult(False, axis, target - now, reason=reason,
                                  position=current)
        self._busy = True
        try:
            self.robot.move_to_coordinates(tuple(position), min_z_height=1,
                                           verbose=False)
        finally:
            self._busy = False
        self._history.append(("abs", 0.0))
        return MoveResult(True, "xyz", 0.0, position=self.position)

    def undo(self) -> MoveResult:
        """Reverse the last relative step."""
        while self._history:
            axis, delta = self._history.pop()
            if axis == "abs":
                continue
            # record=False, otherwise undo becomes its own next undo target
            return self.move(axis, 1, -delta, record=False)
        return MoveResult(False, "", 0.0, reason="nothing to undo")

    # -- step ---------------------------------------------------------------

    def step_up(self) -> float:
        i = self.steps.index(self.step)
        self.step = self.steps[min(i + 1, len(self.steps) - 1)]
        return self.step

    def step_down(self) -> float:
        i = self.steps.index(self.step)
        self.step = self.steps[max(i - 1, 0)]
        return self.step

    # -- positions ----------------------------------------------------------

    def save_position(self, name: str | None = None) -> str:
        name = name or f"p{len(self.saved) + 1}"
        self.saved[name] = self.position
        return name

    def goto(self, name: str) -> MoveResult:
        if name not in self.saved:
            return MoveResult(False, "", 0.0,
                              reason=f"no saved position {name!r}; "
                                     f"have {sorted(self.saved) or 'none'}")
        return self.move_to(self.saved[name])

    # -- display ------------------------------------------------------------

    def status(self) -> str:
        x, y, z = self.position
        out = self.limits.excursion((x, y, z))
        warn = "".join(f"  {a} OUT by {d:.2f}" for a, d in out.items() if d > 0)
        return (f"({x:8.2f}, {y:8.2f}, {z:7.2f})   step {self.step:g} mm"
                + (f"   saved: {', '.join(sorted(self.saved))}" if self.saved else "")
                + warn)


# ---------------------------------------------------------------------------
# input backends
# ---------------------------------------------------------------------------

_WINDOW_HELP = [
    "arrows / WASD  move x and y",
    "q e            move z down and up",
    "+ -            step size",
    "space          save position",
    "u              undo last step",
    "h              hide or show this help",
    "enter          finish",
]

# waitKeyEx returns platform-specific codes for the arrow keys
_ARROWS = {
    2490368: ("y", +1), 65362: ("y", +1), 63232: ("y", +1),      # up
    2621440: ("y", -1), 65364: ("y", -1), 63233: ("y", -1),      # down
    2424832: ("x", -1), 65361: ("x", -1), 63234: ("x", -1),      # left
    2555904: ("x", +1), 65363: ("x", +1), 63235: ("x", +1),      # right
}
_LETTERS = {"w": ("y", +1), "s": ("y", -1), "a": ("x", -1), "d": ("x", +1),
            "q": ("z", -1), "e": ("z", +1)}


def jog_in_window(controller: JogController, camera=None, *,
                  window: str = "jog", size=(1348, 1011),
                  overlay=None, title: str = ""):
    """Jog while watching the camera, using the window's own key events.

    Preferred over global hotkeys: it needs no elevated permissions, it cannot
    steal arrow keys from other applications, and it only responds while its
    window has focus, so a stray keystroke in the notebook cannot drive the
    robot.

    Returns the position at the moment Enter was pressed.
    """
    import cv2

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, *size)
    show_help = True
    message = ""
    msg_until = 0.0

    try:
        while True:
            frame = None
            if camera is not None:
                ok, frame = camera.read()
                if not ok:
                    frame = None
            if frame is None:
                frame = np.zeros((size[1], size[0], 3), np.uint8)
            else:
                frame = frame.copy()
            h, w = frame.shape[:2]
            cv2.drawMarker(frame, (w // 2, h // 2), (0, 0, 255),
                           cv2.MARKER_CROSS, 60, 2)
            if overlay is not None:
                frame = overlay(frame)

            scale = max(0.6, w / 2000)
            y = int(40 * scale) + 20
            if title:
                cv2.putText(frame, title, (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                            scale, (0, 255, 255), 2)
                y += int(45 * scale)
            cv2.putText(frame, controller.status(), (20, y),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 0), 2)
            y += int(45 * scale)
            if message and time.monotonic() < msg_until:
                cv2.putText(frame, message, (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                            scale, (0, 255, 0), 2)
            y += int(45 * scale)
            if show_help:
                for line in _WINDOW_HELP:
                    cv2.putText(frame, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                                scale * 0.62, (0, 200, 0), 1)
                    y += int(30 * scale)

            cv2.imshow(window, frame)
            key = cv2.waitKeyEx(20)
            if key == -1:
                continue

            action = _ARROWS.get(key)
            char = chr(key & 0xFF).lower() if 0 <= (key & 0xFF) < 128 else ""
            if action is None:
                action = _LETTERS.get(char)

            result = None
            if action is not None:
                result = controller.move(*action)
            elif char in "+=":
                message, msg_until = f"step {controller.step_up():g} mm", time.monotonic() + 1.2
            elif char in "-_":
                message, msg_until = f"step {controller.step_down():g} mm", time.monotonic() + 1.2
            elif char == " ":
                message = f"saved as {controller.save_position()}"
                msg_until = time.monotonic() + 1.5
            elif char == "u":
                result = controller.undo()
            elif char == "h":
                show_help = not show_help
            elif key in (13, 10):
                break

            if result is not None and not result.ok:
                message, msg_until = result.reason, time.monotonic() + 2.0
            elif result is not None and result.clamped:
                message, msg_until = result.reason, time.monotonic() + 1.5
    finally:
        cv2.destroyWindow(window)

    return controller.position


class jog_with_hotkeys:
    """Global arrow-key jogging, as before, but unregistered on exit.

    Use as a context manager. The previous version added hotkeys in its
    constructor and never removed them, so a second instance doubled every
    move; here the bindings live exactly as long as the block.

    Needs the `keyboard` package and elevated permissions on Linux and macOS.
    Prefer jog_in_window unless the notebook has no window open.
    """

    BINDINGS = {"up": ("y", +1), "down": ("y", -1),
                "left": ("x", -1), "right": ("x", +1),
                "ctrl+up": ("z", +1), "ctrl+down": ("z", -1)}

    def __init__(self, controller: JogController, verbose: bool = True):
        self.controller = controller
        self.verbose = verbose
        self._hooks: list = []

    def _report(self, result: MoveResult) -> None:
        if not self.verbose:
            return
        if not result.ok:
            print(f"refused: {result.reason}")
        else:
            print(self.controller.status(), end="\r")

    def __enter__(self) -> JogController:
        import keyboard
        for combo, (axis, direction) in self.BINDINGS.items():
            self._hooks.append(keyboard.add_hotkey(
                combo, lambda a=axis, d=direction: self._report(
                    self.controller.move(a, d))))
        self._hooks.append(keyboard.add_hotkey(
            "+", lambda: print(f"step {self.controller.step_up():g} mm")))
        self._hooks.append(keyboard.add_hotkey(
            "-", lambda: print(f"step {self.controller.step_down():g} mm")))
        self._hooks.append(keyboard.add_hotkey(
            "s", lambda: print(f"saved as {self.controller.save_position()}")))
        return self.controller

    def wait(self, key: str = "esc") -> None:
        import keyboard
        keyboard.wait(key)

    def __exit__(self, *exc) -> None:
        import keyboard
        for hook in self._hooks:
            try:
                keyboard.remove_hotkey(hook)
            except (KeyError, ValueError):
                pass
        self._hooks.clear()