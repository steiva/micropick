"""Done: hidden until a procedure ends well, cleared by the next start."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.widgets.done_banner import DoneBanner         # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_hidden_until_done_and_cleared_again(app):
    banner = DoneBanner()
    assert not banner.done
    banner.show_done("Offset saved.")
    assert banner.done and banner.detail.text() == "Offset saved."
    assert "Done" in banner.title.text()
    banner.clear()
    assert not banner.done
