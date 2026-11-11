"""Category D: `.connect(` calls outside `__init__` and builders (GUI audit).

    python scripts/audit/gui/connects.py src/micropick/gui

A connection made in a method that runs more than once (a refresh, a show,
an open) and never disconnected fires twice, three times, ... This lists
every `.connect(` whose enclosing function is not `__init__`, not called
`_build*`/`_make*`/`*_card`/`_install*`, so each can be read: is the object
connected new each time (fine), or one that lives on (a leak)?
"""
import ast
import sys
from pathlib import Path

BUILDERS = ("__init__", "_build", "_make", "_install", "_add", "_wire")
for path in sorted(Path(sys.argv[1]).rglob("*.py")):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef,)):
            continue
        name = node.name
        if name.startswith(BUILDERS) or name.endswith(("_card", "_row", "_section", "_buttons")):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "connect"):
                target = ast.unparse(call.func.value)
                print(f"{path.relative_to(sys.argv[1])}:{call.lineno} {name}: {target}")
