"""PHALANX — pure thesis math (no I/O, no MCP, no clock).

Proven-cohort rotation scorer. Where a state-based follower reads the 4h gain
leaderboard — a momentum/survivorship-biased read that's a *consequence* of
price moves, not a predictor — Phalanx follows the PROVEN COHORT: traders
with >=$1M lifetime realized PnL (discovery_get_top_traders ALL_TIME). Their
positioning is based on track record, not 4h gains, so it's immune to the
circularity that makes a leaderboard follower's directional calls lag.

The unit of analysis is the BOARD, not one coin. Per tick the scanner
aggregates the cohort's headcount per asset (long_n vs short_n among
positioned traders), and this module scores:

  - one_sidedness: how aligned the cohort is (50% = balanced, 90% = a rout).
    Headcount-based, not notional — price can't change a headcount the way
    it drifts notional bias. Below min_sample -> 0 (too thin to trust).
  - directional_delta: change in net headcount in the signal direction since
    last tick. Conviction must be GROWING (delta >= threshold), not stale.
  - conviction: one_sidedness * multipliers (divergence booster, price
    confirmation), gated by delta and overcrowding checks.
  - margin_pct_for: conviction-tiered margin (1.0 / 1.25 / 1.5 x base).
  - accuracy tracking: rolling hit rate per asset class (crypto vs xyz),
    auto-adjusting the tilt threshold so the strategy learns which crowd
    to trust.

Pure: consumes already-normalized headcount dicts and candle lists; returns
conviction-ranked signals. No I/O, no MCP, no clock — unit-testable.
"""

import math

# ── numeric helpers ───────────────────────────────────────────────────────────

