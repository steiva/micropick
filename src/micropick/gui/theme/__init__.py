"""One look, applied in one place.

`qdarktheme` supplies the base stylesheet and follows the operating system's
light/dark setting; `micropick.qss` is layered on top through `additional_qss`
rather than by concatenating stylesheets, because "auto" re-applies the base on
every OS theme change and a stylesheet set separately would be dropped the
first time that happened.

**`micropick.qss` contains no colours, and this is not a style preference.**
Two facts about `qdarktheme` 2.x make anything else wrong:

* `setup_theme` installs `load_palette(..., for_stylesheet=True)`, which is a
  placeholder palette — its `Window` and `Base` are the same grey in the light
  and the dark theme, and only `Text` differs. Every colour qdarktheme actually
  shows is a literal in the generated stylesheet. So `palette(base)` in our qss
  resolves against numbers that mean nothing, and it renders a dark card on a
  light background: unreadable in exactly the mode it was supposed to save.
* `additional_qss` is appended *after* the template is expanded
  (`stylesheet += additional_qss`), so qdarktheme's own colour placeholders do
  not reach it either.

What is left is to describe our widgets in terms qdarktheme already colours —
`QPushButton:default` for the accented button, a `Panel`-shaped `QFrame` for a
card surface — and to keep our file to geometry. Nothing then has to be kept in
step with the theme, because nothing here knows what colour it is. See
`factory.py`, which is where those terms are attached.
"""

from __future__ import annotations

from pathlib import Path

import qdarktheme
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

__all__ = ["SPACING", "RADIUS", "FONT_FAMILIES", "FONT_SIZE_PT",
           "configure_hidpi", "apply_theme", "stylesheet"]

# The base step. Every margin and gap in the application is a multiple of it,
# so a layout is described by a small integer rather than by a pixel count
# somebody chose once.
SPACING = 8
RADIUS = 8

# In preference order. The first family the system actually has wins; if none
# of them is installed the system UI font is used, which is why the list does
# not need a generic entry.
FONT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "Inter")
FONT_SIZE_PT = 10

_QSS = Path(__file__).with_name("micropick.qss")


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


def _ui_font() -> QFont:
    """The first available family, at the size the whole application uses.

    Qt substitutes silently for a family it does not have, which is usually
    fine and occasionally gives a serif face on a machine with a thin font set.
    Asking the database first means the fallback is one this module chose.
    """
    available = set(QFontDatabase.families())
    for family in FONT_FAMILIES:
        if family in available:
            return QFont(family, FONT_SIZE_PT)
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPointSize(FONT_SIZE_PT)
    return font


def stylesheet() -> str:
    return _QSS.read_text(encoding="utf-8")


def apply_theme(app: QApplication, *, theme: str = "auto") -> None:
    """Base theme, our geometry on top, and the UI font."""
    qdarktheme.setup_theme(theme, corner_shape="rounded",
                           additional_qss=stylesheet())
    app.setFont(_ui_font())
