"""A profile file that cannot be read is a ProfileError naming the file,
whatever is wrong with it (GUI-13)."""

import pytest

from micropick.config import store

FILES = [store.META_FILE, store.CALIBRATION_FILE, store.PICKING_FILE,
         store.CAMERAS_FILE, store.POSITIONS_FILE, store.DECK_FILE]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    made = store.create_profile("bench")
    return made


@pytest.mark.parametrize("damage", ["{not json", "", "[]", "7", "\xff\xfe"])
@pytest.mark.parametrize("name", FILES)
def test_a_damaged_file_is_a_profile_error_naming_it(profile, name, damage):
    path = profile.path / name
    if damage == "\xff\xfe":
        path.write_bytes(b"\xff\xfe\x00")
    else:
        path.write_text(damage, encoding="utf-8")
    with pytest.raises(store.ProfileError) as raised:
        store.load_profile("bench")
    assert raised.value.path == path


def test_a_position_that_is_not_x_y_z_is_named(profile):
    path = profile.path / store.POSITIONS_FILE
    path.write_text('{"observe": 5}', encoding="utf-8")
    with pytest.raises(store.ProfileError) as raised:
        store.load_profile("bench")
    assert raised.value.path == path


def test_a_folder_is_archived_beside_itself(profile):
    archive = store.archive_folder(profile.path)
    assert archive.parent == profile.path.parent
    assert archive.name.startswith("bench-") and archive.suffix == ".zip"
