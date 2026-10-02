"""Build the Windows package: `python packaging/build.py`.

1. Stamp the build: `src/micropick/_build.py` gets the commit (and "+dirty"
   when the tree has uncommitted changes) so the window title, the log and
   a report say exactly what was built. The file is removed afterwards and
   is ignored by git.
2. PyInstaller with `packaging/micropick.spec`, into `dist/micropick/`.
3. Zip it as `dist/micropick-<version>-win64.zip`: the thing to hand over.

A release is built from a clean tree on a tagged commit (`v<version>`, see
CHANGELOG.md); `--allow-dirty` builds anyway, marked as such, for trying a
build out.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = ROOT / "src" / "micropick" / "_build.py"
DIST = ROOT / "dist"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def version() -> str:
    namespace: dict = {}
    exec((ROOT / "src" / "micropick" / "_version.py").read_text(
        encoding="utf-8").split("try:")[0], namespace)
    return namespace["__version__"]


def stamp(dirty: bool) -> str:
    commit = git("rev-parse", "--short", "HEAD")
    build = commit + ("+dirty" if dirty else "")
    STAMP.write_text(f'BUILD = "{build}, built {time.strftime("%Y-%m-%d")}"\n',
                     encoding="utf-8")
    return build


def zip_folder(folder: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, Path(folder.name) / path.relative_to(folder))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--allow-dirty", action="store_true",
                        help="build with uncommitted changes (marked +dirty)")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    if dirty and not args.allow_dirty:
        print("The tree has uncommitted changes. Commit them, or pass "
              "--allow-dirty for a build marked +dirty.")
        return 1
    v = version()
    tag = f"v{v}"
    tagged = tag in git("tag", "--points-at", "HEAD").split()
    build = stamp(dirty)
    print(f"building micropick {v} ({build})"
          + ("" if tagged else f" - HEAD is not tagged {tag}"))
    # ultralytics pip-installs into the environment on import unless told
    # not to; the spec says so too, this covers its analysis subprocesses.
    env = dict(os.environ, YOLO_AUTOINSTALL="false")
    try:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "--clean", "--distpath", str(DIST),
                        "--workpath", str(ROOT / "build"),
                        str(ROOT / "packaging" / "micropick.spec")],
                       cwd=ROOT, check=True, env=env)
    finally:
        STAMP.unlink(missing_ok=True)

    folder = DIST / "micropick"
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print(f"built {folder} ({size / 2**20:.0f} MB)")
    if not args.no_zip:
        target = DIST / f"micropick-{v}{'-dirty' if dirty else ''}-win64.zip"
        target.unlink(missing_ok=True)
        zip_folder(folder, target)
        print(f"zipped {target} ({target.stat().st_size / 2**20:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
