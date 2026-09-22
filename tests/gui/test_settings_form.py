"""The picking settings form: built from the model, validated by the model.

Offscreen. What is checked is that no field goes missing and that the values
survive a round trip, because the form's whole claim is that it is the
schema rather than a copy of it.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.schema import PickingConfig                # noqa: E402
from micropick.gui.widgets.settings_form import (                # noqa: E402
    PickingSettingsDialog)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_every_field_has_a_row_and_all_of_them_edit(app):
    dialog = PickingSettingsDialog(PickingConfig())
    assert set(dialog._rows) == set(PickingConfig.model_fields)
    # A field the form cannot express is shown read-only rather than
    # dropped; today every one of them is editable, and this says so.
    assert all(row.editable for row in dialog._rows.values())
    dialog.deleteLater()


def test_values_survive_the_round_trip(app):
    config = PickingConfig(vol=7.5, one_by_one=True, max_batch=4,
                           cuboid_size_threshold=(300, 600),
                           miss_policy="return_all")
    dialog = PickingSettingsDialog(config)
    back = dialog.config()
    assert back.vol == 7.5
    assert back.one_by_one is True
    assert back.max_batch == 4
    assert tuple(back.cuboid_size_threshold) == (300, 600)
    assert back.miss_policy == "return_all"
    dialog.deleteLater()


def test_an_edit_reaches_the_config(app):
    dialog = PickingSettingsDialog(PickingConfig())
    dialog._rows["vol"].set_value(12.5)
    dialog._rows["one_by_one"].set_value(True)
    dialog._rows["miss_policy"].set_value("return_all")
    dialog._rows["cuboid_size_threshold"].set_value((100, 200))
    edited = dialog.config()
    assert (edited.vol, edited.one_by_one, edited.miss_policy) == (
        12.5, True, "return_all")
    assert tuple(edited.cuboid_size_threshold) == (100, 200)
    dialog.deleteLater()


def test_restore_defaults_keeps_the_same_widgets(app):
    """Replacing them left the old ones parented and painted: half the form
    drawn twice, over itself."""
    dialog = PickingSettingsDialog(PickingConfig(vol=99.0))
    widgets = {name: row.widget for name, row in dialog._rows.items()}
    dialog._restore_defaults()
    assert dialog._rows["vol"].value() == PickingConfig().vol
    assert all(dialog._rows[name].widget is widget
               for name, widget in widgets.items())
    dialog.deleteLater()


def test_the_model_is_what_refuses_a_bad_value(app):
    dialog = PickingSettingsDialog(PickingConfig())
    # aspect_ratio_window is validated as an ordered pair by the schema; the
    # widgets happily express the wrong order, and that is the point.
    dialog._rows["aspect_ratio_window"].set_value((2.0, 1.0))
    with pytest.raises(Exception):
        dialog.config()
    dialog._accept()                      # shows the model's words, no crash
    assert dialog.message.isVisibleTo(dialog)
    assert "aspect_ratio_window" in dialog.message.text()
    dialog.deleteLater()


def test_the_filter_hides_by_name(app):
    dialog = PickingSettingsDialog(PickingConfig())
    dialog.show()
    app.processEvents()
    dialog._apply_filter("size")
    shown = [name for name, row in dialog._rows.items()
             if not row.widget.isHidden()]
    assert "cuboid_size_threshold" in shown
    assert "vol" not in shown
    dialog._apply_filter("")
    assert not dialog._rows["vol"].widget.isHidden()
    dialog.close()
    dialog.deleteLater()
