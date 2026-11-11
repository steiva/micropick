"""Category E: start-up with a file that cannot be read.

    conda run -n lab python scripts/audit/gui/bad_files.py

Makes a mock root (the window started once, a point saved, a liquid plan and
a plate plan template written), then, for every JSON file in it and each of
three damages - `{not json`, an empty file, valid JSON of the wrong shape
(`[]`) - starts the window on a copy with that one file damaged, connects the
mock robot and opens every step of every task. Reported: an exception at
start, uncaught exceptions in slots, and what Home says.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent

CHILD = textwrap.dedent('''
    import json, logging, os, sys
    sys.path.insert(0, sys.argv[1])
    import _mock
    app = _mock.app()
    uncaught = []
    class Catch(logging.Handler):
        def emit(self, record):
            if record.name == "micropick.uncaught":
                uncaught.append(str(record.exc_info[1])[:160] if record.exc_info else record.getMessage())
    logging.getLogger().addHandler(Catch())
    out = {"start": None, "home": "", "uncaught": uncaught, "steps": None}
    try:
        from micropick.gui import tasks
        from micropick.gui.app import Options
        from micropick.gui.shell import MainWindow
        window = MainWindow(Options(mock=True))
        window.show()
        out["home"] = window.home.error.text()[:200]
        out["profile"] = window.session.profile is not None
        try:
            window.session.probe_robot(); window.session.new_run()
            if sys.argv[2] == "seed":
                import numpy as np
                from micropick.config.labware import resolve_definition
                s = window.session
                s.load_labware(resolve_definition("corning_96_wellplate_360ul_flat"), 5)
                s.remember("observe", np.array([200.0, 200.0, 100.0]))
                s.add_memo_point(100.0, 100.0, 50.0)
                s.save_memo_point(s.memo_points[0].name, "corner")
                s.set_well_center(5, "corning_96_wellplate_360ul_flat", (0.1, 0.2, 0.3), "A1")
            for task in tasks.TASKS:
                for step in tasks.steps(task):
                    window.open_task_step(task, step)
                    _mock.spin(0.15)
            window.open_settings("general"); _mock.spin(0.1)
            window.open_home()
        except Exception as exc:
            out["steps"] = f"{type(exc).__name__}: {exc}"[:200]
        _mock.spin(1.0 if sys.argv[2] == "seed" else 0.2)
        window.close()
    except Exception as exc:
        out["start"] = f"{type(exc).__name__}: {exc}"[:300]
    print("RESULT " + json.dumps(out), flush=True)
    os._exit(0)
''')


def run(root: Path, mode: str) -> dict:
    env = dict(os.environ, MICROPICK_ROOT=str(root), QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", CHILD, str(HERE), mode],
                          capture_output=True, text=True, env=env, timeout=180)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    return {"start": f"no result, exit {done.returncode}: "
                     f"{done.stderr.strip().splitlines()[-1:]}"}


seed = Path(tempfile.mkdtemp(prefix="micropick-audit-seed-"))
print("seed:", run(seed, "seed"))
files = sorted(p for p in seed.rglob("*") if p.is_file()
               and (p.suffix == ".json" or p.name == "last")
               and "history" not in p.parts)
print("files:", [str(p.relative_to(seed)) for p in files])
DAMAGE = {"not json": "{not json", "empty": "", "wrong shape": "[]\n"}
print("\n| file | damage | at start | Home says | uncaught in slots | opening the steps |")
print("|---|---|---|---|---|---|")
for path in files:
    for name, text in DAMAGE.items():
        root = Path(tempfile.mkdtemp(prefix="micropick-audit-bad-"))
        shutil.copytree(seed, root, dirs_exist_ok=True)
        (root / path.relative_to(seed)).write_text(text)
        result = run(root, "check")
        shutil.rmtree(root, ignore_errors=True)
        print(f"| {path.relative_to(seed)} | {name} | {result.get('start') or 'ok'} | "
              f"{result.get('home') or '-'} | "
              f"{'; '.join(result.get('uncaught') or []) or '-'} | "
              f"{result.get('steps') or 'ok'} |")
