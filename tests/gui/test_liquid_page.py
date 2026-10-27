"""Liquid's Run step on a mock session: it runs the plan Use this plan
handed over, all its groups or only one. Making the plan is the Plan
step's (test_liquid_plan_page)."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import (Aspirate, Dispense, Group,    # noqa: E402
                                   Location, Pause, Program)
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as page_module            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    s.load_labware(resolve_definition(PLATE), 5)
    yield s
    s.shutdown()


TRASH = Location(kind="well", slot="5", load_name=PLATE, well="H12")


def _plan(*groups, trash=TRASH) -> Program:
    """Groups of (wells, steps) on the plate in slot 5."""
    return Program(trash=trash, groups=[
        Group(name=f"Group {n + 1}", slot="5", load_name=PLATE,
              wells=list(wells), steps=list(steps))
        for n, (wells, steps) in enumerate(groups)])


def _wait(app, page, timeout_s=10.0):
    import time
    end = time.monotonic() + timeout_s
    while (page.jog.busy or page._running) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_a_run_goes_through_every_well(app, session):
    session.tip = session.tip.__class__(attached=True)
    page = page_module.LiquidHandlingPage(session)
    session.set_liquid_plan(_plan((["A1", "A2"], [
        Aspirate(), Dispense(location=Location(kind="here"))]), trash=None))
    _wait(app, page)
    assert any("no trash" in p for p in page._run_problems())
    session.set_liquid_plan(_plan((["A1", "A2"], [
        Aspirate(), Dispense(location=Location(kind="here"))])))
    assert page._run_problems() == []
    page._confirm_start = lambda: True
    page._start()
    _wait(app, page)
    assert page.state.done == [(0, "A1"), (0, "A2")]
    liquid_calls = [c for c in session.robot.calls
                    if c[0] in ("aspirate", "dispense", "blow_out")]
    assert liquid_calls[0][0] == "blow_out" and liquid_calls[0][2] == "H12"
    aspirates = [c for c in liquid_calls if c[0] == "aspirate"]
    # The two of the program, after the shake that resets the plunger at
    # the trash.
    assert [c[2] for c in aspirates][-2:] == ["A1", "A2"]
    assert "Finished" in page.run_state.text()
    assert page.done.done                               # the green check
    assert "2 wells done" in page.done.detail.text()
    page.deleteLater()


def test_only_one_group_runs_when_the_plan_says_so(app, session):
    session.tip = session.tip.__class__(attached=True)
    page = page_module.LiquidHandlingPage(session)
    steps = [Aspirate(volume_ul=10), Dispense(location=TRASH)]
    session.set_liquid_plan(_plan((["A1", "A2"], steps), (["B1"], steps)),
                            groups=[1])
    assert page._total() == 1
    page._confirm_start = lambda: True
    page._start()
    _wait(app, page)
    assert page.state.done == [(1, "B1")]
    aspirates = [c[2] for c in session.robot.calls if c[0] == "aspirate"]
    assert "A1" not in aspirates and "B1" in aspirates
    page.deleteLater()


def test_problems_say_what_is_missing(app, session):
    session.tip = session.tip.__class__(attached=False)
    page = page_module.LiquidHandlingPage(session)
    problems = page._run_problems()
    assert any("no tip" in p for p in problems)
    assert any("no plan" in p for p in problems)
    page.deleteLater()


def test_the_well_under_the_pointer_is_named_and_the_window_mirrors(app,
                                                                    session):
    page = page_module.LiquidHandlingPage(session)
    session.set_liquid_plan(_plan((["A1", "A2"], [Pause()])))
    page.plate._set_hover("A2")
    group = page.program.groups[0].name
    assert page.hover_line.text() == f"A2 — {group}, to do"
    page.state.done = [(0, "A2")]
    page.plate._set_hover("A1")
    page.plate._set_hover("A2")
    assert page.hover_line.text() == f"A2 — {group}, done"
    page.plate._set_hover("H12")
    assert page.hover_line.text() == "H12 — no group"
    assert page.plate_window.readout.text() == "H12 — no group"
    page._show_plate_window()
    assert page.plate_window.isVisible()
    assert page.plate_window.view.scene() is page.plate.scene()
    assert "slot 5" in page.plate_window.windowTitle()
    page.plate_window.close()                        # hides, keeps the window
    assert not page.plate_window.isVisible()
    page.deleteLater()


def test_the_deck_says_where_the_group_is_and_where_it_goes(app, session):
    from micropick.core.liquid import Aspirate, Dispense, Group
    from micropick.gui.pages.liquid import TRASH_COLOUR, deck_marks
    group = Group(name="Wash", color="#ff0000", slot="5", load_name=PLATE,
                  wells=["A1", "A2"],
                  steps=[Aspirate(),
                         Dispense(location=Location(kind="well", slot="2",
                                                    load_name="res",
                                                    well="A1")),
                         Dispense(location=Location(kind="point",
                                                    point="waste"))])
    trash = Location(kind="well", slot="3", load_name="trash", well="A1")
    marks, elsewhere = deck_marks(group, trash)
    assert marks["5"] == ("#ff0000", "Wash: 2 wells\n1 aspirate", True)
    assert marks["2"] == ("#ff0000", "2 dispense A1", False)
    assert marks["3"] == (TRASH_COLOUR, "trash A1", False)
    assert "6" not in marks                       # the same plate elsewhere
    assert elsewhere == ["Step 3 dispense: point waste."]


def test_steps_name_the_slot_of_each_well(app, session):
    from micropick.core.liquid import Aspirate, describe
    assert "each well of slot 5" in describe(Aspirate(), "5")
    page = page_module.LiquidHandlingPage(session)
    session.set_liquid_plan(_plan((["A1"], [Aspirate()])))
    assert page.wells_heading.text() == "Wells: slot 5"
    assert page.deck.parent() is not None
    assert "slot 5" in page._group_route(page.program.groups[0])
    page.deleteLater()
