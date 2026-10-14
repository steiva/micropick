"""The Liquid handling page on a mock session: groups from the plate map,
steps, the program saved as it is edited, and a run to the end."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Location, Program              # noqa: E402
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


def _wait(app, page, timeout_s=10.0):
    import time
    end = time.monotonic() + timeout_s
    while (page.jog.busy or page._running) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_a_group_is_made_from_the_selection(app, session):
    page = page_module.LiquidHandlingPage(session)
    assert page.plate_choice.count() == 1
    page.plate.set_selection({"A1", "A2", "A3"})
    page._new_group()
    group = page.program.groups[0]
    assert (group.slot, group.load_name) == ("5", PLATE)
    assert sorted(group.wells) == ["A1", "A2", "A3"]
    # A well is in one group of its plate at most.
    page.plate.set_selection({"A3", "B1"})
    page._new_group()
    assert sorted(page.program.groups[0].wells) == ["A1", "A2"]
    assert sorted(page.program.groups[1].wells) == ["A3", "B1"]
    page.deleteLater()


def test_steps_are_added_edited_and_saved(app, session, tmp_path):
    page = page_module.LiquidHandlingPage(session)
    page.plate.set_selection({"A1"})
    page._new_group()
    page._add_step("aspirate")
    page._add_step("dispense")
    assert [s.action for s in page.program.groups[0].steps] == [
        "aspirate", "dispense"]
    page.steps.setCurrentRow(0)
    page.editor.volume.setValue(25.0)
    assert page.program.groups[0].steps[0].volume_ul == 25.0
    page._save_current()
    saved = Program.load(page_module.programs_dir() / page_module.CURRENT_FILE)
    assert saved == page.program
    # And it comes back on the next start.
    again = page_module.LiquidHandlingPage(session)
    assert again.program == page.program
    page.deleteLater()
    again.deleteLater()


def test_a_run_goes_through_every_well(app, session):
    session.tip = session.tip.__class__(attached=True)
    page = page_module.LiquidHandlingPage(session)
    page.plate.set_selection({"A1", "A2"})
    page._new_group()
    page._add_step("aspirate")
    page._add_step("dispense")
    page.program.groups[0].steps[1].location = Location(kind="here")
    _wait(app, page)
    assert any("no trash" in p for p in page._run_problems())
    page.trash.set_location(Location(kind="well", slot="5", load_name=PLATE,
                                     well="H12"))
    page._trash_edited()
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


def test_problems_say_what_is_missing(app, session):
    session.tip = session.tip.__class__(attached=False)
    page = page_module.LiquidHandlingPage(session)
    page._adopt(Program())
    problems = page._run_problems()
    assert any("no tip" in p for p in problems)
    assert any("no groups" in p for p in problems)
    page.deleteLater()


def test_a_well_location_on_the_deck_is_not_marked_missing(app, session):
    from micropick.core.liquid import Dispense
    session.load_labware(resolve_definition("nest_1_reservoir_195ml"), 2)
    page = page_module.LiquidHandlingPage(session)
    page.plate.set_selection({"A1"})
    page._new_group()
    page._add_step("dispense")
    step = Dispense(location=Location(kind="well", slot="2",
                                      load_name="nest_1_reservoir_195ml",
                                      well="A1"))
    page.program.groups[-1].steps[0] = step
    page._show_steps(select=0)
    where = page.editor.where
    assert "not on the deck" not in where.labware.currentText()
    assert where.location() == step.location
    page.deleteLater()


def test_the_well_under_the_pointer_is_named_and_the_window_mirrors(app,
                                                                    session):
    page = page_module.LiquidHandlingPage(session)
    page.plate.set_selection({"A1", "A2"})
    page._new_group()
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
    page.plate.set_selection({"A1"})
    page._new_group()
    page._add_step("aspirate")
    assert "each well of slot 5" in page.steps.item(0).text()
    assert page.wells_heading.text() == "Wells: slot 5"
    assert page.deck.parent() is not None
    assert "slot 5" in page._group_route(page.program.groups[0])
    page.deleteLater()
