"""Calls per second on every screen of the mock GUI (GUI audit, step 2).

    conda run -n lab python scripts/audit/gui/probe.py --seconds 30 --out probe.json
    conda run -n lab python scripts/audit/gui/probe.py --seconds 10 --events

Builds the real MainWindow with `Options(mock=True)` (mock robot, synthetic
cameras; `MICROPICK_ROOT` is a scratch folder, so no profile of the user's is
read or written), connects the mock robot, and for each screen measures an
idle window and an active one (jog keys at 20 Hz). At the end it sits on Home
and reports what still runs of the screens that are hidden.

What is counted:
- every function of the program and every PySide method called from Python,
  through cProfile on the GUI thread (calls, own time, time with callees);
- `setText` of labels and buttons, `setStyleSheet`, `setToolTip`,
  `setEnabled`, `setVisible`: all calls and those that changed nothing;
- with `--events`, every Qt event by type and receiver class (slow: each
  event passes through Python; use it for counts, not for times).

Qt platform: `offscreen` unless QT_QPA_PLATFORM says otherwise. Paint events
are delivered there; what a paint costs on a real screen is not measured.
"""
from __future__ import annotations

import argparse
import cProfile
import collections
import json
import os
import pstats
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MICROPICK_ROOT", tempfile.mkdtemp(prefix="micropick-audit-"))
os.environ.setdefault("YOLO_AUTOINSTALL", "false")

from PySide6.QtCore import QEvent, QObject, Qt, QTimer            # noqa: E402
from PySide6.QtTest import QTest                                  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication,     # noqa: E402
                               QLabel, QWidget)

SETTERS: collections.Counter = collections.Counter()      # (what, class, changed)
EVENTS: collections.Counter = collections.Counter()
PROFILE: list = [None]


def instrument_setters() -> None:
    def patch(cls, name, getter):
        original = getattr(cls, name)

        def setter(self, value, *rest):
            try:
                same = getter(self) == value
            except Exception:                                   # noqa: BLE001
                same = False
            SETTERS[(f"{cls.__name__}.{name}", type(self).__name__,
                     "same" if same else "changed")] += 1
            return original(self, value, *rest)
        setattr(cls, name, setter)

    patch(QLabel, "setText", lambda w: w.text())
    patch(QAbstractButton, "setText", lambda w: w.text())
    patch(QWidget, "setStyleSheet", lambda w: w.styleSheet())
    patch(QWidget, "setToolTip", lambda w: w.toolTip())
    patch(QWidget, "setEnabled", lambda w: w.isEnabled())
    patch(QWidget, "setVisible", lambda w: not w.isHidden())


class EventCounter(QObject):
    WATCHED = {QEvent.Type.Paint, QEvent.Type.LayoutRequest, QEvent.Type.Timer,
               QEvent.Type.UpdateRequest, QEvent.Type.Resize, QEvent.Type.Move,
               QEvent.Type.MetaCall, QEvent.Type.StyleChange,
               QEvent.Type.Polish, QEvent.Type.PolishRequest,
               QEvent.Type.FontChange, QEvent.Type.PaletteChange,
               QEvent.Type.UpdateLater, QEvent.Type.Show, QEvent.Type.Hide}

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in self.WATCHED:
            EVENTS[(kind.name, type(watched).__name__)] += 1
        return False


def reset():
    SETTERS.clear()
    EVENTS.clear()
    PROFILE[0] = cProfile.Profile()
    PROFILE[0].enable()


def _name(key) -> str | None:
    """"Class.method" for the program's own code, "Qt Class.method" for a
    PySide method called from Python; None for the rest."""
    filename, line, function = key
    if "micropick" in filename and "scripts/audit" not in filename:
        short = filename.split("micropick/")[-1]
        return f"{short}:{line} {function}"
    if filename == "~" and "PySide6" in function:
        # <method 'setText' of 'PySide6.QtWidgets.QLabel' objects>
        parts = function.split("'")
        if len(parts) >= 4:
            return f"Qt {parts[3].split('.')[-1]}.{parts[1]}"
    return None


