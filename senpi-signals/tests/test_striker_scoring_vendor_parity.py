"""The vendored striker scorer must stay byte-identical to penguin's.

"Hyperfeed Movers" answers "what's hot right now?" with the SAME arithmetic Penguin trades on —
that is the whole claim of the section, and a reader who asks why a name is hot and then funds
Penguin has to get the same answer twice. A hand-maintained second copy of a detector drifts:
someone retunes penguin's point weights and the read silently starts describing a strategy that no
longer exists.

So the copy is byte-identical and this test is the lock, the same shape as
senpi-strategy-ops/tests/test_min_budget_vendor_parity.py. If you retune penguin's scorer, copy it
over and land both in one PR; if the two must ever diverge, delete this test deliberately and say
in the section that the numbers are no longer Penguin's.

Run:
  python3 senpi-signals/tests/test_striker_scoring_vendor_parity.py
"""
import hashlib
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCE = os.path.join(REPO, "strategies", "penguin", "main", "scanners", "scoring.py")
VENDORED = os.path.join(REPO, "senpi-signals", "scripts", "striker_scoring.py")


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_striker_scorer_is_byte_identical_with_penguin():
    """pytest entry point. The __main__ runner below is kept so CI can also invoke this file
    directly like senpi-strategy-ops/tests/test_min_budget_vendor_parity.py — a file with only a
    main() is COLLECTED by pytest and silently passes, which is how a parity lock quietly stops
    guarding anything."""
    a, b = _sha(SOURCE), _sha(VENDORED)
    assert a == b, (
        "the vendored striker scorer has drifted from penguin's — Hyperfeed Movers would describe a "
        "detector the strategy no longer runs.\n"
        f"  penguin  {a}\n  vendored {b}\n"
        "  Fix: cp strategies/penguin/main/scanners/scoring.py senpi-signals/scripts/striker_scoring.py")


def main():
    a, b = _sha(SOURCE), _sha(VENDORED)
    if a != b:
        print("VENDOR PARITY FAILED — the striker scorer has drifted from penguin's")
        print(f"  penguin  {SOURCE}\n    sha256 {a}")
        print(f"  vendored {VENDORED}\n    sha256 {b}")
        print("\n  Fix: cp strategies/penguin/main/scanners/scoring.py "
              "senpi-signals/scripts/striker_scoring.py")
        return 1
    print(f"VENDOR PARITY OK — striker scorer byte-identical with penguin ({a[:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
