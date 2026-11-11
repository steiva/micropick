"""Test suite audit: every hand-made wait loop in the tests, by shape.

    python scripts/audit/wait_helpers.py [tests]

A wait helper is a function with a `while` loop that calls `processEvents`
(or `time.sleep`). For each: file, name, lines, and

- outcome: "asserts" (fails where the wait ran out), "returns" (the caller
  must assert it), or "silent" (a wait that ran out goes on as if it had
  not: the failure comes later, somewhere else);
- recheck: the condition is evaluated again after a last `processEvents()`
  following the loop, so what that last pass starts (a job chained on a
  zero timer) can turn a met condition into a failure;
- what it waits for, when it is fixed in the helper (`jog.busy`,
  `robot_lease.busy`, `job_pending`, `_robot_busy()`);
- calls: how often it is called in its file, and how many of the calls of
  a "returns" helper are not asserted.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path


def is_wait(node: ast.FunctionDef) -> bool:
    for loop in ast.walk(node):
        if isinstance(loop, ast.While):
            text = ast.unparse(loop)
            if "processEvents" in text or "sleep" in text:
                return True
    return False


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
    shapes: Counter = Counter()
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not is_wait(node):
                continue
            if node.name.startswith("test_"):
                continue
            body = node.body
            loop_at = next(i for i, s in enumerate(body)
                           if any(isinstance(n, ast.While) for n in ast.walk(s)))
            after = body[loop_at + 1:]
            after_text = "\n".join(ast.unparse(s) for s in after)
            asserts = any(isinstance(n, ast.Assert) for s in after for n in ast.walk(s))
            returns = any(isinstance(n, ast.Return) and n.value is not None
                          for s in after for n in ast.walk(s))
            outcome = "asserts" if asserts else "returns" if returns else "silent"
            recheck = "processEvents" in after_text and (asserts or returns)
            text = ast.unparse(node)
            waits_for = next((w for w in ("job_pending", "robot_lease.busy",
                                          "_robot_busy()", "_robot_free", ".busy")
                              if w in ast.unparse(body[loop_at])), "a condition given")
            calls = unasserted = 0
            for other in ast.walk(tree):
                if (isinstance(other, ast.Call) and isinstance(other.func, ast.Name)
                        and other.func.id == node.name):
                    calls += 1
            for stmt in ast.walk(tree):
                if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                        and isinstance(stmt.value.func, ast.Name)
                        and stmt.value.func.id == node.name):
                    unasserted += 1
            shapes[(outcome, recheck)] += 1
            note = (f", {unasserted} not asserted" if outcome == "returns"
                    and unasserted else "")
            print(f"{str(path.relative_to(root)):42} {node.name:16} "
                  f"{node.end_lineno - node.lineno + 1:2} lines  {outcome:8} "
                  f"{'recheck' if recheck else '-':8} waits for {waits_for:18} "
                  f"{calls:3} calls{note}")
    print()
    for (outcome, recheck), n in sorted(shapes.items()):
        print(f"{n:3} helpers: {outcome}{', recheck after a last processEvents' if recheck else ''}")


if __name__ == "__main__":
    main()
