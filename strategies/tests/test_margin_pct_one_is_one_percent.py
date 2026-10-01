"""Catalog-wide guard: the "pasted v2 fraction -> x100" margin guard must be STRICTLY below 1.

`marginPct` is a PERCENT in (0,100], and exactly 1 is a legal 1% — the runtime's own check
(`findMarginPctFraction`, senpi-trading-runtime src/validate/recipe-checks.ts) lets it through.
The dire/koala guard was copied into 47 packages as `if margin_pct <= 1.0: margin_pct *= 100`, so a
user who asked for 1% per slot was sized at 100% of withdrawable, silently. This refuses any
`x <= 1` test whose branch multiplies by 100.

Run:
  python3 -m pytest strategies/tests -q
"""
import ast
import glob
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _le_one(test):
    return any(
        isinstance(op, ast.LtE) and isinstance(rhs, ast.Constant) and rhs.value == 1
        for cmp in ast.walk(test) if isinstance(cmp, ast.Compare)
        for op, rhs in zip(cmp.ops, cmp.comparators)
    )


def x100_guards_at_one(src):
    """Line of every `if x <= 1: ...100...` / `...100... if x <= 1 else ...` in `src`."""
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.If, ast.IfExp)) and _le_one(node.test):
            branch = [node.body] if isinstance(node, ast.IfExp) else node.body
            if any(isinstance(n, ast.Constant) and n.value == 100 for b in branch for n in ast.walk(b)):
                hits.append(node.lineno)
    return hits


def test_detector_catches_the_shape_it_guards():
    assert x100_guards_at_one("if m <= 1.0:\n    m *= 100.0\n") == [1]
    assert x100_guards_at_one("m = round(m * 100, 2) if 0 < m <= 1.0 else m\n") == [1]
    assert x100_guards_at_one("if m < 1.0:\n    m *= 100.0\n") == []


def test_no_scanner_turns_one_percent_into_one_hundred():
    bad = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "*", "*", "scanners", "*.py"))):
        with open(path, encoding="utf-8") as fh:
            bad += [f"{os.path.relpath(path, _ROOT)}:{ln}" for ln in x100_guards_at_one(fh.read())]
    assert not bad, f"a `<= 1` fraction guard sizes a legal 1% as 100% — use `< 1`: {bad}"
