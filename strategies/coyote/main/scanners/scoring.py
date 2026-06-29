"""COYOTE — pure regime-classification math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Coyote producer's regime classifier
(coyote-producer.py v1.0.1 / SKILL.md v1.0.0). Every metric and threshold is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same candle snapshot.

Coyote classifies the market into TREND_UP / TREND_DOWN / CHOP from three pure
metrics over BTC (plus a cross-asset dispersion metric that is INFORMATIONAL
ONLY — published, never gating, per SKILL RULE 3):

  - btc_7d_pct        : % change of BTC close vs 42 4h-bars ago (7 days)
  - realized_vol_pct  : annualized realized vol = stdev(log returns) * sqrt(2190) * 100
  - dispersion_pct    : cross-sectional stdev of recent returns across the universe
                        (BTC/ETH/SOL/HYPE) — operator visibility only

Regime rules (verbatim, evaluated in order):
  TREND_UP   : btc_7d >=  trendUpThresholdPct  AND vol <= maxVolForTrendPct
  TREND_DOWN : btc_7d <= -trendDownThresholdPct AND vol >= minVolForCrashPct
  CHOP       : otherwise
  UNKNOWN    : required inputs missing

Direction: TREND_UP -> LONG BTC, TREND_DOWN -> SHORT BTC, else no trade.

All functions are single-pass and unit-testable on plain close-price lists.
`scan.py` does the MCP reads + state; this module does the numbers.
"""

import math


def _f(c, primary="close", alt="c", default=0.0):
    """Pull a float close from a candle dict (dual-shape: {close} OR {c}).
    Mirrors the v2 producer's `_f(c, "close", "c")` accessor used to build the
    close-price series. Returns `default` on any non-numeric value."""
    if isinstance(c, dict):
        val = c.get(primary)
        if val is None and alt:
            val = c.get(alt)
    else:
        val = c
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def closes_from_candles(candles):
    """List of close prices from a list of 4h candle dicts. Verbatim shape of
    the v2 producer's `[_f(c, "close", "c") for c in candles]`."""
    return [_f(c) for c in (candles or [])]


def pct_move(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    Verbatim from v2 producer `pct_move`. None if insufficient data or a
    non-positive reference price."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def realized_vol_pct(closes, lookback, bars_per_year=2190):
    """Annualized realized volatility (stdev of log returns * sqrt(N)) as a
    percent. `bars_per_year` defaults to 2190 (4h bars, 24/7 trading). Verbatim
    from v2 producer `realized_vol_pct`. None if insufficient data."""
    if not closes or len(closes) < lookback + 1:
        return None
    series = closes[-(lookback + 1):]
    log_returns = []
    for prev, curr in zip(series[:-1], series[1:]):
        if prev is None or prev <= 0 or curr is None or curr <= 0:
            continue
        log_returns.append(math.log(curr / prev))
    if len(log_returns) < 2:
        return None
    mean = sum(log_returns) / len(log_returns)
    var = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    stdev = math.sqrt(var)
    return stdev * math.sqrt(bars_per_year) * 100.0


def dispersion_pct(returns_by_asset):
    """Cross-sectional dispersion: stdev of returns across assets.
    `returns_by_asset` is {asset: recent_return_pct}. High = mixed market,
    low = synchronized. Verbatim from v2 producer `dispersion_pct`. None if
    fewer than 2 assets have data. INFORMATIONAL ONLY — never gates."""
    values = [r for r in returns_by_asset.values() if r is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def classify_regime(btc_7d_pct, realized_vol_pct_value,
                    trend_up_threshold, trend_down_threshold,
                    max_vol_for_trend, min_vol_for_crash):
    """Classify the market regime from BTC trend strength + realized vol.
    Returns "TREND_UP" | "TREND_DOWN" | "CHOP" | "UNKNOWN". Verbatim from v2
    producer `classify_regime` (rule order preserved exactly)."""
    if btc_7d_pct is None or realized_vol_pct_value is None:
        return "UNKNOWN"
    if btc_7d_pct >= trend_up_threshold and realized_vol_pct_value <= max_vol_for_trend:
        return "TREND_UP"
    if btc_7d_pct <= -trend_down_threshold and realized_vol_pct_value >= min_vol_for_crash:
        return "TREND_DOWN"
    return "CHOP"


def regime_to_direction(regime):
    """Map a regime classification to an entry direction. None if no trade.
    Verbatim from v2 producer `regime_to_direction`."""
    if regime == "TREND_UP":
        return "LONG"
    if regime == "TREND_DOWN":
        return "SHORT"
    return None
