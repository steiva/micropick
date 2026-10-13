"""A packaged build's labware and weights reach the root file by file."""
from micropick import paths


def test_files_the_root_lacks_are_copied_and_nothing_is_replaced(
        tmp_path, monkeypatch):
    bundle, root = tmp_path / "bundle", tmp_path / "root"
    (bundle / "ml_models").mkdir(parents=True)
    (bundle / "ml_models" / "m.pt").write_text("bundled")
    (bundle / "ml_models" / "m.onnx").write_text("new in this version")
    (root / "ml_models").mkdir(parents=True)
    (root / "ml_models" / "m.pt").write_text("the operator's own")
    monkeypatch.setattr(paths, "bundle_dir", lambda: bundle)
    monkeypatch.setenv(paths.ENV_VAR, str(root))

    copied = paths.seed_from_bundle()

    assert copied == [root / "ml_models" / "m.onnx"]
    assert (root / "ml_models" / "m.onnx").read_text() == "new in this version"
    assert (root / "ml_models" / "m.pt").read_text() == "the operator's own"
