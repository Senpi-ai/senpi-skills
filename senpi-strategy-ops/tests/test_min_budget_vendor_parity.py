#!/usr/bin/env python3
"""min_budget.py is vendored byte-identically into senpi-trading-runtime (gen_catalog) and
senpi-strategy-ops (deploy). This test fails CI the moment the two copies drift — a silent
skew between "what the card promised" and "what deploy enforces" is the failure it guards."""
import hashlib, os

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "..", "scripts", "min_budget.py")
B = os.path.join(HERE, "..", "..", "senpi-trading-runtime", "scripts", "min_budget.py")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_vendor_parity():
    assert os.path.exists(A) and os.path.exists(B), "min_budget.py missing in one home"
    assert _sha(A) == _sha(B), "min_budget.py DRIFTED between the two skills — re-vendor byte-identically"


if __name__ == "__main__":
    test_vendor_parity()
    print("VENDOR PARITY OK")
