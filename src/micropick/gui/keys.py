"""The keys that drive the robot from a page, and which keys they are.

The jog keys (`widgets.jog_panel`) and Manual control's own (aspirate,
dispense) are actions with a default key each - the arrangement the panel
always had: the arrows for X and Y, PgUp and PgDn for Z. Not every keyboard
has PgUp and PgDn, so Settings rebinds any of them
(`AppSettings.keys`, action id -> key in Qt's portable text). Only the keys
changed from the default are stored, so a default changed here reaches every
computer that kept it.

A default may have aliases - "+" and "=" are one key on most keyboards,
Enter arrives as either - and a rebound action has the one key chosen.
Esc is not offered: it is Stop, window-wide (`shell.MainWindow._stop`).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

__all__ = ["Action", "ACTIONS", "RESERVED", "action", "sequences",
           "key_text", "problems"]


@dataclass(frozen=True)
class Action:
    id: str
    label: str                      # on the Settings page
    defaults: tuple                 # Qt.Key values, the first one shown


ACTIONS = (
    Action("x-", "Move X −", (Qt.Key.Key_Left,)),
    Action("x+", "Move X +", (Qt.Key.Key_Right,)),
    Action("y+", "Move Y +", (Qt.Key.Key_Up,)),
    Action("y-", "Move Y −", (Qt.Key.Key_Down,)),
    Action("z+", "Move Z up", (Qt.Key.Key_PageUp,)),
    Action("z-", "Move Z down", (Qt.Key.Key_PageDown,)),
    Action("step_up", "Larger step", (Qt.Key.Key_Plus, Qt.Key.Key_Equal)),
    Action("step_down", "Smaller step",
           (Qt.Key.Key_Minus, Qt.Key.Key_Underscore)),
    Action("save", "Save point", (Qt.Key.Key_Space,)),
    Action("undo", "Undo last step", (Qt.Key.Key_U,)),
    Action("help", "Show or hide the key list", (Qt.Key.Key_H,)),
    Action("aspirate", "Aspirate (Manual control)", (Qt.Key.Key_A,)),
    Action("dispense", "Dispense (Manual control)", (Qt.Key.Key_D,)),
)

_BY_ID = {a.id: a for a in ACTIONS}

# Keys no action may take: Esc is Stop.
RESERVED = {QKeySequence(Qt.Key.Key_Escape).toString(): "Stop"}


def action(action_id: str) -> Action:
    return _BY_ID[action_id]


def sequences(bindings: dict, action_id: str) -> list[QKeySequence]:
    """The keys of `action_id`: the one chosen in Settings, else the
    defaults with their aliases."""
    chosen = (bindings or {}).get(action_id)
    if chosen:
        return [QKeySequence.fromString(chosen,
                                        QKeySequence.SequenceFormat.PortableText)]
    return [QKeySequence(key) for key in _BY_ID[action_id].defaults]


def key_text(bindings: dict, action_id: str) -> str:
    """The key as shown in the key list: the first one, as the system
    names it."""
    return sequences(bindings, action_id)[0].toString(
        QKeySequence.SequenceFormat.NativeText)


def problems(bindings: dict) -> list[str]:
    """Why `bindings` cannot be used, as sentences; empty when they can."""
    out, taken = [], {}
    for a in ACTIONS:
        for seq in sequences(bindings, a.id):
            text = seq.toString()
            if not text:
                out.append(f"{a.label}: no key.")
            elif text in RESERVED:
                out.append(f"{a.label}: {text} is {RESERVED[text]}.")
            elif text in taken:
                out.append(f"{a.label} and {taken[text]} are both on "
                           f"{seq.toString(QKeySequence.SequenceFormat.NativeText)}.")
            else:
                taken[text] = a.label
    return out
