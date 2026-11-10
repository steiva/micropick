"""The program's icon: the window's, from the package, and the exe's and the
installer's, from packaging/."""

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui import app as gui_app                         # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_window_icon_is_the_programs(app):
    icon = gui_app.window_icon()
    assert not icon.isNull()
    assert icon.availableSizes() or icon.pixmap(64).width() == 64


def test_the_build_and_the_installer_carry_the_icon():
    assert (ROOT / "packaging" / "micropick.ico").is_file()
    spec = (ROOT / "packaging" / "micropick.spec").read_text()
    assert "micropick.ico" in spec and "micropick.png" in spec
    iss = (ROOT / "packaging" / "micropick.iss").read_text(encoding="utf-8-sig")
    assert "SetupIconFile=micropick.ico" in iss
