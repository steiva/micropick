"""This computer's settings: the robot's address and the output folders."""
from __future__ import annotations

import pytest

from micropick import paths
from micropick.config import app_settings
from micropick.config.app_settings import AppSettings


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    yield tmp_path
    paths.set_overrides()                     # leave the default layout


def test_no_file_is_the_defaults(root):
    settings, note = app_settings.load_settings()
    assert settings == AppSettings() and note == ""
    assert settings.robot_host is None and settings.robot_port == 31950


def test_settings_round_trip(root):
    saved = AppSettings(robot_host="10.0.0.5", robot_port=31951,
                        outputs_dir=str(root / "elsewhere"))
    path = app_settings.save_settings(saved)
    assert path == root / "settings.json"
    assert app_settings.load_settings() == (saved, "")


def test_an_unreadable_file_is_the_defaults_with_a_reason(root):
    (root / "settings.json").write_text("{not json")
    settings, note = app_settings.load_settings()
    assert settings == AppSettings() and "could not be read" in note


def test_folders_move_where_the_settings_say(root):
    assert paths.outputs_dir(create=False) == root / "outputs"
    app_settings.apply(AppSettings(outputs_dir=str(root / "out"),
                                   logs_dir=str(root / "lg")))
    assert paths.outputs_dir() == root / "out"
    assert paths.logs_dir() == root / "lg"
    # Images and clips follow outputs unless images is set itself.
    assert paths.images_dir() == root / "out" / "images"
    assert paths.clips_dir() == root / "out" / "clips"
    app_settings.apply(AppSettings(images_dir=str(root / "pics")))
    assert paths.images_dir() == root / "pics"
    assert paths.outputs_dir(create=False) == root / "outputs"


def test_a_newer_versions_file_keeps_what_this_one_knows(root):
    # Written by a later version: a setting unknown here is left out, and
    # everything else is kept rather than the whole file replaced.
    (root / "settings.json").write_text(
        '{"robot_host": "10.0.0.5", "keys": {"jog_left": "J"},'
        ' "from_the_future": 1}')
    settings, note = app_settings.load_settings()
    assert settings.robot_host == "10.0.0.5"
    assert settings.keys == {"jog_left": "J"}
    assert "from_the_future" in note and "left out" in note


def test_a_value_not_usable_is_that_settings_default_alone(root):
    (root / "settings.json").write_text(
        '{"robot_host": "10.0.0.5", "inference_device": "tpu",'
        ' "robot_port": 0}')
    settings, note = app_settings.load_settings()
    assert settings.robot_host == "10.0.0.5"
    assert settings.inference_device == "auto"
    assert settings.robot_port == 31950
    assert "inference_device" in note and "robot_port" in note


def test_a_file_not_an_object_is_the_defaults(root):
    (root / "settings.json").write_text("[1, 2]")
    settings, note = app_settings.load_settings()
    assert settings == AppSettings() and "could not be read" in note
