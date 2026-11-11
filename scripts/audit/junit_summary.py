"""Test suite audit: what failed in which run, from the junit files.

    python scripts/audit/junit_summary.py OUT [--slow N]

Prints each run's totals, then every test that failed or errored at least
once with the runs it failed in and the first line of its message. With
`--slow N`, the N slowest tests of each serial run too (call time, with
setup and teardown as junit counts them).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1])
    slow = int(sys.argv[sys.argv.index("--slow") + 1]) if "--slow" in sys.argv else 0
    failed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path in sorted(out.glob("*.xml")):
        suite = ET.parse(path).getroot().find("testsuite")
        print(f"{path.stem:18} tests={suite.get('tests')} "
              f"failures={suite.get('failures')} errors={suite.get('errors')} "
              f"skipped={suite.get('skipped')} time={float(suite.get('time')):.0f}s")
        times = []
        for case in suite.iter("testcase"):
            name = f"{case.get('classname')}::{case.get('name')}"
            times.append((float(case.get("time", 0)), name))
            for kind in ("failure", "error"):
                for node in case.findall(kind):
                    message = (node.get("message") or "").splitlines()
                    failed[name].append((path.stem, f"{kind}: {message[0][:160] if message else ''}"))
        if slow and path.stem.startswith("ser"):
            for seconds, name in sorted(times, reverse=True)[:slow]:
                print(f"    {seconds:6.2f}s {name}")
    print()
    for name, runs in sorted(failed.items(), key=lambda item: -len(item[1])):
        print(f"{len(runs)}x {name}")
        for run, message in runs:
            print(f"    {run}: {message}")


if __name__ == "__main__":
    main()