def snapshot(seconds: float) -> dict:
    profile = PROFILE[0]
    profile.disable()
    calls = {}
    for key, (_cc, ncalls, tottime, cumtime, _callers) in \
            pstats.Stats(profile).stats.items():
        name = _name(key)
        if name is not None:
            calls[name] = [ncalls, round(tottime * 1000, 2),
                           round(cumtime * 1000, 2)]
    return {
        "seconds": seconds,
        "calls": calls,
        "setters": {" | ".join(k): v for k, v in SETTERS.items()},
        "events": {" | ".join(k): v for k, v in EVENTS.items()},
    }


def spin(app, seconds: float, every_ms: int = 0, action=None) -> float:
    """Run the event loop for `seconds`; `action()` every `every_ms`."""
    start = time.perf_counter()
    next_action = start
    while (now := time.perf_counter()) - start < seconds:
        if action is not None and now >= next_action:
            action()
            next_action = now + every_ms / 1000
        app.processEvents()
        time.sleep(0.001)
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--events", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--only", default="", help="comma list of screens")
    parser.add_argument("--fix", default="",
                        help="A or B: apply fit_fix.py's candidate for GUI-01 "
                             "first, to see what runs under the loop")
    args = parser.parse_args()

    instrument_setters()
    if args.fix:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fit_fix
        fit_fix.apply(args.fix)
    from micropick.config.labware import resolve_definition
    from micropick.gui.app import Options, create_app
    from micropick.gui.shell import MainWindow

    app = create_app([])
    window = MainWindow(Options(mock=True))
    window.resize(1700, 1000)
    window.show()
    session = window.session
    session.probe_robot()
    session.new_run()
    session.load_labware(resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    session.load_labware(resolve_definition("opentrons_96_tiprack_300ul"), 1)
    session.pick_up_tip(1, "A1")
    spin(app, 2.0)
    if args.events:
        counter = EventCounter()
        app.installEventFilter(counter)

    toggle = [0]

    def jog_keys():
        key = (Qt.Key.Key_Right, Qt.Key.Key_Left)[toggle[0] % 2]
        toggle[0] += 1
        QTest.keyClick(window, key)

    def step(task, name):
        return lambda: window.open_task_step(task, name)

    screens = [
        ("home", window.open_home),
        ("robot setup", window.open_robot_setup),
        ("auto: deck", step("auto", "deck")),
        ("auto: tip", step("auto", "tip")),
        ("auto: dish bottom", step("auto", "dish_bottom")),
        ("auto: plan", step("auto", "plan")),
        ("auto: well center", step("auto", "well_center")),
        ("auto: run (picking)", step("auto", "picking")),
        ("manual: run", step("manual", "work")),
        ("liquid: plan", step("liquid", "liquid_plan")),
        ("liquid: run", step("liquid", "run")),
        ("sheet: upper camera", lambda: (window.open_robot_setup(),
                                         window.open_setup_item("upper_camera", from_setup=True))),
        ("sheet: camera calibration", lambda: (window.open_robot_setup(),
                                               window.open_setup_item("camera_calibration", from_setup=True))),
        ("sheet: calibration position", lambda: (window.open_robot_setup(),
                                                 window.open_setup_item("calibration_position", from_setup=True))),
        ("sheet: settings, log", lambda: (window.open_home(), window.open_settings("log"))),
    ]
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    results = {"fix": args.fix, "platform": app.platformName(),
               "screens": {}}
    for name, show in screens:
        if only and name not in only:
            continue
        try:
            show()
        except Exception as exc:                                 # noqa: BLE001
            print(f"!! {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        spin(app, 2.5)                       # cameras open, first pose read
        need = getattr(window, "_need", None)
        reset()
        seconds = spin(app, args.seconds)
        idle = snapshot(seconds)
        reset()
        seconds = spin(app, args.seconds, every_ms=50, action=jog_keys)
        active = snapshot(seconds)
        results["screens"][name] = {"need": getattr(need, "text", None),
                                    "idle": idle, "active": active}
        print(f"measured {name}", file=sys.stderr)

    # Everything visited, now hidden: what still runs with Home on screen.
    window.open_home()
    spin(app, 2.5)
    reset()
    seconds = spin(app, args.seconds)
    results["hidden"] = snapshot(seconds)

    window.close()
    spin(app, 0.5)
    text = json.dumps(results, indent=1)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
