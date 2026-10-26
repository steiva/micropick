"""The design tokens: every color and size the application draws with.

The single source (docs/redesign/REDESIGN.md §2, §12.6). `micropick.qss`
names them as `@token` and `stylesheet()` fills them in; a widget that
paints by hand takes them from here as `QColor(COLORS["ok"])`. Nothing
else in the application names a color by its hex.

The theme is dark and fixed: it does not follow the operating system. The
redesign was drawn and checked for one palette, and the colors carry
meaning (blue an action, green done, yellow waiting, red only Stop), which
a light variant would have had to keep on another background.
"""

from __future__ import annotations

__all__ = ["COLORS", "GROUP_COLORS", "MONO_FALLBACKS", "MONO_FONT", "SIZES",
           "UI_FALLBACKS", "UI_FONT"]

COLORS: dict[str, str] = {
    # Surfaces, darkest first.
    "bg": "#0F1114",              # window
    "steps-bg": "#111317",        # step bar
    "bar": "#14171B",             # top bar, footer, inner list boxes
    "card": "#171A1F",
    "raised": "#1B1E24",          # popovers, dialogs, task tiles
    "hover": "#1E2228",           # hover on transparent buttons
    "selected-bg": "#1A2130",     # selected row or tile, current step
    # Lines.
    "line": "#22262C",            # dividers, panel borders
    "card-border": "#23272E",
    "control-border": "#2E333B",  # buttons, inputs
    # Text, strongest first.
    "text": "#E8EAED",
    "text-2": "#C9CED6",          # secondary text, icon buttons
    "text-3": "#B3B9C2",          # descriptions
    "muted": "#8A919B",           # CAPS captions, units
    "faint": "#5B626C",           # chevrons, empty slots
    # Meaning.
    "accent": "#6CA6FF",          # action, current step, focus
    "accent-hover": "#8DB8FF",
    "on-accent": "#0B1220",
    "accent-bg": "#17233A",       # busy pill, info banner
    "info-text": "#C9DCFF",
    "ok": "#3DD68C",              # done, saved
    "ok-bg": "#16261E",
    "warn": "#F5B841",            # waiting, needs a choice
    "warn-bg": "#2A2213",
    "warn-text": "#F5D48A",
    "danger": "#D93A41",          # Stop run only
    "danger-hover": "#E5484F",
    "danger-text": "#FF8A8F",     # destructive buttons' text
    # Kinds of labware.
    "plate": "#A99BFA",
    "plate-bg": "#221E35",
    "planned": "#3A3360",         # planned, still empty wells
    "tips": "#4FC9B8",
    "tips-bg": "#15282A",
    "dish": "#EE93BE",
    "dish-bg": "#2C1A24",
    "point": "#4FC9E0",           # memo points
}

# Liquid groups, in order; the list repeats past its end.
GROUP_COLORS: tuple[str, ...] = (
    "#4FC9B8", "#EE93BE", "#F0A35E", "#A99BFA", "#6CA6FF", "#E5D86A",
    "#7FD67A", "#F28B82")

# Pixels. Sized for a mouse on a 1920x1080 screen (§12.6), not for touch.
SIZES: dict[str, int] = {
    "grid": 4,
    "top-bar": 52,
    "step-bar": 48,
    "footer": 60,
    "button": 36,
    "button-compact": 28,
    "input": 32,
    "jog-button": 40,
    "radius": 8,
    "radius-card": 12,
    "side-min": 400,
    "side-max": 520,
    "caption": 11,     # CAPS captions
    "small": 12,
    "body": 14,
    "card-title": 16,
    "panel-title": 20,
    "page-title": 26,
    "readout": 72,     # the one big number (Z on Dish bottom)
}

# Bundled in theme/fonts and registered at start (`theme.load_fonts`).
UI_FONT = "Inter"
MONO_FONT = "JetBrains Mono"
UI_FALLBACKS = ("Segoe UI Variable Text", "Segoe UI")
MONO_FALLBACKS = ("Consolas", "Menlo")
