#!/usr/bin/env python3
"""`_first_written` is vendored byte-identically into senpi-portfolio and senpi-improve-trades (skills
install standalone, so neither may import the other). This test fails the moment the two copies drift —
the two skills disagreeing about what counts as a written name is the divergence the reader exists to
close, and a copy edited in one home only reintroduces it silently.

TWO checks, guarding different things: the sha pins the shared HELPER, the chain table pins the ANSWER.
The sha alone can pass while behaviour diverges (a shadowing redefinition after the end marker, or a
reordered chain in one skill), so the second check is the load-bearing one — see its docstring.

senpi-strategy-ops' `_first_written` is deliberately NOT held to this parity: it dispatches through a
case-insensitive `dig()`, takes no `default=`, and does not strip. Converging it would either make ops
case-SENSITIVE (its docstring calls out the backend's case-normalization as load-bearing) or make these
two skills case-INSENSITIVE for every name read — a behaviour change neither bug asked for. The three
readers share a CHAIN, not an implementation; only these two share bytes.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COPIES = (os.path.join(HERE, "..", "scripts", "portfolio.py"),
          os.path.join(HERE, "..", "..", "senpi-improve-trades", "scripts", "review.py"))

for _d in (os.path.join(HERE, "..", "scripts"),
           os.path.join(HERE, "..", "..", "senpi-improve-trades", "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import portfolio  # noqa: E402
import review  # noqa: E402

_BLOCK = re.compile(r"^# ── VENDORED,.*?^# ── end vendored block$", re.S | re.M)


def _vendored_block(path):
    with open(path, encoding="utf-8") as f:
        found = _BLOCK.search(f.read())
    assert found, f"vendored `_first_written` block not found in {path}"
    return found.group(0)


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


if __name__ == "__main__":
    test_first_written_vendor_parity()
    test_the_two_skills_answer_the_same_chain_the_same_way()
    print("NAME READER PARITY OK")
