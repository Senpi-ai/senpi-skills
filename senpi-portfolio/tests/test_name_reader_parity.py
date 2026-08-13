#!/usr/bin/env python3
"""`_first_written` is vendored byte-identically into senpi-portfolio and senpi-improve-trades (skills
install standalone, so neither may import the other). This test fails the moment the two copies drift —
the two skills disagreeing about what counts as a written name is the divergence the reader exists to
close, and a copy edited in one home only reintroduces it silently.

TWO checks, guarding different things: the sha pins the shared HELPER, the chain table pins the ANSWER.
The sha alone can pass while behaviour diverges (a shadowing redefinition after the end marker, or a
reordered chain in one skill), so the second check is the load-bearing one — see its docstring.

senpi-strategy-ops' `_first_written` is deliberately NOT held to BYTE parity with the vendored one above:
it dispatches through a case-insensitive `dig()`, takes no `default=`, and does not strip. Converging it
would either make ops case-SENSITIVE (its docstring calls out the backend's case-normalization as
load-bearing) or make these two skills case-INSENSITIVE for every name read — a behaviour change neither
bug asked for. The three readers share a CHAIN, not an implementation; only these two share bytes.

That does NOT excuse `_cli.strategy_name_and_source` from an ANSWER check, the same way the vendored pair
gets one — an untested third copy of the reader is exactly the split-brain this suite exists to close
(the incident that motivated it: `status.py`, built on `_cli.py`, and `portfolio.py` naming the same
wallet two different things). `test_ops_chain_answers_match_portfolio_where_the_readers_cannot_disagree`
holds the two to the same answer everywhere the underlying `_first_written`s cannot disagree;
`test_ops_chain_diverges_only_where_pinned_as_intended` pins the specific rows where they legitimately
do (case-only key variants, whitespace-only strings, and non-string values — `dig()`'s `_first_written`
does not strip or exclude containers/bools the way the vendored one does), so those seams stay covered
rather than silently uncovered.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import ast
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COPIES = (os.path.join(HERE, "..", "scripts", "portfolio.py"),
          os.path.join(HERE, "..", "..", "senpi-improve-trades", "scripts", "review.py"))

OPS_CLI = os.path.join(HERE, "..", "..", "senpi-strategy-ops", "scripts", "_cli.py")
PORTFOLIO = os.path.join(HERE, "..", "scripts", "portfolio.py")

SHARED_HELPERS = {"run_cli": "_run_cli", "_extract_json": "_extract_json"}

for _d in (os.path.join(HERE, "..", "scripts"),
           os.path.join(HERE, "..", "..", "senpi-improve-trades", "scripts"),
           os.path.join(HERE, "..", "..", "senpi-strategy-ops", "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import portfolio  # noqa: E402
import review  # noqa: E402
import _cli  # noqa: E402

_BLOCK = re.compile(r"^# ── VENDORED,.*?^# ── end vendored block$", re.S | re.M)


def _vendored_block(path):
    with open(path, encoding="utf-8") as f:
        found = _BLOCK.search(f.read())
    assert found, f"vendored `_first_written` block not found in {path}"
    return found.group(0)


def _fn_source(path, name):
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            norm = ast.parse(ast.unparse(node)).body[0]
            # ast.dump() takes a single AST node, not a bare list of statements — wrap the body
            # in a Module so bodies compare (the rename run_cli -> _run_cli is intentional, so the
            # function name itself must not be part of the comparison). The SIGNATURE is compared
            # too: `timeout=60` drifting to `timeout=30` in one copy is the same silent divergence
            # with every statement still matching.
            return (ast.dump(ast.Module(body=norm.body, type_ignores=[]))
                    + ast.dump(norm.args))
    raise AssertionError(f"{name} not found in {path}")


def test_vendored_cli_helpers_match_their_origin():
    """The name reader was held byte-identical while the state-dir resolver — copied from the same
    origin, twenty lines away, gating strictly more of the output — drifted unnoticed. Parity that
    covers only the cheapest shared function certifies the wrong thing."""
    for origin, vendored in SHARED_HELPERS.items():
        assert _fn_source(OPS_CLI, origin) == _fn_source(PORTFOLIO, vendored), (
            f"{vendored} in portfolio.py has drifted from {origin} in _cli.py"
        )


def test_first_written_vendor_parity():
    assert all(os.path.exists(p) for p in COPIES), "a vendor home is missing"
    blocks = [_vendored_block(p) for p in COPIES]
    shas = [hashlib.sha256(b.encode("utf-8")).hexdigest() for b in blocks]
    assert shas[0] == shas[1], (
        "`_first_written` DRIFTED between senpi-portfolio and senpi-improve-trades — re-vendor the block "
        "byte-identically (the two skills must answer 'is this a written name?' the same way)")


# Rows spanning every leg of the chain plus the shapes the two readers exist to agree on: a written
# name, silence at the first leg (null / empty / whitespace), a shape that is not a name at all, the
# flat-payload alias, a scalar, and total silence.
_CHAIN_ROWS = (
    {"strategyName": "cougar-long", "tradingStrategyName": "cougar"},
    {"strategyName": None, "tradingStrategyName": "cougar"},
    {"strategyName": "", "tradingStrategyName": "cougar"},
    {"strategyName": "   ", "tradingStrategyName": "cougar"},
    {"strategyName": {"oops": 1}, "tradingStrategyName": "cougar"},
    {"strategyName": True, "tradingStrategyName": "cougar"},
    {"strategyName": 42},
    {"name": "flat-payload-alias"},
    {"strategyName": None, "tradingStrategyName": None, "name": ""},
    {},
)


def test_the_two_skills_answer_the_same_chain_the_same_way():
    """THE guard. The sha above pins the shared helper; this pins the ANSWER, which is what users see.

    Byte-parity alone is not enough and demonstrably passes while behaviour diverges: a copy can define
    a second `_first_written` AFTER the end marker (shadowing the vendored one), or a skill can reorder
    the legs of its own chain — the block hashes identically in both cases. Each skill's own tests catch
    a chain edit only if the editor updates that skill's tests too, and then the OTHER skill diverges
    silently. That is precisely the split-brain this slice exists to remove, so the two chains are
    compared against each other directly, not each against its own expectations."""
    for row in _CHAIN_ROWS:
        assert review._strategy_label(row) == portfolio._strategy_name_and_source(row)[0], row


# The subset of the shared chain where `_cli.strategy_name_and_source` (dig()-based `_first_written`,
# senpi-strategy-ops/scripts/_cli.py) and `portfolio._strategy_name_and_source` (the vendored,
# strip+container/bool-excluding `_first_written` above) CANNOT disagree: every leg is either absent,
# `None`, a plain written string, or resolved purely by exact-cased keys. Every row here is asserted
# EQUAL, full `(name, name_source)` tuple.
_OPS_AGREE_ROWS = (
    {"strategyName": "cougar-long", "tradingStrategyName": "cougar"},
    {"strategyName": None, "tradingStrategyName": "cougar"},
    {"strategyName": "", "tradingStrategyName": "cougar"},
    {"name": "flat-payload-alias"},
    {"strategyName": None, "tradingStrategyName": None, "name": ""},
    {},
)


def test_ops_chain_answers_match_portfolio_where_the_readers_cannot_disagree():
    """The guard `_cli.strategy_name_and_source` didn't have: it shares the CHAIN with
    `portfolio._strategy_name_and_source` (see its docstring) but is built on a different
    `_first_written`, and nothing held the two ANSWERS in agreement. This is the same split-brain this
    task exists to close, one layer down: `status.py` (built on `_cli.py`) and `portfolio.py` reading
    `strategy_list` and landing on two different names for the same wallet."""
    for row in _OPS_AGREE_ROWS:
        assert _cli.strategy_name_and_source(row) == portfolio._strategy_name_and_source(row), row


# Genuine, INTENDED divergences — `dig()`'s `_first_written` is case-insensitive, does not `.strip()`,
# and does not exclude a container/bool the way the vendored one does (see both docstrings). Each row is
# pinned to BOTH readers' real answers, so a convergence (the row starts agreeing) and a NEW divergence
# opening up elsewhere are both caught, instead of this seam staying uncovered.
_OPS_DIVERGE_ROWS = (
    # case-insensitive key match: `dig()` sees `STRATEGYNAME`; portfolio's exact `.get("strategyName")`
    # does not, and falls through every leg to the "strategy" default.
    ({"STRATEGYNAME": "cougar-caps"},
     ("cougar-caps", "strategyName"), ("strategy", None)),
    # whitespace-only: portfolio strips to "" (silence) and falls through to the next leg; `dig()`
    # returns the raw unstripped string, which is truthy.
    ({"strategyName": "   ", "tradingStrategyName": "cougar"},
     ("   ", "strategyName"), ("cougar", "tradingStrategyName")),
    # a dict is never a name in portfolio's chain (explicitly excluded); `dig()`'s `_first_written` has
    # no such exclusion and hands the container back verbatim.
    ({"strategyName": {"oops": 1}, "tradingStrategyName": "cougar"},
     ({"oops": 1}, "strategyName"), ("cougar", "tradingStrategyName")),
    # a bool is never a name in portfolio's chain; `dig()`'s `_first_written` has no such exclusion.
    ({"strategyName": True, "tradingStrategyName": "cougar"},
     (True, "strategyName"), ("cougar", "tradingStrategyName")),
    # a bare scalar: portfolio stringifies it (`"42"`); `dig()`'s `_first_written` returns the raw int
    # (`42`) — same digits, not the same value by `==`.
    ({"strategyName": 42}, (42, "strategyName"), ("42", "strategyName")),
)


def test_ops_chain_diverges_only_where_pinned_as_intended():
    """Every known divergence between the two readers, pinned to its real answer on both sides rather
    than left as an untested gap. Also guards the pin itself: if a row stops diverging, it belongs in
    `_OPS_AGREE_ROWS`, not here."""
    for row, ops_expected, portfolio_expected in _OPS_DIVERGE_ROWS:
        assert _cli.strategy_name_and_source(row) == ops_expected, row
        assert portfolio._strategy_name_and_source(row) == portfolio_expected, row
        assert ops_expected != portfolio_expected, (
            f"row {row} no longer diverges — move it into _OPS_AGREE_ROWS")


if __name__ == "__main__":
    test_vendored_cli_helpers_match_their_origin()
    test_first_written_vendor_parity()
    test_the_two_skills_answer_the_same_chain_the_same_way()
    test_ops_chain_answers_match_portfolio_where_the_readers_cannot_disagree()
    test_ops_chain_diverges_only_where_pinned_as_intended()
    print("NAME READER PARITY OK")
