"""One look, applied in one place: a fixed dark theme from `tokens`.

`qdarktheme` supplies the base stylesheet for every stock widget, set up
"dark" with its colors replaced by ours (`custom_colors`), so a plain
QComboBox or QScrollBar already sits in the palette. `micropick.qss` is
layered on top through `additional_qss` and styles our own widgets by
object name. It names colors as `@token`, filled from `tokens.COLORS` by
`stylesheet()`: the tokens are the single source, the qss has no hex.

The theme does not follow the operating system (REDESIGN §2, §10). The
earlier rule - no colors in our qss, everything from qdarktheme so both
OS themes worked - is gone with that: there is one palette, and ours.

The fonts are bundled (`theme/fonts`, both OFL) and registered before the
first widget, so a lab computer without Inter shows the same text as the
design.
"""

from __future__ import annotations

import re
from pathlib import Path

import qdarktheme
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

from .tokens import (COLORS, MONO_FALLBACKS, MONO_FONT, SIZES, UI_FALLBACKS,
                     UI_FONT)

__all__ = ["COLORS", "RADIUS", "SIZES", "SPACING", "accent", "apply_theme",
           "color", "configure_hidpi", "load_fonts", "mono_font", "stylesheet",
           "ui_font"]

# The base step. Every margin and gap in the application is a multiple of it,
# so a layout is described by a small integer rather than by a pixel count
# somebody chose once.
SPACING = 8
RADIUS = SIZES["radius"]

_QSS = Path(__file__).with_name("micropick.qss")
_FONTS = Path(__file__).with_name("fonts")
_CHEVRON = Path(__file__).with_name("chevron-down.svg")
_TOKEN = re.compile(r"@([a-z][a-z0-9-]*)")


def configure_hidpi() -> None:
    """Separate from `apply_theme` because Qt requires it earlier.

    The rounding policy is read when QGuiApplication is constructed and ignored
    afterwards, so it cannot live in a function that takes an application. Call
    it before creating one.

    PassThrough rather than the default: the default rounds a 1.5x display up
    to 2 and shrinks everything back with a font adjustment, which on a
    4000x3000 camera frame shows as resampling nobody asked for.
    """
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def load_fonts() -> None:
    """Register the bundled fonts. Safe to call more than once."""
    for path in sorted(_FONTS.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def _family(preferred: tuple[str, ...]) -> str | None:
    available = set(QFontDatabase.families())
    return next((f for f in preferred if f in available), None)


def ui_font(px: int = SIZES["body"], weight: int = 400) -> QFont:
    """The UI face at a pixel size. Pixels, as the design is drawn in them."""
    font = QFont()
    family = _family((UI_FONT, *UI_FALLBACKS))
    if family:
        font.setFamily(family)
    font.setPixelSize(px)
    font.setWeight(QFont.Weight(weight))
    return font


def mono_font(px: int = SIZES["body"], weight: int = 400) -> QFont:
    """Numbers: coordinates, Z, volumes, well names."""
    font = QFont()
    family = _family((MONO_FONT, *MONO_FALLBACKS))
    if family:
        font.setFamily(family)
    else:
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(px)
    font.setWeight(QFont.Weight(weight))
    return font


def color(name: str) -> str:
    """A token's hex, for a stylesheet built in code."""
    return COLORS[name]


def stylesheet() -> str:
    """`micropick.qss` with its `@token` names replaced by their colors."""
    text = _QSS.read_text(encoding="utf-8")
    # The one file a stylesheet points at: a Select's arrow (§3), drawn
    # because the native one touched the border.
    values = {**COLORS, "chevron": f'"{_CHEVRON.as_posix()}"'}
    return _TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), text)


# qdarktheme's own keys, mapped onto the tokens. Its derived shades (hover,
# pressed, selection) are computed from these bases.
_BASE_COLORS = {
    "background": COLORS["bg"],
    "background>panel": COLORS["card"],
    "background>popup": COLORS["raised"],
    "background>list": COLORS["bar"],
    "background>table": COLORS["bar"],
    "background>textarea": COLORS["bar"],
    "background>title": COLORS["bar"],
    "border": COLORS["control-border"],
    "foreground": COLORS["text"],
    "primary": COLORS["accent"],
    "input.background": COLORS["bar"],
    "statusBar.background": COLORS["bar"],
    "toolbar.background": COLORS["bar"],
    "tableSectionHeader.background": COLORS["card"],
    "treeSectionHeader.background": COLORS["card"],
    "list.hoverBackground": COLORS["hover"],
    "popupItem.selectionBackground": COLORS["selected-bg"],
}


def apply_theme(app: QApplication) -> None:
    """Fonts, the base theme in our colors, our stylesheet on top."""
    load_fonts()
    qdarktheme.setup_theme("dark", corner_shape="rounded",
                           custom_colors=_BASE_COLORS,
                           additional_qss=stylesheet())
    app.setFont(ui_font())


def accent() -> str:
    """The accent color: the primary button's fill, the current step."""
    return COLORS["accent"]