def _f(v, d=0.0):
    """Defensive numeric read: no-op on numbers, casts strings, d on None/garbage."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return d

# ── asset classification ──────────────────────────────────────────────────────

def asset_class_for(asset):
    """Return 'xyz' for HIP-3 assets (equities/commodities/indices), 'crypto' for main-DEX."""
    return "xyz" if str(asset).lower().startswith("xyz:") else "crypto"

def bare_upper(name):
    """Bare, upper-cased asset key: strips any venue prefix ('xyz:NVDA' -> 'NVDA')."""
    s = str(name)
    return (s[4:] if s.lower().startswith("xyz:") else s).upper()

def venue_asset(token, dex):
    """Re-attach xyz: prefix for the venue. Bare token + dex='xyz' -> 'xyz:TOKEN'.
    Already-prefixed tokens are rebuilt with lowercase 'xyz:' (canonical)."""
    t = str(token)
    if t.lower().startswith("xyz:"):
        return "xyz:" + t[4:]
    return f"xyz:{t}" if str(dex).lower() == "xyz" else t

# ── headcount tilt + one-sidedness ────────────────────────────────────────────

def net_tilt(long_n, short_n):
    """Headcount-based net tilt.

    long_ratio = long_n / (long_n + short_n) * 100  (50 = balanced)
    net_tilt   = long_ratio - 50  (+ = cohort net long, - = net short)

    Unlike notional bias (net/gross), headcount is a decision price can't
    fake: a wallet either holds the position or it doesn't, regardless of
    mark-to-market drift.
    """
    total = _f(long_n) + _f(short_n)
    if total <= 0:
        return 50.0, 0.0
    long_ratio = (_f(long_n) / total) * 100.0
    return long_ratio, long_ratio - 50.0

def one_sidedness(long_n, short_n, min_sample=10):
    """How one-sided is the positioned split? Returns the larger side's share (0-100).

    13 long vs 7 short  -> 65.0  (one-sided enough to act)
    43 short vs 4 long  -> 91.5  (a rout)
    429 long vs 380 short -> 53.0 (noise — barely leans)
    4 long vs 1 short   -> 0.0  (below min_sample, too thin to trust)

    This is THE key conviction metric. Signals require one_sidedness >= the
    tilt threshold (default 65), so a 53% lean never produces a trade.
    """
    total = _f(long_n) + _f(short_n)
    if total < min_sample:
        return 0.0
    long_ratio = (_f(long_n) / total) * 100.0
    return max(long_ratio, 100.0 - long_ratio)

def directional_delta(prev_long_n, prev_short_n, curr_long_n, curr_short_n, direction):
    """Change in net headcount in the signal direction since last tick.

    For LONG:  delta = (curr_long - curr_short) - (prev_long - prev_short)
    For SHORT: delta = (prev_long - prev_short) - (curr_long - curr_short)

    Positive = conviction growing in the signal direction.
    Negative = conviction fading (traders closing or flipping).
    Zero = stale (no change — don't enter, the move is already priced in).
    """
    prev_net = _f(prev_long_n) - _f(prev_short_n)
    curr_net = _f(curr_long_n) - _f(curr_short_n)
    if direction == "LONG":
        return curr_net - prev_net
    else:  # SHORT
        return prev_net - curr_net

# ── conviction scoring ────────────────────────────────────────────────────────

def price_conviction_mult(trend_label, direction):
    """How much does the 4h price trend support the signal direction?

    BULLISH trend + LONG  -> 1.2 (confirmed — ride it)
    BEARISH trend + SHORT -> 1.2 (confirmed — ride it)
    BULLISH trend + SHORT -> 0.7 (fighting price — risky)
    BEARISH trend + LONG  -> 0.7 (fighting price — risky)
    NEUTRAL               -> 1.0 (no confirmation, no contradiction)
    """
    if direction == "LONG":
        if trend_label == "BULLISH":
            return 1.2
        if trend_label == "BEARISH":
            return 0.7
    else:  # SHORT
        if trend_label == "BEARISH":
            return 1.2
        if trend_label == "BULLISH":
            return 0.7
    return 1.0

def is_divergent(cohort_long_ratio, crowd_long_ratio):
    """True if the proven cohort and the 4h leaderboard disagree on direction.

    cohort_long_ratio: proven cohort's long% for the asset (headcount-based)
    crowd_long_ratio: 4h leaderboard's long% for the asset (gain-weighted)

    Divergence: cohort net LONG (>50) while crowd net SHORT (<=50), or vice versa.
    This is where the alpha lives — the proven cohort has historically been
    right when the crowd disagrees.
    """
    cohort_side = "LONG" if cohort_long_ratio > 50 else "SHORT"
    crowd_side = "LONG" if crowd_long_ratio > 50 else "SHORT"
    return cohort_side != crowd_side

def margin_pct_for(conviction, base_pct, max_pct=None):
    """Conviction-scaled margin PERCENT. Stronger conviction -> bigger size.

    >= 80 conviction -> base * 1.5
    >= 65 conviction -> base * 1.25
    else             -> base

    Returns a PERCENT in (0,100]; clamped to max_pct when supplied.
    """
    if conviction >= 80:
        pct = base_pct * 1.5
    elif conviction >= 65:
        pct = base_pct * 1.25
    else:
        pct = base_pct
    if max_pct is not None and max_pct > 0:
        pct = min(pct, max_pct)
    return pct

# ── trend structure (4h price confirmation) ───────────────────────────────────

def trend_structure(candles, lookback=6):
    """Determine the 4h trend: BULLISH (higher lows) or BEARISH (lower highs).

    Uses the last `lookback` candles. Higher lows in >= lookback-2 of them
    -> BULLISH. Lower highs in >= lookback-2 -> BEARISH. Otherwise NEUTRAL.

    Returns (label, strength) where strength is the fraction of matching bars.
    """
    if not candles or len(candles) < lookback:
        return "NEUTRAL", 0.0

    def _low(c):
        if isinstance(c, dict):
            return _f(c.get("l", c.get("low", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[3])
        return 0.0

    def _high(c):
        if isinstance(c, dict):
            return _f(c.get("h", c.get("high", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[2])
        return 0.0

    recent = candles[-lookback:]
    lows = [_low(c) for c in recent]
    highs = [_high(c) for c in recent]

    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])

    denom = max(1, lookback - 1)
    if higher_lows >= lookback - 2:
        return "BULLISH", higher_lows / denom
    if lower_highs >= lookback - 2:
        return "BEARISH", lower_highs / denom
    return "NEUTRAL", 0.0

# ── breakout check (overcrowding gate) ─────────────────────────────────────────

def breakout_check(candles_1h, direction, lookback=12):
    """Check if the latest close makes a new lookback-period high (LONG) or low (SHORT).

    Used as the overcrowding gate: when one_sidedness >= 85%, the trade is
    crowded. Require a breakout (new 12h high for longs, new 12h low for shorts)
    before entering — no breakout means the move is exhausted, not accelerating.
    """
    if not candles_1h or len(candles_1h) < lookback:
        return False

    def _close(c):
        if isinstance(c, dict):
            return _f(c.get("c", c.get("close", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[4])
        return 0.0

    def _high(c):
        if isinstance(c, dict):
            return _f(c.get("h", c.get("high", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[2])
        return 0.0

    def _low(c):
        if isinstance(c, dict):
            return _f(c.get("l", c.get("low", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[3])
        return 0.0

    recent = candles_1h[-lookback:]
    last_close = _close(recent[-1])
    if last_close <= 0:
        return False

    prior = recent[:-1]
    if direction == "LONG":
        prior_highs = [_high(c) for c in prior]
        return last_close >= max(prior_highs) if prior_highs else False
    else:  # SHORT
        prior_lows = [_low(c) for c in prior]
        return last_close <= min(prior_lows) if prior_lows else False

# ── accuracy tracking (per-class hit rate, auto-threshold tuning) ─────────────

EVAL_DELAY_S = 3600  # 1h to evaluate a signal's direction
MIN_EVAL_SAMPLES = 5  # need at least 5 evaluated signals before adjusting
RECENT_WINDOW = 10  # rolling window of evaluated signals per class
HIT_RATE_LOW = 0.40  # below this -> raise threshold by 4
HIT_RATE_HIGH = 0.55  # above this -> lower threshold by 2
THRESH_ADJ_UP = 4.0
THRESH_ADJ_DOWN = -2.0
THRESH_FLOOR = 52.0  # never lower the tilt threshold below this

def new_accuracy_state():
    """Fresh accuracy tracker."""
    return {
        "crypto": {"pending": [], "recent": [], "threshold_adj": 0.0},
        "xyz": {"pending": [], "recent": [], "threshold_adj": 0.0},
    }

def add_pending_signal(accuracy_state, asset_class, asset, direction, entry_price, ts):
    """Record a freshly-emitted signal for later evaluation."""
    cls = accuracy_state.get(asset_class)
    if cls is None:
        return accuracy_state
    cls["pending"].append({
        "asset": asset, "direction": direction,
        "entry_price": entry_price, "ts": ts,
    })
    return accuracy_state

def update_accuracy(accuracy_state, asset_class, now, price_lookup):
    """Evaluate pending signals whose eval delay has elapsed.

    price_lookup: function(asset) -> current price (or 0.0 if unreadable).
    Moves evaluated signals from pending -> recent, trims recent to
    RECENT_WINDOW, and recomputes threshold_adj.
    """
    cls = accuracy_state.get(asset_class)
    if cls is None:
        return accuracy_state

    still_pending = []
    for sig in cls.get("pending", []):
        if now - sig["ts"] < EVAL_DELAY_S:
            still_pending.append(sig)
            continue
        entry = _f(sig.get("entry_price", 0))
        curr = _f(price_lookup(sig["asset"]))
        if entry <= 0 or curr <= 0:
            still_pending.append(sig)  # can't evaluate, keep trying
            continue
        if sig["direction"] == "LONG":
            correct = curr > entry
        else:
            correct = curr < entry
        cls["recent"].append({"correct": correct, "ts": now})

    cls["pending"] = still_pending
    cls["recent"] = cls["recent"][-RECENT_WINDOW:]

    if len(cls["recent"]) >= MIN_EVAL_SAMPLES:
        hit_rate = sum(1 for r in cls["recent"] if r["correct"]) / len(cls["recent"])
        if hit_rate < HIT_RATE_LOW:
            cls["threshold_adj"] = THRESH_ADJ_UP
        elif hit_rate > HIT_RATE_HIGH:
            cls["threshold_adj"] = THRESH_ADJ_DOWN
        else:
            cls["threshold_adj"] = 0.0
    else:
        cls["threshold_adj"] = 0.0

    return accuracy_state

def adjusted_threshold(base_threshold, asset_class, accuracy_state):
    """The tilt threshold for this asset class, adjusted by hit-rate learning."""
    adj = 0.0
    cls = accuracy_state.get(asset_class)
    if cls is not None:
        adj = _f(cls.get("threshold_adj", 0.0))
    return max(THRESH_FLOOR, base_threshold + adj)

# ── signal ranking ────────────────────────────────────────────────────────────

def rank_signals(candidates):
    """Sort candidates by conviction descending. Pure."""
    return sorted(candidates, key=lambda c: _f(c.get("conviction", 0)), reverse=True)
