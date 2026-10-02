"""The robot's clock, set from this computer's.

An OT-2 on the direct cable has no network time and no battery-backed
clock worth trusting: the bench robot reported July 2025 in October 2026.
Everything it stamps - a session's `createdAt`, its logs - is then wrong,
and a session left on the robot is shown by when it was started
(`gui.session.RunState.label`). So at connect the robot is asked its time
(GET /system/time) and, when it is off by more than `TOLERANCE_S`, told
this computer's (PUT /system/time).

Never raises: a clock is not worth a failed connect. A robot whose clock is
kept by network time refuses the PUT (409) and is left alone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

__all__ = ["TOLERANCE_S", "robot_time", "sync_clock"]

log = logging.getLogger(__name__)

TOLERANCE_S = 60.0
TIMEOUT_S = 5.0


def _parse(text: str) -> datetime | None:
    try:
        stamp = json.loads(text)["data"]["systemTime"]
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def robot_time(api) -> datetime | None:
    """The robot's clock, or None if it cannot be read."""
    import requests
    try:
        response = requests.get(f"{api.BASE_URL}/system/time",
                                headers=api.HEADERS, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        log.warning("could not read the robot's clock: %s", exc)
        return None
    if not response.ok:
        log.warning("could not read the robot's clock: HTTP %s",
                    response.status_code)
        return None
    return _parse(response.text)


def sync_clock(api, now: datetime | None = None) -> str:
    """Set the robot's clock to this computer's if it is off. Returns what
    was done, in words, for the log."""
    import requests
    now = now or datetime.now(timezone.utc)
    theirs = robot_time(api)
    if theirs is None:
        return "robot clock: not read"
    off = (theirs - now).total_seconds()
    if abs(off) <= TOLERANCE_S:
        return f"robot clock: right ({off:+.0f} s)"
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        response = requests.put(
            f"{api.BASE_URL}/system/time", headers=api.HEADERS,
            json={"data": {"id": "time", "systemTime": stamp}},
            timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        return f"robot clock: {off / 86400:+.1f} days off, not set: {exc}"
    if response.status_code == 409:
        return (f"robot clock: {off / 86400:+.1f} days off, but kept by "
                f"network time; left alone")
    if not response.ok:
        return (f"robot clock: {off / 86400:+.1f} days off, not set: HTTP "
                f"{response.status_code} {response.text[:200]}")
    after = _parse(response.text) or robot_time(api)
    return (f"robot clock set from {theirs:%Y-%m-%d %H:%M} to "
            f"{(after or now):%Y-%m-%d %H:%M} UTC")
