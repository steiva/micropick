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
    assert tasks.steps("auto") == ["deck", "tip", "plan", "dish_bottom",
                                   "picking"]
    assert tasks.steps("manual") == ["deck", "tip", "dish_bottom", "work"]
    assert tasks.steps("liquid") == ["deck", "tip", "well_center",
                                     "liquid_plan", "run"]
    assert [tasks.title(s) for s in tasks.steps("auto")] == [
        "Deck", "Tip", "Plan", "Dish bottom", "Picking"]
    assert tasks.title("liquid_plan") == "Plan"


def _connect(session):
    session.probe_robot()
    session.new_run()


def test_deck_needs_the_robot_first(session):
    state = tasks.step_state(session, "auto", "deck")
    assert not state.done
    assert state.need.kind == "robot"


def test_deck_is_done_once_confirmed_in_this_robot_session(session):
    _connect(session)
    assert not tasks.step_state(session, "auto", "deck").done
    assert tasks.step_state(session, "auto", "deck").need is None
    session.confirm("deck")
    assert tasks.step_state(session, "auto", "deck").done
    # The same step in another task: the same state.
    assert tasks.step_state(session, "liquid", "deck").done
    session.new_run()
    assert not tasks.step_state(session, "manual", "deck").done


def _tip_on(session, slot=10):
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, slot)
    session.pick_up_tip(slot, "A1")


def test_tip_needs_the_deck_then_the_camera_calibration(session):
    _connect(session)
    session.load_profile("mock")
    need = tasks.step_state(session, "auto", "tip").need
    assert (need.kind, need.key) == ("step", "deck")
    session.confirm("deck")
    need = tasks.step_state(session, "manual", "tip").need
    assert (need.kind, need.key) == ("setup", "camera_calibration")


def test_liquid_tip_is_done_with_any_tip_on(session):
    _connect(session)
    session.load_profile("mock")
    session.confirm("deck")
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
    session.confirm("deck")
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


def test_picking_names_the_first_thing_missing(session):
    assert tasks.step_state(session, "auto", "picking").need.kind == "robot"
    _connect(session)
    session.load_profile("mock")
    need = tasks.step_state(session, "auto", "picking").need
    assert (need.kind, need.key, need.text) == ("step", "deck",
                                                "Check the deck first.")
    session.confirm("deck")
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
    session.confirm("deck")
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
    session.confirm(tasks.skipped_plate(6, "corning_96_wellplate_360ul_flat"))
    assert tasks.step_state(session, "liquid", "well_center").done
    session.new_run()                     # skipping holds for one session
    assert not tasks.step_state(session, "liquid", "well_center").done


def test_liquid_plan_and_run_are_confirmed_by_their_screens(session):
    _connect(session)
    session.confirm("deck")
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
    ("auto", "plan", None, None),
    ("auto", "dish_bottom", "step", "tip"),
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
    session.confirm("deck")
    need = tasks.step_state(session, task, step).need
    assert (need.kind if need else None, need.key if need else None) == (
        kind, key)


def test_nothing_is_confirmed_without_a_robot_session(session):
    session.confirm("dish_bottom")
    _connect(session)
    assert not tasks.step_state(session, "auto", "dish_bottom").done
