"""A scanner may not read an API field that Hyperliquid never returns, with no real fallback.

2026-09-24: `orca` (and `penguin`, forked from it hours earlier) computed its volume-confirmation
gate as `dayNtlVlm / prevDayNtlVlm`. HL's asset context has no `prevDayNtlVlm` — its previous-day
field is `prevDayPx`, a PRICE. So the divisor was always 0, the ratio branch never ran, and the
function returned its permissive `(0, True)` on every call. The gate was dead from launch while
`VOL_CONFIRMED` still printed downstream: 982 zero-ratio lines in telemetry.

What makes the class worth a guard is that it is SILENT. A missing key does not raise: `.get(k, 0)`
yields the default, and a gate written to fail open then passes everything. The only symptom is a
metric pinned at zero, which reads like a quiet market rather than a bug.

## The rule this encodes

A phantom key is only a defect when **no real key is reachable in the same fallback chain.** The
tree is full of deliberate cross-version chains that are perfectly fine:

    c.get("volume", c.get("v", c.get("vlm", 0)))      # OK — "v" is real
    ctx_.get("openInterest") or ctx_.get("open_interest")   # OK — "openInterest" is real
    ac.get("max_leverage", ac.get("maxLeverage"))     # BUG — neither exists
    ac.get("prevDayNtlVlm", 0)                        # BUG — the original

So this collects the whole alternative set for each read and fails only when the set and reality are
disjoint. A regex cannot do that, and a regex over one variable name (`ac`) was the first version of
this file — it missed `ctxb`, `ctx_`, `asset_ctx`, `ctx_block` and every inline chained form, which
is how three further phantoms went unnoticed for an hour. Hence the AST.

Key sets are the live responses, verified 2026-09-24. If HL adds a field, add it here in the same PR
that uses it — and verify against a real response, not a doc, which is how the original got in.

Run: python3 -m pytest strategies/tests/test_no_phantom_asset_context_keys.py -q
"""
import ast
import os

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ""))

# market_get_asset_data(BTC).data.asset_context — live, 2026-09-24.
REAL_ASSET_CONTEXT_KEYS = {
    "coin", "dayBaseVlm", "dayNtlVlm", "funding", "impactPxs", "markPx",
    "midPx", "openInterest", "oraclePx", "premium", "prevDayPx",
}
# ...data.candles["1h"][i] — same response.
REAL_CANDLE_KEYS = {"t", "T", "s", "i", "o", "c", "h", "l", "v", "n"}

_AC_NAMES = {"asset_context", "assetContext"}

# Known-unfixed, with the reason. Not a dismissal: each entry is a real finding whose fix is a
# design decision someone owns, recorded here so it cannot be forgotten or silently re-introduced.
KNOWN_UNFIXED = {
    # `venue = ac.get("max_leverage", ac.get("maxLeverage"))` — neither key exists, so venue is
    # always None and `sizing_for` does `clamp_leverage(lev, lev)`: the venue leverage cap has
    # never clamped anything in these four. The correct source is the
    # strategy_get_asset_trading_limits MCP call (orca uses it), but wiring that in costs one read
    # per candidate and lands in the middle of the MCP-usage-reduction work — an owner's call, not
    # a drive-by in a volume-gate PR. Found 2026-09-24 by this guard.
    ("chimp", "max_leverage"),
    ("gibbon", "max_leverage"),
    ("gorilla", "max_leverage"),
    ("orangutan", "max_leverage"),
}


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _unwrap(node):
    """Strip `(x or {})` wrappers down to the first operand."""
    while isinstance(node, ast.BoolOp) and node.values:
        node = node.values[0]
    return node


