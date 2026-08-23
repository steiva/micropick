"""Constructing and running the application.

Separate from `__main__` so that everything Qt is in one place and the command
line is in another: the window can be brought up from a shell, a test or a
future embedding without going through `sys.argv` and `sys.exit`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from . import theme
from .theme.factory import (card, heading, primary_button,
                            secondary_button)

__all__ = ["Options", "create_app", "run"]


@dataclass(frozen=True)
class Options:
    """What the command line asked for.

    `mock` is not a debug switch: with no robot on the bench it is the mode the
    application is developed in, and everything the GUI does has to work in it.
    """

    profile: str | None = None
    mock: bool = False


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


def _window(options: Options) -> QWidget:
    """The placeholder the shell replaces.

    It shows what the command line was understood to mean, so the arguments are
    visible rather than parsed and dropped.
    """
    window = QWidget()
    window.setWindowTitle("micropick")
    window.resize(1400, 900)

    layout = QVBoxLayout(window)
    layout.setContentsMargins(theme.SPACING * 4, theme.SPACING * 4,
                              theme.SPACING * 4, theme.SPACING * 4)
    layout.addStretch(1)

    panel = card(window)
    panel.layout().addWidget(heading("micropick", 1))
    panel.layout().addWidget(QLabel(
        f"profile: {options.profile or '(none)'}\n"
        f"robot:   {'mock' if options.mock else 'real'}"))

    # The two button styles, so that the theme is something to look at rather
    # than something to take on trust. They go with the placeholder.
    buttons = QHBoxLayout()
    buttons.addWidget(primary_button("Primary"))
    buttons.addWidget(secondary_button("Secondary"))
    disabled = secondary_button("Disabled")
    disabled.setEnabled(False)
    buttons.addWidget(disabled)
    buttons.addStretch(1)
    panel.layout().addLayout(buttons)

    layout.addWidget(panel)
    layout.addStretch(1)
    return window


def run(options: Options) -> int:
    """Show the main window and run the event loop. Returns the exit code."""
    app = create_app()
    window = _window(options)
    window.show()
    return app.exec()
