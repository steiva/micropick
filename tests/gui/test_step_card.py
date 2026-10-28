"""A numbered step card (`widgets.step_card`, #46): only the current
step's button is the blue one; a done or waiting step's is secondary and
still usable."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

from micropick.gui.theme.factory import secondary_button         # noqa: E402
from micropick.gui.widgets.checklist import BLOCKED, OK, TODO    # noqa: E402
from micropick.gui.widgets.step_card import StepCard             # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _primary(button) -> bool:
    return button.objectName() == "primary" and button.isDefault()


def test_only_the_current_steps_button_is_primary(app):
    host = QWidget()
    card = StepCard(host, 1, "Move the tip")
    button = secondary_button("Move the tip", host)
    card.add_button(button)
    assert card.state == TODO and _primary(button)
    card.set_state(OK)
    assert button.objectName() == "secondary" and not button.isDefault()
    assert button.isEnabled()                      # still usable
    card.set_state(BLOCKED, "First: step 1.")
    assert not _primary(button)
    card.set_state(TODO)
    assert _primary(button)
    host.deleteLater()


def test_a_button_placed_in_a_row_follows_its_card_too(app):
    host = QWidget()
    card = StepCard(host, 2, "Save")
    card.set_state(OK)
    button = secondary_button("Save", host)
    card.own_button(button)                        # laid out by the page
    assert button.objectName() == "secondary"
    card.set_state(TODO)
    assert _primary(button)
    host.deleteLater()
