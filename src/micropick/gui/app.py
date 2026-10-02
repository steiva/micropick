"""Constructing and running the application.

Separate from `__main__` so that everything Qt is in one place and the command
line is in another: the window can be brought up from a shell, a test or a
future embedding without going through `sys.argv` and `sys.exit`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from . import theme
from .shell import MainWindow

__all__ = ["Options", "create_app", "run"]


@dataclass(frozen=True)
class Options:
    """What the command line asked for.

    `mock` is not a debug switch: with no robot on the bench it is the mode the
    application is developed in, and everything the GUI does has to work in it.
    """

    profile: str | None = None
    mock: bool = False
    # The robot's address for this start only, over the saved setting.
    robot_host: str | None = None
    # Check the installation and exit, no window (`micropick.selftest`).
    self_test: bool = False


def create_app(argv: list[str] | None = None) -> QApplication:
    """A themed QApplication. Reuses one if it already exists."""
    existing = QApplication.instance()
    if existing is not None:
        theme.apply_theme(existing)
        return existing

    # Before the application, not after: Qt reads the rounding policy when
    # QGuiApplication is constructed and ignores a later change.
    theme.configure_hidpi()

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("micropick")
    app.setApplicationDisplayName("micropick")
    theme.apply_theme(app)
    return app


def run(options: Options) -> int:
    """Show the main window and run the event loop. Returns the exit code.

    Maximised rather than `show()`: every page here is a camera view beside
    a panel of cards, and both want the room. Maximised and not true full
    screen, because the title bar and the taskbar are how an operator gets
    to the notebook and the Opentrons app while the robot is running, and a
    window that hides them is a window that has to be fought.
    """
    # ultralytics pip-installs what it thinks is missing when it is first
    # imported, which is not something a lab computer should find done.
    import os
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    # A packaged build keeps its data in Documents/micropick; its first
    # start puts the labware and weights it shipped with there.
    from .. import paths
    paths.root().mkdir(parents=True, exist_ok=True)
    paths.seed_from_bundle()
    app = create_app()
    window = MainWindow(options)
    window.showMaximized()
    return app.exec()
