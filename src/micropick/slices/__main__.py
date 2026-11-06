"""Entry point: `micropick-slices`, or `python -m micropick.slices`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="micropick-slices",
        description="Slice picking under a fixed microscope.")
    parser.add_argument("--mock", action="store_true",
                        help="the mock robot and a drawn blade, no hardware")
    parser.add_argument("--robot-host", metavar="ADDRESS",
                        help="the robot's address for this start, over the "
                             "one in settings.json")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from .. import paths
    from ..config import app_settings
    from ..gui import theme
    from .mock import mock_robot
    from .rig import Rig
    from .store import load_state
    from .window import SliceWindow

    settings, problem = app_settings.load_settings()
    app_settings.apply(settings)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(
                      paths.logs_dir() / f"slices-{date.today()}.log",
                      encoding="utf-8")])
    if problem:
        logging.warning(problem)

    theme.configure_hidpi()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Slice picking")
    theme.apply_theme(app)

    rig = Rig(load_state(), api=mock_robot() if args.mock else None,
              host=args.robot_host or settings.robot_host,
              port=settings.robot_port, mock=args.mock)
    window = SliceWindow(rig, settings.keys)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
