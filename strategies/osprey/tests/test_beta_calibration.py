"""Osprey's betas were guesses, and the guess ran in the direction that invents trades.

The scanner decides what a proxy still "owes" the leader as
`gap = leader_move x beta - proxy_move`, so beta is a multiplier on the leader's move.
Carrying COIN at 1.8 against a realized 1.28, and MSTR at 2.5 against 1.73, does not make
the scanner cautious — it reports a catch-up gap for a proxy that has already fully kept
up. On a 4% BTC move that phantom gap is ~2-3%, which clears minGapPct (2.0) by itself.

Betas here are measured: cov(proxy, BTC)/var(BTC) over 720 hourly HL bars (30d) to
2026-09-18. Run: python3 -m pytest strategies/osprey/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan     # noqa: E402
import scoring  # noqa: E402

LOOKBACK = 4
INPUTS = {"moveLookbackBars": LOOKBACK, "minLeaderMovePct": 2.0, "minGapPct": 2.0,
          "strongGapPct": 5.0, "smTiltMinPct": 55, "smStrongTiltPct": 70}
MEASURED = {"xyz:MSTR": 1.73, "xyz:COIN": 1.28}
SUPERSEDED = {"xyz:MSTR": 2.5, "xyz:COIN": 1.8}


def _closes(move_pct):
    """A close series whose move over LOOKBACK bars is exactly `move_pct`."""
    return [100.0] * LOOKBACK + [100.0 * (1 + move_pct / 100.0)]


def _thesis(proxy, beta, leader_move, proxy_move):
    return scoring.build_thesis({"proxy": proxy, "beta": beta}, leader_move,
                                _closes(proxy_move), [], (None, 0.0), INPUTS)


def test_a_proxy_that_moved_its_full_beta_owes_nothing():
    """The whole premise: keep up with the leader and there is no catch-up to trade."""
    for proxy, beta in MEASURED.items():
        for leader in (4.0, -4.0, 6.5):
            kept_up = leader * beta
            assert _thesis(proxy, beta, leader, kept_up) is None, proxy
            # and it is not that nothing ever fires — a proxy that sat still does owe
            assert _thesis(proxy, beta, leader, 0.0) is not None, proxy


def test_the_superseded_betas_invent_a_gap_on_that_same_tape():
    """Mutation proof: revert either beta and this exact tape starts firing."""
    for proxy, beta in MEASURED.items():
        kept_up = 4.0 * beta
        th = _thesis(proxy, SUPERSEDED[proxy], 4.0, kept_up)
        assert th is not None and th["direction"] == "LONG", proxy
        assert th["gap_pct"] >= INPUTS["minGapPct"], proxy


def test_runtime_yaml_is_what_actually_ships_and_matches_the_fallback():
    """inputs.get("proxies", _DEFAULT_PROXIES) means runtime.yaml wins: a beta fixed
    only in scan.py would never reach a deployed wallet. Keep the two in lockstep."""
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    scanner = next(s for s in rt["scanners"] if s.get("inputs", {}).get("proxies"))
    live = {p["proxy"]: float(p["beta"]) for p in scanner["inputs"]["proxies"]}
    assert live == MEASURED
    assert {p["proxy"]: float(p["beta"]) for p in scan._DEFAULT_PROXIES} == live


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} OSPREY TESTS PASS")
