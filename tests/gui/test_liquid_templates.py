"""Liquid plan templates: named files under Outputs/templates/liquid-plans,
Save as makes a new one, Save changes overwrites the open one, and the
programs saved before plans are listed as old, and stay programs."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick import paths                                      # noqa: E402
from micropick.core.liquid import (Aspirate, Group, Location,    # noqa: E402
                                   Program)
from micropick.core.liquid_plan import (LiquidPlan, MoveLiquid,  # noqa: E402
                                        Place, WaitStep)
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session, SessionError          # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    yield s
    s.shutdown()


def _program(*wells_per_group) -> Program:
    return Program(groups=[
        Group(name=f"g{n}", slot="5", load_name="plate", wells=list(wells),
              steps=[Aspirate(volume_ul=10)])
        for n, wells in enumerate(wells_per_group)])


def _plan(*wells) -> LiquidPlan:
    return LiquidPlan(
        trash=Location(kind="point", point="Waste"), tip_ul=300,
        steps=[MoveLiquid(color="#123456",
                          source=Place(slot="5", load_name="plate",
                                       wells=["A1"]),
                          dest=Place(slot="2", load_name="plate",
                                     wells=list(wells))),
               WaitStep(color="#654321", seconds=30)])


def test_save_as_makes_a_named_file_in_the_liquid_templates_folder(session):
    template = session.save_liquid_template(_plan("A1", "A2"), "Wash")
    assert template.path.parent == (paths.outputs_dir() / "templates"
                                    / "liquid-plans")
    listed = session.liquid_templates()
    assert [(t.name, t.wells, t.old) for t in listed] == [("Wash", 3, False)]
    assert listed[0].facts().startswith("2 steps · 3 wells · saved ")
    # Its own folder: not a plate plan.
    assert session.plate_templates() == []


def test_a_plan_comes_back_from_its_file_as_it_was(session):
    plan = _plan("A1", "A2")
    template = session.save_liquid_template(plan, "Wash")
    back = LiquidPlan.load(template.path)
    assert back == plan.model_copy(update={"name": "Wash"})
    assert [s.id for s in back.steps] == [s.id for s in plan.steps]
    assert [s.color for s in back.steps] == ["#123456", "#654321"]


def test_save_as_refuses_a_name_in_use_and_save_changes_overwrites(session):
    first = session.save_liquid_template(_plan("A1"), "Wash")
    with pytest.raises(SessionError):
        session.save_liquid_template(_plan("B1"), " wash")
    with pytest.raises(SessionError):
        session.save_liquid_template(_plan("B1"), "")
    changed = session.save_liquid_template_changes(first, _plan("C1", "C2"))
    assert changed.path == first.path and changed.name == "Wash"
    assert LiquidPlan.load(first.path).steps[0].dest.wells == ["C1", "C2"]
    assert len(session.liquid_templates()) == 1


def test_an_old_program_is_listed_as_old_and_stays_a_program(session):
    folder = paths.outputs_dir() / "templates" / "liquid-plans"
    folder.mkdir(parents=True)
    _program(["A1"]).model_copy(update={"name": "Daily"}).save(
        folder / "daily.json")
    before = (folder / "daily.json").read_bytes()

    listed = session.liquid_templates()
    assert [(t.name, t.old) for t in listed] == [("Daily", True)]
    assert listed[0].facts() == "Made with an older version"
    assert (folder / "daily.json").read_bytes() == before
    assert Program.load(folder / "daily.json").groups[0].wells == ["A1"]


def test_the_programs_saved_before_templates_move_into_the_folder(session):
    old = paths.outputs_dir() / "liquid"
    old.mkdir(parents=True)
    _program(["A1"]).save(old / "daily wash.json")
    _program(["B1"]).save(old / "current.json")      # the page's own
    (old / "broken.json").write_text("{", encoding="utf-8")

    listed = session.liquid_templates()
    assert [(t.name, t.old) for t in listed] == [("daily wash", True)]
    assert listed[0].path.parent.name == "liquid-plans"
    assert not (old / "daily wash.json").exists()
    assert (old / "current.json").exists()
    assert (old / "broken.json").exists()


def test_the_default_trash_is_kept_in_the_profile(session):
    trash = Location(kind="well", slot="3", load_name="res", well="A1",
                     level="center", offset=[0, 0, -2])
    session.load_last_profile()
    assert session.default_trash is None
    session.set_default_trash(trash)
    name = session.profile.name
    session.load_profile(name)
    assert session.default_trash == trash
    session.set_default_trash(None)
    session.load_profile(name)
    assert session.default_trash is None
