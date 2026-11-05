"""Manual pick's Source step (#88): the tiles, and the steps that follow
the choice."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel             # noqa: E402

from micropick.config.labware import resolve_definition        # noqa: E402
from micropick.config.schema import WellCenter                 # noqa: E402
from micropick.gui import tasks                                # noqa: E402
from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.session import SOURCE_DISH                  # noqa: E402
from micropick.gui.shell import MainWindow                     # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
NAME = "Corning 96 Well Plate 360 µL Flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    # The Tip step done: what follows it is what is tested.
    monkeypatch.setattr(tasks, "tip_calibration_status",
                        lambda _session: (True, ""))
    w = MainWindow(Options(mock=True))
    w.session.probe_robot()
    w.session.new_run()
    w.session.load_profile("mock")
    w.session.load_labware(resolve_definition(PLATE), 5)
    w.session.load_labware(resolve_definition(PLATE), 6)
    w.show()
    app.processEvents()
    yield w
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def test_the_heading_asks_where_from(app, window):
    window.open_task("manual")
    window.show_step("source")
    app.processEvents()
    page = window.current_page()
    assert "Where do you pick from?" in [
        label.text() for label in page.findChildren(QLabel)]


def _titles(window):
    return [chip.text() for chip in window.step_bar.chips]


def _measure(session, slot):
    session.profile.deck.well_centers.append(WellCenter(
        slot=slot, load_name=PLATE, offset=(0.1, 0.2, 0.0), well="A1"))
    session.profile_changed.emit(session.profile)


def test_the_task_opens_on_source_with_a_tile_for_the_dish_and_each_plate(
        window):
    window.home.tiles["manual"].clicked.emit()
    assert window.step == "source"
    page = window.source_page
    assert window.current_page() is page
    assert [t.text() for t in page.tiles.values()] == [
        "Source dish", f"{NAME}\nSlot 5", f"{NAME}\nSlot 6"]
    assert not any(t.isChecked() for t in page.tiles.values())
    assert not window.footer.next.isEnabled()


def test_the_source_dish_tile_says_its_slot_once_the_position_is_set(window):
    window.session.profile.positions["observe"] = (200.0, 150.0, 120.0)
    window.open_task("manual")
    assert window.source_page.tiles[SOURCE_DISH].text().startswith(
        "Source dish\nSlot ")


def test_a_click_on_a_tile_chooses_it_and_marks_the_step(window):
    window.open_task("manual")
    page = window.source_page
    page.tiles["5"].click()
    assert window.session.source_plate.slot == "5"
    assert page.tiles["5"].isChecked()
    assert window.footer.next.isEnabled()
    page.tiles[SOURCE_DISH].click()
    assert window.session.source == SOURCE_DISH
    assert page.tiles[SOURCE_DISH].isChecked()
    assert not page.tiles["5"].isChecked()


def test_the_step_bar_follows_the_choice(window):
    window.open_task("manual")
    with_dish = ["Deck", "Tip", "Source", "Well center", "Dish bottom", "Run"]
    assert _titles(window) == with_dish
    window.source_page.tiles["5"].click()
    assert _titles(window) == ["Deck", "Tip", "Source", "Well center", "Run"]
    window.source_page.tiles[SOURCE_DISH].click()
    assert _titles(window) == with_dish


def test_a_chip_goes_to_its_step_after_the_bar_changed(window):
    window.open_task("manual")
    window.source_page.tiles["5"].click()
    window.step_bar.clicked.emit(4)
    assert window.step == "work"


def test_the_plate_gone_brings_dish_bottom_back_and_unchecks_the_tile(window):
    window.open_task("manual")
    page = window.source_page
    page.tiles["5"].click()
    window.session.unload_labware(5)
    assert list(page.tiles) == [SOURCE_DISH, "6"]
    assert not any(t.isChecked() for t in page.tiles.values())
    assert "Dish bottom" in _titles(window)
    assert not window.footer.next.isEnabled()


def test_a_step_that_left_the_task_gives_way_to_the_next_to_do(window):
    window.open_task_step("manual", "dish_bottom")
    window.session.choose_source("5")
    assert window.step == "well_center"
    assert "Dish bottom" not in _titles(window)


def test_with_a_plate_the_task_opens_on_well_center_then_on_run(window):
    session = window.session
    session.choose_source("5")
    window.open_task("manual")
    assert window.step == "well_center"
    assert not window.footer.next.isEnabled()
    _measure(session, 5)
    session.use_plate_bottom(5, PLATE)
    assert window.footer.next.isEnabled()
    window.open_task("manual")
    assert window.step == "work"


def test_run_waits_under_a_banner_naming_the_plate_and_goes_there(window):
    window.session.choose_source("6")
    window.open_task_step("manual", "work")
    assert window.banner.label.text() == f"Measure {NAME} in slot 6 first."
    window.go_to(tasks.step_state(window.session, "manual", "work").need)
    assert window.step == "well_center"


def test_well_center_selects_the_source_plate_and_says_so(window):
    session = window.session
    session.choose_source("6")
    window.open_task("manual")
    page = window.well_center_page
    assert page._selected == ("6", PLATE)
    assert page.intro.text() == "You pick from slot 6: measure it, or Use it."
    assert window.footer.sentence.text() == "Measure the plate in slot 6."


def test_a_measured_source_not_confirmed_stays_the_plate_to_do(window):
    """A measured plate not confirmed is still the one to do."""
    session = window.session
    _measure(session, 6)
    session.choose_source("6")
    window.open_task("manual")
    page = window.well_center_page
    assert page._selected == ("6", PLATE)
    page.select_plate("5", PLATE)
    assert page._first_to_do() == ("6", PLATE)


def test_with_the_source_dish_well_center_says_what_it_said(window):
    window.session.choose_source(SOURCE_DISH)
    window.open_task_step("manual", "well_center")
    assert window.well_center_page.intro.text() == (
        "A plate not measured uses its drawing.")
    assert window.footer.next.isEnabled()


def test_finish_by_hand_still_opens_work_with_the_robot_lost(window):
    from micropick.gui.session import RunState
    window.session._lose_run(RunState(), restarted=True)
    window.picking_page._finish_by_hand()
    assert (window.task, window.step) == ("manual", "work")
