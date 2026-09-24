"""What stops a picking run from starting, and what the model choice is.

Offscreen, with a mock session. Every item in `_run_problems` is a way for
a run to fail minutes after it starts, on a bench, with a tip in the air;
each one is checked here because each one was found the expensive way.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import LabwareDefinition           # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.detector import STANDIN                       # noqa: E402
from micropick.gui.pages.picking import (DISH_POSITION,          # noqa: E402
                                         SHAKE_POSITION,
                                         PickingPage)
from micropick.gui.session import Session                        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    session = Session(Options(mock=True))
    session.load_profile("mock")
    p = PickingPage(session)
    yield p
    p.deleteLater()


def problems(page):
    return " | ".join(page._run_problems())


def test_it_names_what_is_missing_rather_than_refusing_quietly(page):
    text = problems(page)
    for wanted in ("no robot", "no routine", DISH_POSITION, SHAKE_POSITION,
                   "no detector"):
        assert wanted in text, f"{wanted!r} not named in: {text}"
    assert not page.start_button.isEnabled()


def test_a_routine_is_required(page):
    class _Routine:
        name = "stub"
        needs_confirmation = False

        def summary(self):
            return "routine 'stub'"

        class destination:
            slot = 5
    page.session.set_routine(_Routine())
    assert "no routine" not in problems(page)
    page.session.set_routine(None)
    assert "no routine" in problems(page)


def test_the_plate_the_routine_delivers_into_has_to_be_on_the_deck(page):
    """Checked against the run, not against the plan: a routine names a
    slot and the robot is what knows whether anything is in it."""
    class _Routine:
        name = "stub"
        needs_confirmation = False

        def summary(self):
            return "routine 'stub'"

        class destination:
            slot = 5
    session = page.session
    session.set_routine(_Routine())
    session.probe_robot()
    session.new_run()
    assert "slot 5" in problems(page)
    session.load_labware(
        LabwareDefinition(load_name="corning_96_wellplate_360ul_flat",
                          namespace="opentrons", version=1,
                          display_name="plate", ordering=[["A1"]], data={},
                          source="shared"), 5)
    assert "slot 5" not in problems(page)
    session.shutdown()


def test_an_unconfirmed_routine_is_refused(page):
    class _Routine:
        name = "stub"
        needs_confirmation = True

        class destination:
            slot = 5
    page.session.set_routine(_Routine())
    assert "not been confirmed" in problems(page)


def test_the_model_comes_from_the_profile(page):
    # --mock has no weights at all, so the stand-in is what an unnamed
    # model means there; on the bench it is a thing to be chosen.
    assert page._wanted_model() == STANDIN
    page.session.profile.picking.model_file = "cuboid_v9.pt"
    assert page._wanted_model() == "cuboid_v9.pt"
    page.session.profile.picking.model_file = ""
    page.session.options = Options(mock=False)
    assert page._wanted_model() == ""


def test_the_page_has_no_weights_chooser_any_more(page):
    """It moved to the Profile page: an installation has one detector and
    choosing it is not a question to ask on every visit."""
    assert not hasattr(page, "weights")
    assert not hasattr(page, "load_button")


def test_the_histogram_cannot_be_moved_by_the_mouse(page):
    """It is a readout. Left to itself pyqtgraph keeps whatever range the
    last wheel turn left behind - including wheel turns meant for the
    scrolling column it sits in."""
    view = page.hist.getPlotItem().getViewBox()
    assert view.state["mouseEnabled"] == [False, False]
    assert not view.menuEnabled()
