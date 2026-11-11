"""Test suite audit: coverage per module, and what a set of tests alone covers.

Make the data serially (the plugins come from a `pip install --target`
folder, so the `lab` env stays as it is):

    PYTHONPATH=<plugins> COVERAGE_FILE=<out>/base.coverage \
        conda run -n lab python -m pytest -q -p no:randomly -p no:cacheprovider \
        --cov=micropick --cov=opentrons_api --cov-report= tests

Then:

    PYTHONPATH=<plugins> python scripts/audit/coverage_report.py \
        <out>/base.coverage [--modules] [--missing MODULE ...]

        --modules   statements, missed, percent per module (the default)
        --missing   the lines of the modules named that no test runs

    PYTHONPATH=<plugins> python scripts/audit/coverage_report.py \
        --diff <out>/base.coverage <out>/without.coverage

        the lines the first run executed and the second did not, per
        module: what a deletion would lose. Make the second file with the
        same command plus `--deselect` for each test proposed for deletion
        (or with the tests already merged). Nothing printed under a module
        means nothing lost there.

Why not per-test contexts (`--cov-context=test`): on Python 3.12+ coverage
measures through sys.monitoring, which sees the jobs' QThreads but stops
reporting a line once it has run, so the contexts are incomplete (coverage
warns "no-sysmon-context"); the older tracer (COVERAGE_CORE=ctrace) keeps
contexts but does not see a QThread at all, and most robot code here runs
on one. A second whole run without the tests in question is the check that
is right for this code base. A few lines differ from run to run by
themselves (a retry, a race lost): run the base twice and take what both
diffs agree on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import coverage


def load(path: str):
    cov = coverage.Coverage(data_file=path)
    cov.load()
    return cov


def short(filename: str) -> str:
    parts = Path(filename).parts
    for anchor in ("micropick", "opentrons_api"):
        if anchor in parts[::-1]:
            at = len(parts) - 1 - parts[::-1].index(anchor)
            return "/".join(parts[at:])
    return filename


def analysis(cov) -> dict[str, tuple[set[int], set[int]]]:
    """{module: (statements, missing)}"""
    result = {}
    for filename in sorted(cov.get_data().measured_files()):
        try:
            _, statements, _, missing, _ = cov.analysis2(filename)
        except coverage.CoverageException:
            continue
        result[short(filename)] = (set(statements), set(missing))
    return result


def modules(cov) -> None:
    total_n = total_m = 0
    for name, (statements, missing) in analysis(cov).items():
        n, m = len(statements), len(missing)
        total_n, total_m = total_n + n, total_m + m
        print(f"{name:52} {n:5} {m:5} {100 * (n - m) / max(n, 1):6.1f}%")
    print(f"{'TOTAL':52} {total_n:5} {total_m:5} "
          f"{100 * (total_n - total_m) / max(total_n, 1):6.1f}%")


def missing(cov, wanted: list[str]) -> None:
    for filename in sorted(cov.get_data().measured_files()):
        if any(short(filename).endswith(w) for w in wanted):
            _, _, _, lines, text = cov.analysis2(filename)
            print(f"{short(filename)}: {len(lines)} lines not run: {text}")


def ranges(lines: list[int]) -> str:
    out, start, last = [], None, None
    for line in lines:
        if start is None:
            start = last = line
        elif line == last + 1:
            last = line
        else:
            out.append(f"{start}-{last}" if last > start else f"{start}")
            start = last = line
    if start is not None:
        out.append(f"{start}-{last}" if last > start else f"{start}")
    return ", ".join(out)


def diff(base_path: str, other_path: str) -> None:
    base, other = analysis(load(base_path)), analysis(load(other_path))
    lost_total = gained_total = 0
    for name, (statements, base_missing) in base.items():
        other_missing = other.get(name, (statements, statements))[1]
        lost = sorted((statements - base_missing) & other_missing)
        gained = sorted(base_missing - other_missing)
        lost_total, gained_total = lost_total + len(lost), gained_total + len(gained)
        if lost:
            print(f"{name}: {len(lost)} lines lost: {ranges(lost)}")
    print(f"lines run by the first and not the second: {lost_total}; "
          f"the other way: {gained_total}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--diff":
        diff(args[1], args[2])
        return
    cov = load(args[0])
    flags = args[1:]
    if "--missing" in flags:
        missing(cov, [a for a in flags[flags.index("--missing") + 1:]
                      if not a.startswith("--")])
    if "--modules" in flags or not flags:
        modules(cov)


if __name__ == "__main__":
    main()
