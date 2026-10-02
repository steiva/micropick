"""Filesystem roots.

Functions rather than module-level constants: a constant is frozen at import
time, so a GUI or a test could not point the package at a different root
without reloading the module.

Directories that the package writes into are created on demand, so a fresh
clone needs no manual mkdir. Directories that hold inputs are not created
silently: if labware/ or ml_models/ is missing, that is a real problem and a
helpful error beats an empty folder that looks correct.

Three of the written folders - outputs, logs and images - can be put
elsewhere (`set_overrides`, from the application's settings). Everything
that writes asks these functions, so a moved folder takes effect for the
next file. Clips follow outputs; profiles stay under the root, since the
profile is the bench and not what the application produces.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["root", "frozen", "bundle_dir", "seed_from_bundle", "profiles_dir",
           "logs_dir", "outputs_dir", "images_dir",
           "clips_dir", "ml_models_dir", "labware_dir", "fixtures_dir",
           "ensure_layout", "describe", "set_overrides", "overrides"]

ENV_VAR = "MICROPICK_ROOT"

# Folders moved out of the root by the settings, by name. Empty is the
# default layout.
_OVERRIDES: dict[str, Path] = {}


def set_overrides(*, outputs=None, logs=None, images=None) -> None:
    """Put outputs, logs and images elsewhere; None keeps the default."""
    _OVERRIDES.clear()
    for name, value in (("outputs", outputs), ("logs", logs),
                        ("images", images)):
        if value:
            _OVERRIDES[name] = Path(value).expanduser()


def overrides() -> dict[str, Path]:
    return dict(_OVERRIDES)


def _moved(name: str, default: str, create: bool) -> Path:
    path = _OVERRIDES.get(name)
    if path is None:
        return _writable(default, create)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def frozen() -> bool:
    """Running as a packaged build (PyInstaller), not from the source tree."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path | None:
    """Where a packaged build keeps what it shipped with - labware and the
    model weights - read-only. None from the source tree."""
    if not frozen():
        return None
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def root() -> Path:
    """`MICROPICK_ROOT` if set. Otherwise, from the source tree, the working
    directory (the repository, as before); from a packaged build, the
    user's Documents/micropick - an installed program's own folder is not
    writable, and its working directory is wherever it was started from."""
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    if frozen():
        return Path.home() / "Documents" / "micropick"
    return Path.cwd()


def seed_from_bundle() -> list[Path]:
    """A packaged build's first start: copy the labware and the weights it
    shipped with into the root, where they can be added to and replaced.
    Nothing already there is touched. Returns what was copied."""
    import shutil
    bundle = bundle_dir()
    if bundle is None:
        return []
    copied = []
    for name in ("labware", "ml_models"):
        source, target = bundle / name, root() / name
        if source.is_dir() and not target.exists():
            shutil.copytree(source, target)
            copied.append(target)
    return copied


def _writable(name: str, create: bool) -> Path:
    path = root() / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


# -- written by the package --------------------------------------------------

def profiles_dir(create: bool = False) -> Path:
    return _writable("profiles", create)


def logs_dir(create: bool = True) -> Path:
    return _moved("logs", "logs", create)


def outputs_dir(create: bool = True) -> Path:
    return _moved("outputs", "outputs", create)


def images_dir(create: bool = True) -> Path:
    if "images" in _OVERRIDES:
        return _moved("images", "", create)
    path = outputs_dir(create) / "images"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def clips_dir(create: bool = True) -> Path:
    path = outputs_dir(create) / "clips"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def fixtures_dir(create: bool = True) -> Path:
    return _writable("tests/fixtures", create)


# -- supplied by the user ----------------------------------------------------

def ml_models_dir() -> Path:
    return root() / "ml_models"


def labware_dir() -> Path:
    return root() / "labware"


# -- helpers -----------------------------------------------------------------

def ensure_layout() -> list[Path]:
    """Create every directory the package writes into. Safe to call repeatedly."""
    made = []
    for fn in (profiles_dir, logs_dir, outputs_dir, images_dir, clips_dir,
               fixtures_dir):
        path = fn(create=True) if fn is profiles_dir else fn()
        made.append(path)
    return made


def describe() -> str:
    """One line per directory, with whether it exists. For a first run."""
    entries = [
        ("root", root()),
        ("profiles", profiles_dir()),
        ("labware", labware_dir()),
        ("ml_models", ml_models_dir()),
        ("outputs", outputs_dir(create=False)),
        ("logs", logs_dir(create=False)),
    ]
    width = max(len(n) for n, _ in entries)
    lines = []
    for name, path in entries:
        mark = "ok     " if path.exists() else "MISSING"
        lines.append(f"  {name:<{width}}  {mark}  {path}")
    if os.environ.get(ENV_VAR) is None:
        lines.append(f"  ({ENV_VAR} is not set, so root is the working directory)")
    return "\n".join(lines)