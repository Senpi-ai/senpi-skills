#!/usr/bin/env python3
"""`blacklist.py` is vendored from quant-desk into strategies/shadow's scanners.

It is the client for Senpi's market-maker blacklist (GetDiscoveryBlacklist), and the thing that
makes it safe is subtle: the table also holds probe/test rows, so ONLY wallets whose reason is
MARKET MAKER reach `flagged`. A drifted copy that widened that match would start refusing real
traders — a false accusation — and a copy that narrowed it would quietly mirror quote machines.
Both directions are silent, so the copies are pinned byte-for-byte."""
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # strategies/shadow/tests -> root
COPIES = [
    os.path.join(REPO, "quant-desk", "scripts", "blacklist.py"),
    os.path.abspath(os.path.join(HERE, "..", "main", "scanners", "blacklist.py")),
]


def _sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_the_blacklist_client_copies_do_not_drift():
    for p in COPIES:
        assert os.path.exists(p), f"missing vendored copy: {p}"
    digests = {_sha(p) for p in COPIES}
    assert len(digests) == 1, (
        "blacklist.py DRIFTED between quant-desk and strategies/shadow — re-vendor it "
        "byte-identically. The reason-matching rule is what stops a false refusal.")


def test_the_market_maker_reason_match_is_still_strict():
    """Guard the actual rule, not just the bytes."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_bl_under_test", COPIES[0])
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    assert bl.is_market_maker_reason("MARKET_MAKER") is True
    assert bl.is_market_maker_reason("market maker") is True
    assert bl.is_market_maker_reason(" Market Maker ") is True
    for not_mm in ("PROBE", "TEST", "UNAUTH-WRITE-POC-BENIGN", "", None, "MARKET"):
        assert bl.is_market_maker_reason(not_mm) is False, f"{not_mm!r} matched as a market maker"


if __name__ == "__main__":
    test_the_blacklist_client_copies_do_not_drift()
    test_the_market_maker_reason_match_is_still_strict()
    print("BLACKLIST VENDOR PARITY OK")
