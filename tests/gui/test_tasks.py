"""Tasks as steps: each step's done mark and first unmet need, from the
session's state (REDESIGN §4)."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                     # noqa: E402

from micropick.gui import tasks                                 # noqa: E402
from micropick.gui.app import Options                          # noqa: E402
from micropick.config.labware import LabwareDefinition         # noqa: E402
from micropick.core.routine import Destination, Routine          # noqa: E402
from micropick.gui.session import Session                      # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    yield s
    s.shutdown()


def test_each_task_is_its_steps_in_order():
    assert tasks.steps("auto") == ["deck", "tip", "dish_bottom", "plan",
                                   "picking"]
    assert tasks.steps("manual") == ["deck", "tip", "dish_bottom", "work"]
    assert tasks.steps("liquid") == ["deck", "tip", "well_center",
                                     "liquid_plan", "run"]
    assert [tasks.title(s) for s in tasks.steps("auto")] == [
        "Deck", "Tip", "Dish bottom", "Plan", "Picking"]
    assert tasks.title("liquid_plan") == "Plan"


def _connect(session):
    session.probe_robot()
    session.new_run()


RACK = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                         namespace="opentrons", version=1,
                         display_name="tips", ordering=[["A1"]], data={},
                         source="shared")


def _fill_deck(session):
    """Something on the deck, and no plate: a tip box."""
    session.load_labware(RACK, 10)


def test_deck_needs_the_robot_first(session):
    state = tasks.step_state(session, "auto", "deck")
    assert not state.done
    assert state.need.kind == "robot"


def test_deck_is_done_while_something_is_on_it_without_a_confirmation(
        session):
    _connect(session)
    assert not tasks.step_state(session, "auto", "deck").done
    assert tasks.step_state(session, "auto", "deck").need is None
    _fill_deck(session)
    assert tasks.step_state(session, "auto", "deck").done
    # The same step in another task: the same state.
    assert tasks.step_state(session, "liquid", "deck").done
    session.unload_labware(10)
    assert not tasks.step_state(session, "manual", "deck").done


def test_deck_is_not_done_while_it_has_a_problem(session):
    from micropick.config.schema import DeckModule
    _connect(session)
    session.load_profile("mock")
    _fill_deck(session)
    # A platform placed under the rack after it was loaded: no height.
    session.set_deck_modules([DeckModule(slots=[10], offset=[0, 0, 64.2],
                                         name="Picking platform")])
    assert session.deck_problems()
    assert not tasks.step_state(session, "auto", "deck").done


def test_plan_needs_a_plate_on_the_deck_not_the_deck_step(session):
    _connect(session)
    session.load_profile("mock")
    _fill_deck(session)                         # a tip box is not a plate
    need = tasks.step_state(session, "auto", "plan").need
    assert (need.kind, need.key, need.text) == (
        "plate", "deck", "Put a plate on the deck first.")
    session.unload_labware(10)
    session.load_labware(LabwareDefinition(
        load_name="corning_96_wellplate_360ul_flat", namespace="opentrons",
        version=1, display_name="plate", ordering=[["A1"]], data={},
        source="shared"), 5)
    assert tasks.step_state(session, "auto", "plan").need is None


def _tip_on(session, slot=10):
    session.load_labware(RACK, slot)
    session.pick_up_tip(slot, "A1")


def test_tip_needs_the_deck_then_the_camera_calibration(session):
    _connect(session)
    session.load_profile("mock")
    need = tasks.step_state(session, "auto", "tip").need
    assert (need.kind, need.key) == ("step", "deck")
    _fill_deck(session)
    need = tasks.step_state(session, "manual", "tip").need
    assert (need.kind, need.key) == ("setup", "camera_calibration")


def test_liquid_tip_is_done_with_any_tip_on(session):
    _connect(session)
    session.load_profile("mock")
    _fill_deck(session)
    assert not tasks.step_state(session, "liquid", "tip").done
    _tip_on(session)
    liquid = tasks.step_state(session, "liquid", "tip")
    assert liquid.done and liquid.need is None
    # Picking needs the tip calibrated, not only a tip on.
    assert not tasks.step_state(session, "auto", "tip").done


def _plan(session, confirmed=True):
    ordering = [["A1", "B1"], ["A2", "B2"]]
    plate = LabwareDefinition(load_name="tiny", namespace="custom_beta",
                              version=1, display_name="tiny",
                              ordering=ordering, data={}, source="local")
    routine = Routine(Destination.from_definition(plate, slot=5),
                      {"A1": 1, "B2": 1}, name="plan")
    routine.needs_confirmation = not confirmed
    session.set_routine(routine)
    return routine


def test_plan_is_done_once_a_plan_is_confirmed(session):
    _connect(session)
    _fill_deck(session)
    assert not tasks.step_state(session, "auto", "plan").done
    _plan(session, confirmed=False)            # restored, not confirmed
    assert not tasks.step_state(session, "auto", "plan").done
    _plan(session)
    assert tasks.step_state(session, "auto", "plan").done


def test_dish_bottom_is_done_once_confirmed_in_this_robot_session(session):
    _connect(session)
    assert not tasks.step_state(session, "manual", "dish_bottom").done
    session.confirm("dish_bottom")
    assert tasks.step_state(session, "auto", "dish_bottom").done


def test_picking_needs_the_dish_bottom_and_plan_does_not():
    picking = [n.key for n in tasks._needs("auto", "picking")
               if n.kind == "step"]
    assert picking == ["deck", "tip", "dish_bottom", "plan"]
    assert "dish_bottom" not in [n.key for n in tasks._needs("auto", "plan")]


def test_picking_names_the_first_thing_missing(session):
    assert tasks.step_state(session, "auto", "picking").need.kind == "robot"
    _connect(session)
    session.load_profile("mock")
    need = tasks.step_state(session, "auto", "picking").need
    assert (need.kind, need.key, need.text) == ("step", "deck",
                                                "Check the deck first.")
    _fill_deck(session)
    assert tasks.step_state(session, "auto", "picking").need.key == "tip"


def test_picking_is_done_when_the_plan_is_delivered(session):
    _connect(session)
    routine = _plan(session)
    assert not tasks.step_state(session, "auto", "picking").done
    for _ in range(2):
        routine.next()
        routine.record(delivered=1)
    assert tasks.step_state(session, "auto", "picking").done


def test_work_is_an_optional_step_never_done(session):
    _connect(session)
    _fill_deck(session)
    work = tasks.step_state(session, "manual", "work")
    assert not work.done and work.optional
    assert work.need.key == "tip"


def test_well_center_is_done_when_every_plate_is_measured_or_skipped(session):
    from micropick.config.labware import resolve_definition
    from micropick.config.schema import WellCenter
    _connect(session)
    session.load_profile("mock")
    assert not tasks.step_state(session, "liquid", "well_center").done
    session.load_labware(resolve_definition("corning_96_wellplate_360ul_flat"),
                         5)
    session.load_labware(resolve_definition("corning_96_wellplate_360ul_flat"),
                         6)
    assert not tasks.step_state(session, "liquid", "well_center").done
    session.profile.deck.well_centers.append(WellCenter(
        slot=5, load_name="corning_96_wellplate_360ul_flat",
        offset=(0.1, 0.2, 0.0), well="A1"))
    assert not tasks.step_state(session, "liquid", "well_center").done
    session.skip_plate(6, "corning_96_wellplate_360ul_flat")
    assert tasks.step_state(session, "liquid", "well_center").done
    session.new_run()                     # skipping holds for one session
    assert not tasks.step_state(session, "liquid", "well_center").done


def test_liquid_plan_and_run_are_confirmed_by_their_screens(session):
    _connect(session)
    _fill_deck(session)
    assert not tasks.step_state(session, "liquid", "liquid_plan").done
    session.confirm("liquid_plan")
    assert tasks.step_state(session, "liquid", "liquid_plan").done
    assert tasks.step_state(session, "liquid", "run").need.key == "tip"
    assert not tasks.step_state(session, "liquid", "run").done
    session.confirm("liquid_run")
    assert tasks.step_state(session, "liquid", "run").done


@pytest.mark.parametrize("task, step, kind, key", [
    ("auto", "deck", None, None),
    ("auto", "tip", "setup", "camera_calibration"),
    ("liquid", "tip", None, None),
    ("auto", "dish_bottom", "step", "tip"),
    ("auto", "plan", "plate", "deck"),
    ("auto", "picking", "step", "tip"),
    ("manual", "work", "step", "tip"),
    ("liquid", "well_center", "step", "tip"),
    ("liquid", "liquid_plan", None, None),
    ("liquid", "run", "step", "tip"),
])
def test_first_need_of_each_step_with_only_the_deck_checked(
        session, task, step, kind, key):
    _connect(session)
    session.load_profile("mock")
    _fill_deck(session)
    need = tasks.step_state(session, task, step).need
    assert (need.kind if need else None, need.key if need else None) == (
        kind, key)


def test_nothing_is_confirmed_without_a_robot_session(session):
    session.confirm("dish_bottom")
    _connect(session)
    assert not tasks.step_state(session, "auto", "dish_bottom").done


def test_positions_come_in_the_order_they_can_be_set():
    """The calibration position before the shake position (which needs a
    calibrated tip), the position over the dish last (#33)."""
    assert list(tasks.SETUP) == [
        "upper_camera", "camera_calibration", "lower_camera", "platforms",
        "calibration_position", "shake_position", "dish_position"]


def test_picking_names_the_shake_position_before_the_dish(session):
    _connect(session)
    session.load_profile("mock")
    _fill_deck(session)
    need = tasks.step_state(session, "auto", "picking").need
    assert need.key == "tip"
    picking = tasks._needs("auto", "picking")
    assert [n.key for n in picking if n.kind == "setup"] == [
        "shake_position", "dish_position"]
