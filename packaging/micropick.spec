# PyInstaller spec for the Windows build: one folder, no console.
#
# Run through packaging/build.py, which stamps the commit and zips the
# result; `pyinstaller packaging/micropick.spec` alone works too.
#
# One folder, not one file: the detector brings torch, about a gigabyte,
# and a one-file exe unpacks all of it to a temporary folder at every
# start. The packages collected whole are the ones that load data files or
# modules by name at run time, which PyInstaller's import scan cannot see.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# Collecting ultralytics imports it, and on import it pip-installs what it
# thinks is missing into the build environment. Never.
os.environ["YOLO_AUTOINSTALL"] = "false"

ROOT = Path(SPECPATH).parent                     # noqa: F821 (PyInstaller)

datas, binaries, hiddenimports = [], [], []
for package in ("ultralytics", "qdarktheme", "qtawesome", "pyqtgraph",
                "opentrons_shared_data"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

# What a first start copies into Documents/micropick (paths.seed_from_bundle).
datas += [(str(ROOT / "labware"), "labware"),
          (str(ROOT / "ml_models"), "ml_models"),
          (str(ROOT / "src" / "micropick" / "gui" / "theme" / "micropick.qss"),
           "micropick/gui/theme")]
# The robot wrapper is imported inside functions, and the GUI's pages by
# the shell's table.
hiddenimports += ["opentrons_api.ot2_api", "micropick.gui.pages"]

a = Analysis(                                    # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # One Qt binding: the application's is PySide6, and the build refuses
    # two (the lab environment has PyQt6 as well).
    excludes=["tkinter", "IPython", "jupyter", "notebook", "pytest",
              "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)                                # noqa: F821
exe = EXE(                                       # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="micropick",
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(                                  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="micropick",
)
