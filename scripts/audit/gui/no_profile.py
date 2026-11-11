"""Category E/F: the window with a profile that did not load, and a liquid
plan file that cannot be read.

    conda run -n lab python scripts/audit/gui/no_profile.py

1. `outputs/mock_profile/positions.json` holds `{not json`: the profile is
   refused whole. With the mock robot connected, is a task tile on Home
   enabled, and what does a click on it do?
2. `outputs/liquid/current.json` damaged three ways: does the window start?
"""
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent

CHILD = textwrap.dedent('''
    import logging, os, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import _mock
    app = _mock.app()
    uncaught = []
    class Catch(logging.Handler):
        def emit(self, record):
            if record.name == "micropick.uncaught":
                uncaught.append(repr(record.exc_info[1])[:150])
    logging.getLogger().addHandler(Catch())
    from micropick import paths
    from micropick.gui.app import Options
    from micropick.gui.shell import MainWindow
    case, text = sys.argv[2], sys.argv[3]
    if case == "profile":
        first = MainWindow(Options(mock=True)); first.session.shutdown(); first.deleteLater()
        (paths.outputs_dir() / "mock_profile" / "positions.json").write_text(text)
    else:
        folder = paths.outputs_dir() / "liquid"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "current.json").write_text(text)
    try:
        window = MainWindow(Options(mock=True))
    except Exception as exc:
        print("  the window did not start:", repr(exc)[:200]); sys.stdout.flush(); os._exit(0)
    window.show()
    window.session.probe_robot(); window.session.new_run()
    _mock.spin(0.5)
    print("  profile loaded:", window.session.profile is not None,
          "| Home says:", repr(window.home.error.text()[:90]))
    for task, tile in window.home.tiles.items():
        enabled = tile.isEnabled() and window.home.below.isEnabled()
        if enabled:
            try:
                tile.clicked.emit()
                _mock.spin(0.3)
            except Exception as exc:
                uncaught.append("raised: " + repr(exc)[:150])
        print(f"  tile {task}: enabled={enabled}, now on step {window.step!r}")
        window.open_home()
    print("  uncaught:", uncaught or "none")
    sys.stdout.flush(); os._exit(0)
''')

for case, text in (("profile", "{not json"), ("liquid", "{not json"),
                   ("liquid", ""), ("liquid", "[]")):
    print(f"--- {case} file holds {text!r}")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("MICROPICK_ROOT", None)
    done = subprocess.run([sys.executable, "-c", CHILD, str(HERE), case, text],
                          capture_output=True, text=True, env=env, timeout=120)
    for line in done.stdout.splitlines():
        print(line[:260])
    if done.returncode:
        print("  exit", done.returncode, done.stderr.strip().splitlines()[-1:])
