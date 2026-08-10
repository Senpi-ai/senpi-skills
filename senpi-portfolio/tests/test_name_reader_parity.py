#!/usr/bin/env python3
"""`_first_written` is vendored byte-identically into senpi-portfolio and senpi-improve-trades (skills
install standalone, so neither may import the other). This test fails the moment the two copies drift —
the two skills disagreeing about what counts as a written name is the divergence the reader exists to
close, and a copy edited in one home only reintroduces it silently.

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

HERE = os.path.dirname(os.path.abspath(__file__))
COPIES = (os.path.join(HERE, "..", "scripts", "portfolio.py"),
          os.path.join(HERE, "..", "..", "senpi-improve-trades", "scripts", "review.py"))

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


def test_both_copies_answer_the_divergence_case_identically():
    """The behavioural half of the parity: an unstripped copy is exactly what drift looks like."""
    ns = [{}, {}]
    for block, env in zip((_vendored_block(p) for p in COPIES), ns):
        exec(compile(block, "<vendored>", "exec"), env)     # noqa: S102 — the block under test
    row = {"strategyName": "   ", "tradingStrategyName": "cub"}
    got = [env["_first_written"](row, "strategyName", "tradingStrategyName") for env in ns]
    assert got == ["cub", "cub"], got


if __name__ == "__main__":
    test_first_written_vendor_parity()
    test_both_copies_answer_the_divergence_case_identically()
    print("NAME READER PARITY OK")
