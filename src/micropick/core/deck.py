"""The OT-2 deck's geometry, in the robot's deck frame and in millimetres.

The numbers are the deck definition's: slot pitch and the slot opening.
`gui.widgets.deck_view` draws the deck with them, and the jog envelope
(`workflows.motion`) raises its floor over a deck module's slots with them,
so they live here, with no Qt, rather than in the widget.
"""

from __future__ import annotations

__all__ = ["SLOT_PITCH_X", "SLOT_PITCH_Y", "SLOT_WIDTH", "SLOT_HEIGHT",
           "nearest_slot", "slot_center"]

SLOT_PITCH_X = 132.5
SLOT_PITCH_Y = 90.5
SLOT_WIDTH = 127.76
SLOT_HEIGHT = 85.48
# The deck's slots; 12 is the fixed trash.
_ALL_SLOTS = range(1, 13)


def slot_center(slot) -> tuple[float, float]:
    """The center of a slot in the robot's deck frame, in millimetres: slot
    1's corner at the origin, x to the right, y to the back."""
    n = int(slot)
    column = (n - 1) % 3
    row = (n - 1) // 3
    return (column * SLOT_PITCH_X + SLOT_WIDTH / 2,
            row * SLOT_PITCH_Y + SLOT_HEIGHT / 2)


def nearest_slot(x: float, y: float) -> int:
    """The slot whose center is nearest to (x, y), in the frame of
    `slot_center`: where a stored pose roughly is, in words an operator
    knows ("≈ slot 8")."""
    def distance(slot: int) -> float:
        cx, cy = slot_center(slot)
        return (x - cx) ** 2 + (y - cy) ** 2
    return min(_ALL_SLOTS, key=distance)
