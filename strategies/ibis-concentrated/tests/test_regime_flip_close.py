"""The regime-flip close scanner — the only code in this package that is not the parent's.

It can CLOSE a position, so it is tested on behaviour, not wiring (wiring is in
test_ibis_concentrated.py). Four properties matter, and each one is a way this could be
actively harmful rather than merely wrong:

  1. It closes a position the CURRENT regime contradicts — the thing it exists to do.
  2. It does NOT close on an UNCLEAR regime. Efficiency drifts through the no-trade band
     constantly; treating that as a verdict would flatten the book on noise.
  3. It does NOT close on one tick. A borderline ER oscillating across trendErThreshold
     would otherwise churn the position and pay a round trip of fees every time.
  4. An unreadable book or unreadable candles close NOTHING. "Unknown" must never collapse
     to "flipped" — that is the silent-zero shape that keeps biting this repo, and here it
     would exit a live position for no reason.

Candles are built to classify a specific way and the classification is ASSERTED as a
precondition, so a scoring retune surfaces as a failing precondition rather than a test that
silently stops testing anything.

Run: python3 -m pytest strategies/ibis-concentrated/tests/test_regime_flip_close.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCANNERS = os.path.abspath(os.path.join(HERE, "..", "main", "scanners"))
if SCANNERS not in sys.path:
    sys.path.insert(0, SCANNERS)

import close_regime_flip as flip  # noqa: E402
import scoring  # noqa: E402

INPUTS = {"erLookback": 20, "trendErThreshold": 0.36, "rangeErThreshold": 0.20,
          "rangeBars": 24, "rangeBuyBelowPct": 0.22, "rangeSellAbovePct": 0.78,
          "extremeFundingApr": 35.0, "flipConfirmTicks": 2}


# ── candle builders (values are STRINGS, as the API sends them) ───────────────────────────

def _bar(c, spread=0.004):
    return {"o": str(c), "h": str(c * (1 + spread)), "l": str(c * (1 - spread)),
            "c": str(c), "v": "1000"}


def _trending(n=30, start=100.0, step=0.9):
    """A clean one-way march: high ER, higher highs."""
    return [_bar(start + i * step) for i in range(n)]


def _falling(n=30, start=130.0, step=-0.9):
    return [_bar(start + i * step) for i in range(n)]


def _ranging(n=30, lo=100.0, hi=110.0, end="low"):
    """A saw-tooth between lo and hi: low ER. `end` places the last close at an edge."""
    bars = []
    for i in range(n - 1):
        bars.append(_bar(lo if i % 2 == 0 else hi))
    bars.append(_bar(lo if end == "low" else hi))
    return bars


def _unclear():
    """Drift plus chop, tuned until ER lands strictly BETWEEN the two thresholds — the
    no-trade band. Searched over a grid so it is deterministic rather than hand-guessed; the
    assert makes a scoring retune a loud failure instead of a silent skip."""
    lo, hi = INPUTS["rangeErThreshold"], INPUTS["trendErThreshold"]
    for drift in [x / 100 for x in range(5, 120, 5)]:
        for chop in [x / 100 for x in range(20, 400, 10)]:
            c = [_bar(100.0 + i * drift + (chop if i % 2 else -chop)) for i in range(30)]
            regime, er = scoring.classify_regime(c, INPUTS)[:2]
            if regime == "UNCLEAR" and lo < er < hi:
                return c
    raise AssertionError(f"no UNCLEAR fixture found in the band ({lo}, {hi}) — the regime "
                         "thresholds moved and this test needs re-deriving")


# ── preconditions: the fixtures classify the way the tests assume ─────────────────────────

def test_fixtures_classify_as_intended():
    up = scoring.classify_regime(_trending(), INPUTS)
    assert up[0] == "TREND" and up[2] == "UP", f"rising fixture classified {up[:3]}"
    down = scoring.classify_regime(_falling(), INPUTS)
    assert down[0] == "TREND" and down[2] == "DOWN", f"falling fixture classified {down[:3]}"
    rng = scoring.classify_regime(_ranging(), INPUTS)
    assert rng[0] == "RANGE", f"saw-tooth fixture classified {rng[:3]}"


# ── (1) it closes a contradicted position ────────────────────────────────────────────────

def test_a_long_in_a_now_downtrending_market_is_a_contradiction():
    bad, reason = flip._contradiction({"direction": "LONG"}, _falling(), 0.0, INPUTS)
    assert bad is True
    assert "flipped" in reason and "DOWN" in reason


def test_a_short_in_a_now_uptrending_market_is_a_contradiction():
    bad, reason = flip._contradiction({"direction": "SHORT"}, _trending(), 0.0, INPUTS)
    assert bad is True


def test_a_long_in_a_still_uptrending_market_is_held():
    bad, reason = flip._contradiction({"direction": "LONG"}, _trending(), 0.0, INPUTS)
    assert bad is False
    assert "holding" in reason


# ── the range leg ─────────────────────────────────────────────────────────────────────────

def test_a_long_fade_now_at_the_range_high_is_a_contradiction():
    bad, reason = flip._contradiction({"direction": "LONG"}, _ranging(end="high"), 0.0, INPUTS)
    assert bad is True
    assert "range HIGH" in reason


def test_a_long_fade_still_at_the_range_low_is_held():
    bad, _ = flip._contradiction({"direction": "LONG"}, _ranging(end="low"), 0.0, INPUTS)
    assert bad is False


def test_extreme_funding_closes_a_fade_because_the_range_is_breaking():
    bad, reason = flip._contradiction({"direction": "LONG"}, _ranging(end="low"), 48.0, INPUTS)
    assert bad is True
    assert "extreme" in reason


def test_unreadable_funding_does_not_close_a_fade():
    """None must mean unknown. Treating it as 0.0 would read as 'calm' — the opposite error,
    but reading it as extreme would close a healthy position for a failed HTTP call."""
    bad, _ = flip._contradiction({"direction": "LONG"}, _ranging(end="low"), None, INPUTS)
    assert bad is False


# ── (2) UNCLEAR is not a verdict ─────────────────────────────────────────────────────────

def test_an_unclear_regime_never_contradicts_an_open_position():
    """ER between the two thresholds. The no-trade band gates ENTRIES; an open position that
    drifts into it is not thereby wrong."""
    candles = _unclear()
    for direction in ("LONG", "SHORT"):
        bad, reason = flip._contradiction({"direction": direction}, candles, 0.0, INPUTS)
        assert bad is False, f"an UNCLEAR regime closed a {direction}: {reason}"
        assert "no verdict" in reason


# ── (3) hysteresis, and (4) failure is never a close: via scan() ──────────────────────────

class _State:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def __len__(self):
        return len(self.rows)

    def last(self):
        return self.rows[-1] if self.rows else None

    def append(self, row):
        self.rows.append(row)


class _MCP:
    """Minimal fake: a book, candles per coin, and an instruments row for funding."""

    def __init__(self, positions, candles, funding_hourly="0.0", fail=()):
        self.positions, self.candles = positions, candles
        self.funding_hourly, self.fail = funding_hourly, set(fail)

    def call_tool(self, tool, args=None):
        if tool in self.fail:
            raise RuntimeError(f"simulated {tool} failure")
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": [
                {"position": {"coin": c, "szi": ("1" if d == "LONG" else "-1"),
                              "marginUsed": "100", "unrealizedPnl": "-5"}}
                for c, d in self.positions]}, "xyz": {}}}
        if tool == "market_get_asset_data":
            return {"data": {"candles": {"4h": self.candles}}}
        if tool == "market_list_instruments":
            return {"data": [{"name": c, "context": {"funding": self.funding_hourly}}
                             for c, _ in self.positions]}
        raise AssertionError(f"unexpected tool {tool}")


class _Ctx:
    def __init__(self, mcp, state=None, wallet="0xtest"):
        self.senpi_mcp, self.state, self.wallet = mcp, state, wallet


def test_the_first_contradicting_tick_does_not_close():
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling()), _State())
    out = flip.scan(INPUTS, ctx)
    assert out == [], "closed on the first contradicting tick — no hysteresis"
    assert ctx.state.last()["streaks"]["SOL"] == 1


def test_the_second_consecutive_contradicting_tick_closes():
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling()), _State([{"streaks": {"SOL": 1}}]))
    out = flip.scan(INPUTS, ctx)
    assert len(out) == 1
    assert out[0]["asset"] == "SOL" and out[0]["direction"] == "LONG"
    assert out[0]["data"]["confirmations"] == 2
    assert "regime invalidation" in out[0]["data"]["reason"]


def test_a_recovering_tick_resets_the_streak():
    ctx = _Ctx(_MCP([("SOL", "LONG")], _trending()), _State([{"streaks": {"SOL": 1}}]))
    assert flip.scan(INPUTS, ctx) == []
    assert ctx.state.last()["streaks"]["SOL"] == 0, (
        "a non-contradicting tick must reset the count, or two unrelated flips a day apart "
        "would add up to a close")


def test_an_unreadable_book_closes_nothing_and_does_not_discard_the_streaks():
    """Two separate things, and the second is easy to lose. `_open_positions` returns None for
    "unreadable" and [] for "genuinely flat", and only the [] path may clear the streaks. If a
    failed read fell through to the flat-book path it would reset every count, so a flaky
    clearinghouse could postpone a real flip indefinitely while looking perfectly healthy."""
    state = _State([{"streaks": {"SOL": 1}}])
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling(), fail=("strategy_get_clearinghouse_state",)),
               state)
    assert flip.scan(INPUTS, ctx) == [], "an unknown book must never be read as a flipped one"
    assert state.last()["streaks"] == {"SOL": 1}, (
        "a failed read cleared the confirmation streaks — unknown is not flat")


def test_unreadable_candles_close_nothing_and_keep_the_streak():
    """Carried, not reset: a flaky candle read should not indefinitely postpone a real flip,
    but it must not advance one either."""
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling(), fail=("market_get_asset_data",)),
               _State([{"streaks": {"SOL": 1}}]))
    assert flip.scan(INPUTS, ctx) == []
    assert ctx.state.last()["streaks"]["SOL"] == 1


def test_an_empty_book_is_not_an_error_and_clears_the_streaks():
    ctx = _Ctx(_MCP([], _falling()), _State([{"streaks": {"SOL": 9}}]))
    assert flip.scan(INPUTS, ctx) == []
    assert ctx.state.last()["streaks"] == {}


def test_it_survives_having_no_state_at_all():
    """ctx.state is None on some runtime paths; a crash here would take out the tick."""
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling()), None)
    out = flip.scan(INPUTS, ctx)
    assert out == [], "with no state there is no streak to confirm, so nothing may close"


def test_confirm_ticks_of_one_closes_immediately_when_configured():
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling()), _State())
    out = flip.scan({**INPUTS, "flipConfirmTicks": 1}, ctx)
    assert len(out) == 1 and out[0]["data"]["confirmations"] == 1


def test_a_zero_or_negative_confirm_tick_config_cannot_disable_confirmation():
    """max(1, ...) — a config typo must not turn the scanner into a one-tick hair trigger it
    was never meant to be, and must not divide by zero either."""
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling()), _State())
    out = flip.scan({**INPUTS, "flipConfirmTicks": 0}, ctx)
    assert len(out) == 1, "floors to 1 rather than erroring"


def test_the_emitted_signal_matches_the_declared_schema():
    """The runtime DROPS a candidate whose data violates signal_data_schema, silently. The
    declared keys are roe/confirmations/reason, all non-null."""
    import yaml
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    declared = next(s for s in rt["scanners"]
                    if s.get("entrypoint") == "close_regime_flip.py")["signal_data_schema"]
    ctx = _Ctx(_MCP([("SOL", "LONG")], _falling()), _State([{"streaks": {"SOL": 1}}]))
    sig = flip.scan(INPUTS, ctx)[0]
    assert set(sig) == {"asset", "direction", "data"}
    assert set(sig["data"]) == set(declared), (
        f"emitted {sorted(sig['data'])} but the schema declares {sorted(declared)}")
    for k, v in sig["data"].items():
        assert v is not None, f"{k} is None — the runtime rejects a null even when optional"
        want = declared[k]["type"]
        assert isinstance(v, (int, float)) if want == "number" else isinstance(v, str), (
            f"{k} is {type(v).__name__}, schema says {want}")
