"""A plate and one of its wells, by the mouse: In a well on Manual pick's
Aspirate and Dispense (MANUAL.md §4.2, §4.3, §10.10)."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtGui import QColor                                 # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.gui.theme import COLORS                           # noqa: E402
from micropick.gui.widgets.plate_grid import PLATE_MM            # noqa: E402
from micropick.gui.widgets.well_picker import WellPicker         # noqa: E402

P96 = "corning_96_wellplate_360ul_flat"
P24 = "corning_24_wellplate_3.4ml_flat"


class _Entry:
    def __init__(self, labware_id):
        self.labware_id = labware_id


def _plates(*slots_and_names):
    return [(slot, _Entry(f"id{slot}"), resolve_definition(name))
            for slot, name in slots_and_names]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def picker(app):
    widget = WellPicker("tips", 320)
    widget.set_plates(_plates(("2", P96), ("5", P24)))
    widget.show()
    yield widget
    widget.deleteLater()


def _click(picker, well):
    at = picker.map.well_rect(well).center().toPoint()
    QTest.mouseClick(picker.map, Qt.MouseButton.LeftButton, pos=at)


def test_the_plate_is_drawn_in_real_proportions_at_its_width(picker):
    assert picker.map.width() == 320
    assert picker.map.height() == pytest.approx(
        320 * PLATE_MM[1] / PLATE_MM[0], abs=1)


def test_a_click_on_a_well_chooses_it_and_says_so(picker):
    said = []
    picker.changed.connect(lambda: said.append(picker.chosen()[3]))
    _click(picker, "C4")
    slot, labware_id, definition, well = picker.chosen()
    assert (slot, labware_id, well) == ("2", "id2", "C4")
    assert definition.load_name == P96
    assert picker.well_label.text() == "C4"
    assert said == ["C4"]


def test_the_chosen_well_is_in_its_color_and_ringed(picker):
    _click(picker, "C4")
    assert picker.map._fills["C4"] == QColor(COLORS["tips"])
    assert picker.map._current == "C4"


def test_another_plate_keeps_a_well_it_has_else_its_first(picker):
    _click(picker, "B3")
    picker.plate._chosen(5)
    assert picker.chosen()[0] == "5" and picker.chosen()[3] == "B3"
    picker.plate._chosen(2)
    _click(picker, "H12")
    picker.plate._chosen(5)
    assert picker.chosen()[3] == "A1"


def test_only_the_plates_can_be_chosen_on_the_mini_deck(picker):
    assert sorted(picker.plate.tiles) == [2, 5]
    assert picker.plate.tiles[2].kind == "plate"


def test_a_plate_gone_gives_way_to_the_first_left(picker):
    picker.choose("5", "A2")
    picker.set_plates(_plates(("2", P96)))
    assert picker.chosen()[0] == "2"
    picker.set_plates([])
    assert picker.chosen() is None and picker.map.wells == []


def test_a_wished_for_plate_is_chosen_until_one_is_clicked(picker):
    plates = _plates(("2", P96), ("5", P24))
    picker.set_default("5")
    picker.set_plates(plates)
    assert picker.chosen()[0] == "5"
    picker.plate._chosen(2)
    picker.set_plates(plates)
    assert picker.chosen()[0] == "2"


def test_choose_takes_a_plate_and_a_well(picker):
    picker.choose("5", "B2")
    assert picker.chosen()[0] == "5" and picker.chosen()[3] == "B2"
    assert picker.plate.slot == 5
    picker.choose("9", "A1")                          # not on the deck
    assert picker.chosen()[0] == "5"


def test_marked_wells_show_on_their_plate_only(picker):
    color = QColor(COLORS["planned"])
    picker.set_marked("2", ["B1", "B2"], color)
    assert picker.map._fills["B1"] == color
    picker.plate._chosen(5)
    assert "B1" not in picker.map._fills
    picker.plate._chosen(2)
    assert picker.map._fills["B2"] == color


def test_any_well_can_be_chosen_with_the_plan_on_the_plate(picker):
    picker.set_marked("2", ["B1"], QColor(COLORS["planned"]))
    _click(picker, "D9")
    assert picker.chosen()[3] == "D9"
    assert picker.map._fills["B1"] == QColor(COLORS["planned"])


def test_a_long_name_does_not_widen_it(app):
    widget = WellPicker("tips", 320)
    widget.set_plates(_plates(("2", P96)))
    assert widget.minimumSizeHint().width() <= 320


def test_the_name_has_the_room_of_the_facts(picker):
    # In a card's width the name is what tells the plates apart.
    assert picker.plate.tiles[2].facts
    assert picker.plate._facts.isHidden()
