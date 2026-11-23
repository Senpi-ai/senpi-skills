"""A quiet tick has to say WHICH gate was quiet.

Phalanx drops an asset at one of three gates — one_sidedness, the tick-over-tick delta, and the
overcrowding breakout check — and the WAITING line reported only the final candidate count. So
`long_cands=0 short_cands=0` could not distinguish a cohort with no view from a gate that never
passes anything.

That mattered: on a live book the line read exactly that for **359 consecutive ticks over 30 hours**
(cohort=100, assets=224) and there was no way to tell which gate to look at without editing the
scanner. `max_delta` is the decisive one — if the largest delta among assets that cleared gate 1
never approaches `delta_min`, the delta window is too short for the tick interval rather than the
cohort being undecided.

Run: python3 -m pytest strategies/phalanx/tests/test_gate_attribution.py -q
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "main", "scanners", "scan.py")
LEG = os.path.join(HERE, "..", "..", "athena", "phalanx", "scanners", "scan.py")


def _src(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# athena/phalanx runs this engine plus two gates of its own (athena/tests/test_legs_verbatim.py
# declares them); the attribution every phalanx gate reports has to be there too.
BOTH = pytest.mark.parametrize("path", [SRC, LEG], ids=["phalanx", "athena-phalanx"])


@BOTH
def test_every_gate_increments_its_own_counter(path):
    body = _src(path)
    for gate in ("tilt", "delta", "breakout"):
        assert f'blocked["{gate}"] += 1' in body, f"gate {gate} does not report itself"


@BOTH
def test_the_waiting_line_carries_the_attribution(path):
    body = _src(path)
    waiting = body[body.index('WAITING — no emit'):]
    waiting = waiting[:waiting.index('file=sys.stderr')]
    for needle in ("blocked:", "tilt=", "delta=", "breakout=", "max_delta="):
        assert needle in waiting, f"WAITING line lost {needle!r}"


@BOTH
def test_max_delta_is_measured_after_gate_one_not_before(path):
    """It is only meaningful for assets that actually cleared one_sidedness — a max taken over
    every asset would be dominated by names the strategy would never trade."""
    body = _src(path)
    gate1 = body.index('blocked["tilt"] += 1')
    best = body.index("best_delta = delta if best_delta is None else max(best_delta, delta)")
    gate2 = body.index('blocked["delta"] += 1')
    assert gate1 < best < gate2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        for path in (SRC, LEG):
            fn(path); print(f"ok  {fn.__name__}  {os.path.relpath(path, HERE)}")
    print(f"\nALL {len(fns)} PHALANX GATE TESTS PASS")
