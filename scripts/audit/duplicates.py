"""Test suite audit: copied helpers, fixtures and near-identical tests.

    python scripts/audit/duplicates.py [tests] [--similar 0.85]

Three lists, all from the source alone (nothing is imported or run):

1. Module-level helpers and fixtures (not tests) whose body is the same in
   several files once names of arguments are kept and docstrings dropped:
   what a shared conftest or helper module would hold once.
2. Test names that occur in more than one file.
3. Pairs of tests whose token sets overlap by `--similar` or more (Jaccard,
   over 3-token shingles of the body with literals replaced by a
   placeholder): candidates for one `@pytest.mark.parametrize`. Clusters
   are printed, largest first.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from collections import defaultdict
from pathlib import Path


def body_source(node: ast.FunctionDef) -> str:
    body = node.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(statement) for statement in body)


def shingles(source: str) -> frozenset:
    tokens = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.NUMBER, tokenize.STRING):
                tokens.append("LIT")
            elif tok.type in (tokenize.NAME, tokenize.OP):
                tokens.append(tok.string)
    except tokenize.TokenError:
        pass
    return frozenset(zip(tokens, tokens[1:], tokens[2:]))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0] if args else "tests")
    threshold = (float(sys.argv[sys.argv.index("--similar") + 1])
                 if "--similar" in sys.argv else 0.85)
    helpers: dict[str, list[str]] = defaultdict(list)
    names: dict[str, list[str]] = defaultdict(list)
    tests: list[tuple[str, frozenset, int]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            where = f"{path.relative_to(root)}::{node.name}"
            source = body_source(node)
            if node.name.startswith("test_"):
                names[node.name].append(str(path.relative_to(root)))
                lines = node.end_lineno - node.lineno + 1
                tests.append((where, shingles(source), lines))
            else:
                helpers[source].append(where)

    print("== 1. helpers and fixtures with the same body in several files")
    saved = 0
    for source, places in sorted(helpers.items(), key=lambda kv: -len(kv[1])):
        files = {p.split("::")[0] for p in places}
        if len(files) < 2:
            continue
        lines = source.count("\n") + 2
        saved += lines * (len(places) - 1)
        print(f"{len(places):3}x  {lines:2} lines  {places[0].split('::')[1]}"
              f"  e.g. {', '.join(sorted(files)[:4])}")
    print(f"lines a single copy would save: about {saved}")

    print("\n== 2. test names in more than one file")
    for name, files in sorted(names.items()):
        if len(files) > 1:
            print(f"{name}: {', '.join(files)}")

    print(f"\n== 3. tests alike by {threshold:.2f} or more")
    parent = list(range(len(tests)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (_, a, _) in enumerate(tests):
        if len(a) < 12:
            continue
        for j in range(i + 1, len(tests)):
            b = tests[j][1]
            if len(b) < 12 or not (0.6 < len(a) / len(b) < 1.67):
                continue
            if len(a & b) / len(a | b) >= threshold:
                parent[find(i)] = find(j)
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tests)):
        clusters[find(i)].append(i)
    total = 0
    for members in sorted(clusters.values(), key=len, reverse=True):
        if len(members) < 2:
            continue
        lines = sum(tests[i][2] for i in members)
        total += lines - max(tests[i][2] for i in members)
        print(f"-- {len(members)} tests, {lines} lines")
        for i in members:
            print(f"   {tests[i][0]}")
    print(f"lines beyond one copy per cluster: about {total}")


if __name__ == "__main__":
    main()
