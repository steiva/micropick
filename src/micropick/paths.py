"""Filesystem roots.

Functions rather than module-level constants: a constant is frozen at import
time, so a GUI or a test could not point the package at a different root
without reloading the module.

Directories that the package writes into are created on demand, so a fresh
clone needs no manual mkdir. Directories that hold inputs are not created
silently: if labware/ or ml_models/ is missing, that is a real problem and a
helpful error beats an empty folder that looks correct.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["root", "profiles_dir", "logs_dir", "outputs_dir", "images_dir",
           "clips_dir", "ml_models_dir", "labware_dir", "fixtures_dir",
           "ensure_layout", "describe"]

ENV_VAR = "MICROPICK_ROOT"


def root() -> Path:
    env = os.environ.get(ENV_VAR)
    return Path(env).expanduser().resolve() if env else Path.cwd()


def _writable(name: str, create: bool) -> Path:
    path = root() / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


# -- written by the package --------------------------------------------------

def profiles_dir(create: bool = False) -> Path:
    return _writable("profiles", create)


def logs_dir(create: bool = True) -> Path:
    return _writable("logs", create)


def outputs_dir(create: bool = True) -> Path:
    return _writable("outputs", create)


def images_dir(create: bool = True) -> Path:
    return _writable("outputs/images", create)


def clips_dir(create: bool = True) -> Path:
    return _writable("outputs/clips", create)


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