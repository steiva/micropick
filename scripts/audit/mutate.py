"""Test suite audit: break the code on purpose and see whether a test fails.

    conda run -n lab python scripts/audit/mutate.py [-n 6] [--all-tests] [name ...]

Each entry of MUTATIONS replaces one exact string in one source file, runs
the tests named for it (the whole suite with `--all-tests`), and puts the file
back with `git checkout`. "caught" means a test failed; "SURVIVED" means
every test passed with the code broken: nothing pins that behavior.
Refuses to start on a dirty `src/`, and checks `git diff --quiet src` at
the end. A file's `.pyc` is rewritten by Python as the source changes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "micropick"

# name: (file under src/micropick, old, new, pytest targets)
MUTATIONS = {
    "lock ignored by the lease": (
        "hardware/robot_lease.py",
        "            if self._locked:\n",
        "            if False:\n",
        ["tests/hardware/test_robot_lease.py", "tests/gui/test_lock.py"]),
    "lease given twice": (
        "hardware/robot_lease.py",
        "            if self._held is not None:\n",
        "            if False:\n",
        ["tests/hardware", "tests/gui/test_robot_lease_pages.py",
         "tests/gui/test_top_bar_park.py"]),
    "a failed job keeps the lease": (
        "gui/workers/base.py",
        "            self.error = exc\n            self._release()\n",
        "            self.error = exc\n",
        ["tests/gui/test_worker.py", "tests/gui/test_jog_says.py",
         "tests/gui/test_top_bar_retract.py", "tests/gui/test_robot_lease_pages.py"]),
    "a stale job's end taken as the panel's": (
        "gui/widgets/jog_panel.py",
        "        if self.sender() is not self._worker:\n            return False\n",
        "",
        ["tests/gui/test_jog_pose_kept.py", "tests/gui/test_jog_says.py",
         "tests/gui/test_jog_pad_panel.py", "tests/gui/test_manual_page.py"]),
    "under the measured bottom allowed to 5 mm": (
        "workflows/manual.py",
        "    limit = UNDER_MEASURED_BOTTOM_MM if measured else UNDER_DRAWN_BOTTOM_MM\n",
        "    limit = 5.0\n",
        ["tests/workflows", "tests/gui/test_manual_page.py",
         "tests/gui/test_manual_well_height.py"]),
    "a stored pose reached above the travel height": (
        "workflows/motion.py",
        "            pose[2] = min(float(pose[2]), _travel(top))\n",
        "            pose[2] = float(pose[2])\n",
        ["tests/workflows", "tests/gui/test_setup_sheets.py"]),
    "raise_tip never retracts once the top is known": (
        "workflows/motion.py",
        "        if top is not None and xyz(robot)[2] >= _travel(top) - READ_TOL_MM:\n            return top\n        return self.retract(log=log)",
        "        if top is not None:\n            return top\n        return self.retract(log=log)",
        ["tests/workflows", "tests/gui/test_pipette_page.py",
         "tests/gui/test_dish_bottom.py"]),
    "the second Stop never holds": (
        "gui/shell.py",
        "        if now < self._stop_again_until and any_running():\n",
        "        if False:\n",
        ["tests/gui/test_top_bar_park.py", "tests/gui/test_shell.py",
         "tests/gui/test_run_status.py"]),
    "closing during a run does not ask": (
        "gui/shell.py",
        "            if answer != QMessageBox.StandardButton.Yes:\n",
        "            if False:\n",
        ["tests/gui/test_top_bar_park.py", "tests/gui/test_shell.py"]),
    "the window closes while the robot is still busy": (
        "gui/shell.py",
        "            self.refuse(f\"Not closed: the {lease.holder} is still stopping. \"\n                        f\"Close again once it has.\")\n            event.ignore()\n            return\n",
        "            pass\n",
        ["tests/gui/test_top_bar_park.py", "tests/gui/test_shell.py"]),
    "a write is not atomic (no replace)": (
        "config/store.py",
        "        os.replace(tmp, path)\n",
        "        Path(path).write_text(payload, encoding=\"utf-8\")\n",
        ["tests/config", "tests/gui/test_last_profile.py",
         "tests/gui/test_deck_saves.py"]),
    "a half-written file is left on a failed write": (
        "config/store.py",
        "        Path(tmp).unlink(missing_ok=True)\n        raise\n",
        "        raise\n",
        ["tests/config", "tests/core"]),
    "unreadable profile JSON read as empty": (
        "config/store.py",
        "        raise ProfileError(f\"{path} is not valid JSON: {exc}\") from exc\n",
        "        return {}\n",
        ["tests/config", "tests/gui/test_last_profile.py",
         "tests/gui/test_settings_page.py"]),
    "a built app keeps its data beside the exe": (
        "paths.py",
        "        return Path.home() / \"Documents\" / \"micropick\"\n",
        "        return Path.cwd()\n",
        ["tests/config", "tests/gui/test_settings_page.py"]),
    "a dish bottom saved is not confirmed": (
        "gui/session.py",
        "        self.profile_changed.emit(self.profile)\n        self.confirm(\"dish_bottom\")\n",
        "        self.profile_changed.emit(self.profile)\n",
        ["tests/gui/test_dish_bottom.py", "tests/gui/test_tasks.py"]),
    "a tip on a well's wall counts as in the well": (
        "workflows/well_center.py",
        "        return hypot(dx, dy) <= well[\"diameter\"] / 2 - tip_radius\n",
        "        return hypot(dx, dy) <= well[\"diameter\"] / 2 + tip_radius\n",
        ["tests/workflows/test_well_center.py", "tests/gui/test_manual_page.py"]),
}


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def main() -> None:
    args = sys.argv[1:]
    workers = "6"
    if args[:1] == ["-n"]:
        workers, args = args[1], args[2:]
    everything = "--all-tests" in args
    args = [a for a in args if a != "--all-tests"]
    if run("git", "diff", "--quiet", "--", "src").returncode:
        raise SystemExit("src/ has changes: commit or stash them first")
    chosen = {k: v for k, v in MUTATIONS.items() if not args or k in args}
    for name, (rel, old, new, targets) in chosen.items():
        path = SRC / rel
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            print(f"SKIPPED   {name}: the text occurs {source.count(old)} "
                  f"times in {rel}")
            continue
        try:
            path.write_text(source.replace(old, new), encoding="utf-8")
            result = run(sys.executable, "-m", "pytest", "-q", "-x",
                         "-p", "no:cacheprovider", "-n", workers,
                         *(["tests"] if everything else targets))
        finally:
            run("git", "checkout", "--", str(path.relative_to(ROOT)))
        tail = [l for l in result.stdout.splitlines() if l.startswith("FAILED")
                or " passed" in l or " failed" in l or " error" in l]
        failed = [l for l in tail if l.startswith("FAILED")]
        # With -x under xdist a failure ends the run with exit 2.
        verdict = "caught  " if failed else (
            "SURVIVED" if result.returncode == 0 else f"exit {result.returncode}")
        print(f"{verdict}  {name}  [{rel}]")
        for line in failed[:3]:
            print(f"            {line[:150]}")
        if not failed and result.returncode != 0:
            print("\n".join(result.stdout.splitlines()[-8:]))
    clean = run("git", "diff", "--quiet", "--", "src").returncode == 0
    print("src/ is as it was." if clean else "src/ STILL HAS CHANGES")


if __name__ == "__main__":
    main()
