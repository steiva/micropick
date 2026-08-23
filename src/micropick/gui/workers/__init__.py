"""Running blocking work off the GUI thread.

Everything below `gui` is synchronous and blocking by design — DESIGN section 2
— so the thread is this layer's business. One class does it for all of them.
"""

from .base import Worker

__all__ = ["Worker"]
