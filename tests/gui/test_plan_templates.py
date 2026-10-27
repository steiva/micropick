"""Plate plan templates: named files under Outputs/templates/plate-plans,
Save as makes a new one, Save changes overwrites the open one, and the
presets saved before templates move there."""

import json
import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick import paths                                      # noqa: E402
from micropick.core.routine import Destination, PlanPreset       # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session, SessionError          # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    yield s
    s.shutdown()


def _preset(plan, name="", strategy="by_column"):
    return PlanPreset.from_plan(Destination.from_labware(PLATE, 5), plan,
                                strategy=strategy, name=name)


def test_save_as_makes_a_named_file_in_the_templates_folder(session):
    template = session.save_plate_template(_preset({"A1": 1, "A2": 1}),
                                           "Row A")
    assert template.path.parent == (paths.outputs_dir() / "templates"
                                    / "plate-plans")
    assert template.path.exists()
    listed = session.plate_templates()
    assert [(t.name, t.wells) for t in listed] == [("Row A", 2)]
    assert PlanPreset.load(listed[0].path).plan == {"A1": 1, "A2": 1}


def test_save_as_never_overwrites_and_refuses_a_name_in_use(session):
    first = session.save_plate_template(_preset({"A1": 1}), "Row A")
    with pytest.raises(SessionError):
        session.save_plate_template(_preset({"B1": 1}), "row a ")
    with pytest.raises(SessionError):
        session.save_plate_template(_preset({"B1": 1}), "  ")
    other = session.save_plate_template(_preset({"B1": 1}), "Row A/B")
    assert other.path != first.path
    assert PlanPreset.load(first.path).plan == {"A1": 1}
    assert sorted(t.name for t in session.plate_templates()) == [
        "Row A", "Row A/B"]


def test_save_changes_overwrites_the_open_template(session):
    template = session.save_plate_template(_preset({"A1": 1}), "Row A")
    changed = session.save_plate_template_changes(
        template, _preset({"A1": 2, "A2": 2}, strategy="by_row"))
    assert changed.path == template.path
    assert changed.name == "Row A"
    assert len(session.plate_templates()) == 1
    back = PlanPreset.load(template.path)
    assert back.plan == {"A1": 2, "A2": 2}
    assert back.strategy == "by_row"
    assert back.name == "Row A"


def test_the_presets_saved_before_templates_move_into_the_folder(session):
    old = paths.outputs_dir() / "plate_plan_presets"
    old.mkdir(parents=True)
    _preset({"C3": 1}).save(old / "my plate.json")
    (old / "broken.json").write_text("{", encoding="utf-8")

    listed = session.plate_templates()
    assert [(t.name, t.wells) for t in listed] == [("my plate", 1)]
    assert listed[0].path.parent.name == "plate-plans"
    assert not (old / "my plate.json").exists()
    # What is not a preset is left where it was, and never listed.
    assert (old / "broken.json").exists()


def test_a_template_lists_with_its_wells_and_when_it_was_saved(session):
    template = session.save_plate_template(_preset({"A1": 1, "B1": 3}),
                                           "Mixed")
    assert template.wells == 2
    assert template.facts().startswith("2 wells · saved ")
    data = json.loads(template.path.read_text(encoding="utf-8"))
    assert data["name"] == "Mixed"
