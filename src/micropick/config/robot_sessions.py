"""Names for the robot's sessions, kept on this computer.

The OT-2 names a run by a 36-character id and nothing else, and on screen
that id says nothing - least of all how old the session is when the robot
has been left on for days. So a session is given a name when this
application creates it, by default the date and time and the profile
("2026-10-02 12:54 lab_main"), and the name is kept here against the id.

One small file, `robot_sessions.json` beside `settings.json`, holding the
last `KEEP` names: a robot only ever has one current session, and a name
older than that is of a session long gone. A session this computer did not
create has no name here; it is shown by when the robot says it was created.
Reading never raises: a missing or unreadable file is no names.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .. import paths
from .store import _write_atomic

__all__ = ["NAMES_FILE", "KEEP", "default_name", "name_of", "remember_name",
           "created_label"]

NAMES_FILE = "robot_sessions.json"
KEEP = 50


def _path() -> Path:
    return paths.root() / NAMES_FILE


def _load() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) \
        else {}


def default_name(profile_name: str | None, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return f"{stamp} {profile_name}" if profile_name else stamp


def name_of(run_id: str | None) -> str | None:
    return _load().get(run_id) if run_id else None


def remember_name(run_id: str, name: str) -> None:
    names = _load()
    names.pop(run_id, None)
    names[run_id] = name                     # newest last
    kept = dict(list(names.items())[-KEEP:])
    _write_atomic(_path(), json.dumps(kept, indent=2) + "\n")


def created_label(created_at: str | None) -> str | None:
    """The robot's `createdAt` (ISO, UTC) as local "YYYY-MM-DD HH:MM"."""
    if not created_at:
        return None
    try:
        when = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.strftime("%Y-%m-%d %H:%M")