def _is_asset_context_expr(node):
    node = _unwrap(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if node.args and _str(node.args[0]) in _AC_NAMES:
            return True
        if len(node.args) > 1 and _is_asset_context_expr(node.args[1]):
            return True
    return False


def _chain_keys(node):
    """Every key reachable as an alternative in one read expression.

    Handles the nested-default form `x.get("a", x.get("b", 0))` and the or-chain form
    `x.get("a") or x.get("b")`, which is how every cross-version fallback in the tree is written.
    """
    keys = set()
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            keys |= _chain_keys(v)
        return keys
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if node.args:
            k = _str(node.args[0])
            if k:
                keys.add(k)
        if len(node.args) > 1:
            keys |= _chain_keys(node.args[1])
    return keys


def _collect(path, pkg):
    """(pkg, rel, surface, frozenset(chain keys)) for every asset-context / candle read."""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
    except SyntaxError:
        return []

    ac_vars, candle_vars = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_asset_context_expr(node.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    ac_vars.add(t.id)
        if isinstance(node, (ast.For, ast.comprehension)):
            it = getattr(node, "iter", None)
            dump = ast.dump(it) if it is not None else ""
            if "candle" in dump.lower() or "'1h'" in dump or '"1h"' in dump or "'4h'" in dump or '"4h"' in dump:
                tgt = getattr(node, "target", None)
                if isinstance(tgt, ast.Name):
                    candle_vars.add(tgt.id)

    def surface_of(node):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args):
            return None
        base = node.func.value
        if (isinstance(base, ast.Name) and base.id in ac_vars) or _is_asset_context_expr(base):
            return "asset_context"
        if isinstance(base, ast.Name) and base.id in candle_vars:
            return "candle"
        return None

    def nested(node):
        """Read-calls reachable inside one chain — they are part of it, not separate reads."""
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                yield from ([v] if surface_of(v) else [])
                yield from nested(v)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and len(node.args) > 1:
            d = node.args[1]
            if surface_of(d):
                yield d
            yield from nested(d)

    # An `x.get("a", x.get("b", 0))` chain visits its inner call again under ast.walk, which would
    # report `{b}` alone and fail a chain that is actually fine. Only the OUTERMOST read counts.
    consumed = set()
    units = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and any(surface_of(v) for v in node.values):
            units.append(node)
        elif surface_of(node):
            units.append(node)
    for node in units:
        for inner in nested(node):
            consumed.add(id(inner))

    rel = os.path.relpath(path, _ROOT)
    seen, out = set(), []
    for node in units:
        if id(node) in consumed:
            continue
        if isinstance(node, ast.BoolOp):
            surface = next((surface_of(v) for v in node.values if surface_of(v)), None)
        else:
            surface = surface_of(node)
        if surface is None:
            continue
        chain = _chain_keys(node) - _AC_NAMES
        if not chain:
            continue
        item = (pkg, rel, surface, frozenset(chain))
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _reads():
    out = []
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "tests"}]
        if os.path.basename(dirpath) != "scanners":
            continue
        pkg = os.path.relpath(dirpath, _ROOT).split(os.sep)[0]
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.extend(_collect(os.path.join(dirpath, name), pkg))
    return sorted(out, key=lambda r: (r[0], r[1], sorted(r[3])))


_READS = _reads()
_REAL = {"asset_context": REAL_ASSET_CONTEXT_KEYS, "candle": REAL_CANDLE_KEYS}


def test_the_sweep_found_reads():
    """Guard the guard: a walker that matched nothing would make the test below vacuous."""
    assert len(_READS) > 50, f"only {len(_READS)} reads found — AST walk or layout changed"


def test_both_surfaces_are_covered():
    """Both key sets must actually be exercised, or half this file is dead."""
    surfaces = {r[2] for r in _READS}
    assert surfaces == {"asset_context", "candle"}, surfaces


@pytest.mark.parametrize("pkg,rel,surface,chain", _READS)
def test_read_can_resolve_against_a_real_field(pkg, rel, surface, chain):
    real = _REAL[surface]
    if chain & real:
        return  # a real key is reachable in this fallback chain — fine
    if any((pkg, k) in KNOWN_UNFIXED for k in chain):
        pytest.skip(f"{pkg}: {sorted(chain)} is a documented open finding — see KNOWN_UNFIXED")
    pytest.fail(
        f"{rel} reads {surface} {sorted(chain)}, none of which Hyperliquid returns.\n"
        f"Real {surface} keys: {', '.join(sorted(real))}.\n"
        "A missing key does not raise — .get() returns the default and any gate built on it "
        "silently passes everything. Verify the field against a real response, not a doc; if HL "
        "genuinely added it, add it to the key set in this PR."
    )
