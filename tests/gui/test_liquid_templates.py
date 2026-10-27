"""Liquid plan templates: named files under Outputs/templates/liquid-plans,
Save as makes a new one, Save changes overwrites the open one, and the
programs saved before templates move there."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick import paths                                      # noqa: E402
from micropick.core.liquid import Aspirate, Group, Program       # noqa: E402
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


def test_save_as_makes_a_named_file_in_the_liquid_templates_folder(session):
    template = session.save_liquid_template(_program(["A1", "A2"], ["B1"]),
                                            "Wash")
    assert template.path.parent == (paths.outputs_dir() / "templates"
                                    / "liquid-plans")
    listed = session.liquid_templates()
    assert [(t.name, t.wells) for t in listed] == [("Wash", 3)]
    assert listed[0].facts().startswith("2 groups · 3 wells · saved ")
    back = Program.load(listed[0].path)
    assert back.name == "Wash"
    assert [g.wells for g in back.groups] == [["A1", "A2"], ["B1"]]
    # Its own folder: not a plate plan.
    assert session.plate_templates() == []


def test_save_as_refuses_a_name_in_use_and_save_changes_overwrites(session):
    first = session.save_liquid_template(_program(["A1"]), "Wash")
    with pytest.raises(SessionError):
        session.save_liquid_template(_program(["B1"]), " wash")
    with pytest.raises(SessionError):
        session.save_liquid_template(_program(["B1"]), "")
    changed = session.save_liquid_template_changes(first,
                                                   _program(["C1", "C2"]))
    assert changed.path == first.path and changed.name == "Wash"
    assert [g.wells for g in Program.load(first.path).groups] == [["C1",
                                                                   "C2"]]
    assert len(session.liquid_templates()) == 1


def test_the_programs_saved_before_templates_move_into_the_folder(session):
    old = paths.outputs_dir() / "liquid"
    old.mkdir(parents=True)
    _program(["A1"]).save(old / "daily wash.json")
    _program(["B1"]).save(old / "current.json")      # the page's own
    (old / "broken.json").write_text("{", encoding="utf-8")

    listed = session.liquid_templates()
    assert [t.name for t in listed] == ["daily wash"]
    assert listed[0].path.parent.name == "liquid-plans"
    assert not (old / "daily wash.json").exists()
    assert (old / "current.json").exists()
    assert (old / "broken.json").exists()
