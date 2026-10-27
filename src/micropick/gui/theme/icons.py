"""Line icons, drawn from SVG in the color they are asked for.

The shapes are the prototype's (24x24, stroked, round caps), kept here as
path data so an icon follows the text it sits beside: `icon("lock",
COLORS["text-2"])` for a quiet button, the same shape in `on-accent` on a
primary one. A filled variant (`fill=True`) is how a toggle shows it is on
(Light, Lock), as §1.1 asks.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

__all__ = ["ICONS", "icon", "pixmap"]

# The body of a 24x24 SVG. `{fill}` marks the part a filled variant fills.
ICONS: dict[str, str] = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "chevron-left": '<path d="m15 6-6 6 6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "light": ('<path d="M9 18h6"/><path d="M10 21h4"/>'
              '<path fill="{fill}" d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 '
              '2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3Z"/>'),
    "lock": ('<rect fill="{fill}" x="4" y="11" width="16" height="10" rx="2"/>'
             '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
    "park": ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
             '<path d="M8 16l8-8"/><path d="M11 8h5v5"/>'),
    "settings": (
        '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 '
        '1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 '
        '0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 '
        '0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 '
        '0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 '
        '0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 '
        '1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 '
        '0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 '
        '1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>'),
    "next": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "back": '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    "ring": '<circle cx="12" cy="12" r="8"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "shake": '<path d="M4 12c2-3 4-3 6 0s4 3 6 0 4-3 4-3"/>',
    "play": '<path fill="{fill}" d="M7 5v14l12-7z"/>',
    "stop": '<rect fill="{fill}" x="5" y="5" width="14" height="14" rx="2"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "eye": ('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/>'
            '<circle cx="12" cy="12" r="3"/>'),
    "grip": ('<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/>'
             '<circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/>'
             '<circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>'),
    "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "arrow-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "arrow-left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "keyboard": ('<rect x="2" y="6" width="20" height="12" rx="2"/>'
                 '<path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01'
                 'M7 14h10"/>'),
    "tip": '<path d="M9 3h6v5l-2 13h-2L9 8Z"/>',
    "plate": ('<path d="M3 6h18v12H3z"/><path d="M7 10h.01M11 10h.01'
              'M15 10h.01M7 14h.01M11 14h.01M15 14h.01"/>'),
    "dish": ('<path d="M3 12a9 4 0 1 0 18 0a9 4 0 1 0 -18 0"/>'
             '<path d="M3 12v2a9 4 0 0 0 18 0v-2"/>'),
}


def _svg(name: str, color: str, fill: bool, stroke: float) -> bytes:
    body = ICONS[name].replace("{fill}", color if fill else "none")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
            ).encode()


def pixmap(name: str, color: str, size: int = 20, *, fill: bool = False,
           stroke: float = 1.8, ratio: float = 2.0) -> QPixmap:
    """The icon at `size` logical pixels, rendered for a `ratio` screen."""
    renderer = QSvgRenderer(QByteArray(_svg(name, color, fill, stroke)))
    side = round(size * ratio)
    image = QPixmap(side, side)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, side, side))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


def icon(name: str, color: str, size: int = 20, *, fill: bool = False,
         stroke: float = 1.8, disabled: str | None = None) -> QIcon:
    """A QIcon in `color`, with `disabled` (else a faint gray) when off."""
    from .tokens import COLORS
    result = QIcon()
    result.addPixmap(pixmap(name, color, size, fill=fill, stroke=stroke))
    result.addPixmap(pixmap(name, disabled or COLORS["faint"], size,
                            fill=fill, stroke=stroke), QIcon.Mode.Disabled)
    return result


def size(px: int = 20) -> QSize:
    return QSize(px, px)
