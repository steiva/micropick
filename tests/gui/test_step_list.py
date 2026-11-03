"""StepList without a drag: cards, the keys, the menu, the lock (LIQUID.md
§5, §9 checks 3 and 4). The drags are in `step_list_drags.py`, run from here
in a process of their own: see there why."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt                              # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402
from step_list_drags import (LEFT, Harness, _numbers, _shown_ids,  # noqa: E402, F401
                             app, harness)

from micropick.core.liquid_plan import WaitStep                    # noqa: E402

DRAGS = Path(__file__).with_name("step_list_drags.py")


def test_the_drags_in_a_real_drag_loop():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_")}
    env["QT_QPA_PLATFORM"] = "minimal"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-o", "addopts=", str(DRAGS)],
        env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert " passed" in done.stdout


def test_a_click_on_the_header_opens_and_closes_the_card(harness):
    h = harness()
    card = h.list.card("s1")
    QTest.mouseClick(card.header, LEFT, pos=QPoint(120, 8))
    assert card.is_open and card.body.isVisible()
    QTest.mouseClick(card.header, LEFT, pos=QPoint(120, 8))
    assert not card.is_open and not card.body.isVisible()


def test_only_one_card_is_open_at_a_time(harness):
    h = harness()
    QTest.mouseClick(h.list.card("s1").header, LEFT, pos=QPoint(120, 8))
    QTest.mouseClick(h.list.card("s2").header, LEFT, pos=QPoint(120, 8))
    assert [c.is_open for c in h.list.cards()] == [False, True, False]


def test_a_press_on_the_handle_that_does_not_move_does_nothing(harness):
    h = harness()
    card = h.list.card("s1")
    QTest.mouseClick(card.handle, LEFT, pos=card.handle.rect().center())
    assert not card.is_open
    assert h.moves == []


def test_a_card_is_reused_by_id_and_a_gone_step_loses_its_card(harness):
    h = harness()
    kept = h.list.card("s2")
    h.list.set_steps([h.plan.steps[1]])
    assert h.list.cards() == [kept]
    assert _numbers(h) == ["1"]


def test_alt_down_moves_the_focused_card_and_ctrl_z_restores(harness):
    h = harness()
    card = h.list.card("s1")
    card.setFocus()
    QTest.keyClick(card, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert h.ids == ["s2", "s1", "s3"]
    assert _numbers(h) == ["1", "2", "3"]
    assert card.hasFocus()
    QTest.keyClick(h.windowHandle(), Qt.Key.Key_Z,
                   Qt.KeyboardModifier.ControlModifier)
    assert h.ids == ["s1", "s2", "s3"]
    assert _shown_ids(h) == ["s1", "s2", "s3"]


def test_alt_up_moves_up_and_stops_at_the_end(harness):
    h = harness()
    card = h.list.card("s2")
    QTest.keyClick(card, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    QTest.keyClick(card, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert h.moves == [("s2", 0)]


def test_the_menu_moves_duplicates_and_deletes(harness):
    h = harness()
    duplicates, deletes = [], []
    h.list.duplicateRequested.connect(duplicates.append)
    h.list.deleteRequested.connect(deletes.append)
    card = h.list.card("s2")
    card.action("Move up").trigger()
    assert h.ids == ["s2", "s1", "s3"]
    card.action("Move down").trigger()
    assert h.ids == ["s1", "s2", "s3"]
    card.action("Duplicate").trigger()
    card.action("Delete").trigger()
    assert duplicates == ["s2"] and deletes == ["s2"]


def test_the_menu_cannot_move_past_the_ends(harness):
    h = harness()
    first, last = h.list.card("s1"), h.list.card("s3")
    assert not first.action("Move up").isEnabled()
    assert first.action("Move down").isEnabled()
    assert not last.action("Move down").isEnabled()


def test_cards_read_as_sentences_with_not_finished_when_folded(harness):
    h = harness()
    steps = [*h.plan.steps, WaitStep(id="w", seconds=300)]
    h.list.set_steps(steps, not_finished={"s1"})
    card = h.list.card("s1")
    assert card.title.text() == "Move 10 µl"
    assert [c.text() for c in card.chips] == ["Slot 5 · A1", "choose"]
    assert h.list.card("s2").title.text() == "Empty 1 well"
    assert h.list.card("w").title.text() == "Wait 5 min"
    assert card.warning.isVisible()
    assert not h.list.card("s2").warning.isVisible()
    QTest.mouseClick(card.header, LEFT, pos=QPoint(120, 8))
    assert not card.warning.isVisible()
