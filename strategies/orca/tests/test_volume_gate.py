"""The volume-confirmation gate must actually evaluate.

2026-09-24: this gate divided by `prevDayNtlVlm`, a field HL's asset context does not contain. The
divisor was always 0, so the ratio branch never ran and the function returned its permissive
`(0, True)` on every call — a dead gate that still printed `VOL_CONFIRMED`. Restored by computing
both days from the 1h candles the same read already returns.

The sibling guard `strategies/tests/test_no_phantom_asset_context_keys.py` stops the *key* coming
back. This file covers what that one cannot: that the arithmetic is right and that a real ratio is
actually returned rather than the fail-open default.

`penguin` and `razorbill` each carry a byte-identical copy of this function;
test_forks_match_orca below pins them together so a fix to one cannot silently skip the others.

Run: python3 -m pytest strategies/orca/tests -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402


def _candles(vols, close="10"):
    """1h candles in HL's wire shape — o/h/l/c/v are STRINGS."""
    return [{"t": i, "T": i, "s": "X", "i": "1h", "o": close, "c": close,
             "h": close, "l": close, "v": str(v), "n": 1} for i, v in enumerate(vols)]


class _MCP:
    def __init__(self, payload):
        self.payload = payload

    def call_tool(self, name, args):
        assert name == "market_get_asset_data", name
        # the fix must not cost an extra read: the candles ride the call the gate already made
        assert args.get("candle_intervals") == ["1h"], args
        return self.payload


class _Ctx:
    """Returns one canned market_get_asset_data payload through the real ctx.senpi_mcp path."""

    def __init__(self, payload):
        self.senpi_mcp = _MCP(payload)
        self.wallet = "0xtest"


def _gate(payload, min_ratio=1.5):
    return scan._check_asset_volume(_Ctx(payload), "X", "", min_ratio)


def _wrap(candles):
    return {"data": {"asset": "X", "candles": {"1h": candles},
                     "asset_context": {"coin": "X", "dayNtlVlm": "1000"}}}


def test_doubling_volume_confirms():
    """Prior 24h at 1.0/candle, current 24h at 2.0 -> ratio 2.0, clears 1.5."""
    ratio, strong = _gate(_wrap(_candles([1.0] * 24 + [2.0] * 24)))
    assert round(ratio, 6) == 2.0, ratio
    assert strong is True


def test_flat_volume_does_not_confirm():
    """The case the dead gate got wrong: a quiet market must NOT confirm."""
    ratio, strong = _gate(_wrap(_candles([1.0] * 48)))
    assert round(ratio, 6) == 1.0, ratio
    assert strong is False, "a flat tape cleared a 1.5x volume gate"


def test_falling_volume_does_not_confirm():
    ratio, strong = _gate(_wrap(_candles([4.0] * 24 + [1.0] * 24)))
    assert round(ratio, 6) == 0.25, ratio
    assert strong is False


def test_ratio_is_notional_not_base_volume():
    """v2 named dayNtlVlm/prevDayNtlVlm — NOTIONAL. Same base volume at double the price is a
    doubling of notional, and must confirm."""
    prior = _candles([1.0] * 24, close="10")
    current = _candles([1.0] * 24, close="20")
    ratio, strong = _gate(_wrap(prior + current))
    assert round(ratio, 6) == 2.0, f"base-volume math would give 1.0 here, got {ratio}"
    assert strong is True


def test_threshold_is_honoured():
    payload = _wrap(_candles([1.0] * 24 + [1.6] * 24))
    assert _gate(payload, min_ratio=1.5)[1] is True
    assert _gate(payload, min_ratio=2.0)[1] is False


def test_short_history_fails_open():
    """Fewer than 48 candles is a read shortfall, not a quiet market. v2's documented behaviour —
    'a missing volume reference never blocks a strong signal' — is preserved for that case."""
    assert _gate(_wrap(_candles([1.0] * 47))) == (0, True)


def test_failed_read_fails_open():
    assert _gate(None) == (0, True)
    assert _gate({"data": {"candles": {}}}) == (0, True)


def test_no_phantom_previous_day_volume_field():
    """The specific regression: HL returns prevDayPx (a price), never prevDayNtlVlm. A payload
    carrying only the real keys must still produce a real ratio."""
    payload = {"data": {"candles": {"1h": _candles([1.0] * 24 + [3.0] * 24)},
                        "asset_context": {"coin": "X", "dayNtlVlm": "1000",
                                          "prevDayPx": "9.5"}}}
    ratio, strong = _gate(payload)
    assert ratio == 3.0 and strong is True, (ratio, strong)


def test_forks_match_orca():
    """Every fork of orca inherited this bug and the fix. Pin the implementations together so a
    later fix to one cannot silently skip the others. `razorbill` is a penguin fork that changes
    only the xyz universe flag, so it carries this function unchanged too."""
    here = os.path.dirname(__file__)
    orca = os.path.join(here, "..", "main", "scanners", "scan.py")

    def _fn(path):
        body = open(path, encoding="utf-8").read()
        start = body.index("def _check_asset_volume(")
        return body[start:body.index("\ndef ", start + 1)]

    for fork in ("penguin", "razorbill"):
        path = os.path.join(here, "..", "..", fork, "main", "scanners", "scan.py")
        if not os.path.exists(path):
            continue  # fork retired or renamed; nothing to pin
        assert _fn(orca) == _fn(path), (
            f"orca and {fork}'s _check_asset_volume have diverged — a fix to one skipped the other"
        )


def test_a_degraded_read_is_not_reported_as_confirmed():
    """The 982 telemetry lines all read `VOL_CONFIRMED 0.0x` — a dead gate wearing the same label
    as a real confirmation, which is why it went unnoticed for so long. After the fix the fail-open
    path still exists (a genuinely failed read), so the LABEL has to carry the difference or the
    same blind spot returns in a rarer form."""
    import re
    here = os.path.dirname(__file__)
    for pkg in ("orca", "penguin", "razorbill"):
        path = os.path.join(here, "..", "..", pkg, "main", "scanners", "scan.py")
        if not os.path.exists(path):
            continue
        body = open(path, encoding="utf-8").read()
        emit = [ln for ln in body.splitlines() if "VOL_CONFIRMED" in ln and "reasons" in ln]
        assert emit, f"{pkg}: no VOL_CONFIRMED emit line found — did the reason string move?"
        joined = "\n".join(emit)
        assert re.search(r"vol_ratio\s*>\s*0", joined), (
            f"{pkg}: VOL_CONFIRMED is emitted without guarding on a non-zero ratio, so a "
            f"fail-open read would print 'VOL_CONFIRMED 0.0x' again:\n{joined}"
        )
