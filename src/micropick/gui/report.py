"""A report: everything needed to see what happened, in one file to send.

When something goes wrong at the bench, the person who can fix it is
usually not there. The log page forgets on close and the profile is a
folder of JSON nobody should have to find, so this gathers them into one
zip in Outputs/reports: the last days' log files (`log_bridge.install_file`),
the run logs beside them, the profile's own files (not its history),
this computer's settings, and a summary of what the application is
attached to right now. Nothing is sent anywhere; the operator decides
where the zip goes.
"""

from __future__ import annotations

import logging
import platform
import sys
import time
import zipfile
from pathlib import Path

from .. import paths

__all__ = ["save_report", "save_report_asking", "summary", "REPORT_DAYS",
           "MAX_FILE_MB"]

log = logging.getLogger(__name__)

# How far back log files are taken, and the largest one taken whole.
REPORT_DAYS = 3
MAX_FILE_MB = 50


def summary(session) -> str:
    """What the application is attached to, as plain lines."""
    lines = [f"report made {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"python {sys.version.split()[0]} on {platform.platform()}",
             f"robot: {session.robot_state} at {session.robot_address}"]
    profile = session.profile
    lines.append(f"profile: {profile.name if profile else 'none'}"
                 + (f" ({profile.path})" if profile else ""))
    state = session.run_state
    if state is not None:
        lines.append(state.describe())
    tip = session.tip
    lines.append(f"tip: {tip}")
    for problem in session.deck_problems():
        lines.append(f"deck problem: {problem.describe()}")
    lines.append(f"open cameras: {', '.join(session.open_cameras) or 'none'}")
    routine = session.routine
    if routine is not None:
        try:
            lines.append("plate plan:\n" + routine.summary())
        except Exception as exc:                     # noqa: BLE001
            lines.append(f"plate plan: could not summarise ({exc})")
    return "\n".join(lines) + "\n"


def _recent(directory: Path, days: int) -> list[Path]:
    if not directory.is_dir():
        return []
    since = time.time() - days * 86400
    return sorted(p for p in directory.iterdir()
                  if p.is_file() and p.stat().st_mtime >= since
                  and p.stat().st_size <= MAX_FILE_MB * 1024 * 1024)


def save_report(session) -> Path:
    """Write the zip and return where it is. Blocking, briefly: a few
    megabytes of text."""
    out = paths.outputs_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"report_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.txt", summary(session))
        for item in _recent(paths.logs_dir(create=False), REPORT_DAYS):
            zf.write(item, f"logs/{item.name}")
        profile = session.profile
        if profile is not None and Path(profile.path).is_dir():
            for item in sorted(Path(profile.path).glob("*.json")):
                zf.write(item, f"profile/{item.name}")
        for name in ("settings.json", "robot_sessions.json"):
            item = paths.root() / name
            if item.is_file():
                zf.write(item, name)
    log.info("report saved to %s", path)
    return path


def save_report_asking(session, parent=None) -> Path | None:
    """`save_report`, then say where it is and offer to open the folder.
    For a button: a failure is a message box, not an exception."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

    try:
        path = save_report(session)
    except Exception as exc:                         # noqa: BLE001
        log.error("report not saved: %s", exc)
        QMessageBox.warning(parent, "Report", f"The report could not be "
                                              f"saved: {exc}")
        return None
    box = QMessageBox(parent)
    box.setWindowTitle("Report saved")
    box.setText("A report was saved. Send this file to whoever helps you "
                "with the robot:")
    box.setInformativeText(str(path))
    opener = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if box.clickedButton() is opener:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
    return path
