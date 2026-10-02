"""Entry point: `micropick-gui`, or `python -m micropick.gui`."""

from __future__ import annotations

import argparse
import sys

from .app import Options, run

__all__ = ["main", "parse_args"]


def parse_args(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        prog="micropick-gui",
        description="Desktop control for the microtissue rig.")
    parser.add_argument("--profile", metavar="NAME",
                        help="profile to load at startup; if omitted, none is "
                             "loaded and one is chosen in the application")
    parser.add_argument("--mock", action="store_true",
                        help="run against the mock robot and a synthetic ArUco "
                             "scene instead of the bench")
    parser.add_argument("--robot-host", metavar="ADDRESS",
                        help="the robot's address for this start, over the "
                             "one saved on the Settings page (an IP, a name, "
                             "or host:port)")
    args = parser.parse_args(argv)
    return Options(profile=args.profile, mock=args.mock,
                   robot_host=args.robot_host)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
