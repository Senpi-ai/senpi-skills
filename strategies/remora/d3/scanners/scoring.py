"""REMORA — pure thesis math (no I/O, no MCP, no clock).

Benchmark-laggard relative-performance reversion. One traded asset (default HYPE)
against one benchmark (default BTC, READ but never traded). Each instance watches ONE
lookback (3d / 7d / 30d as 4h bars): when the benchmark has outperformed the asset by
at least `min_gap_pct` over the lookback, the asset is a laggard → BUY expecting
catch-up. Templated from a user-authored strategy ("HYPE always catches back up to
BTC on the 3D/7D/30D pattern").

Pure + unit-testable: candle lists in, decision out. The caller (scan.py) owns all
I/O. Candle coercion (`_f`) and the %-change-over-lookback-bars semantics follow the
iguana engine verbatim so the math is fleet-consistent.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


# ── value coercion (verbatim from iguana / v2 _f: primary/alt key fallback) ──

def _f(c, primary, alt=None, default=0.0):
    """Coerce c[primary] (or c[alt]) to float, default on missing/bad. Candle dicts
    come in the dual shape {close|c} / {volume|v}."""
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def closes(candles):
    """Close series from a candle list (dual-shape keys), skipping malformed rows."""
    return [_f(c, "close", "c") for c in candles if isinstance(c, dict)]


def pct_change(close_series, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive.
    (Same semantics as iguana's trend_strength — a slow drift read, not structure.)"""
    if not close_series or len(close_series) <= lookback:
        return None
    ref = close_series[-(lookback + 1)]
    latest = close_series[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def relative_gap(asset_closes, bench_closes, lookback):
    """The RELATIVE performance read: asset % move, benchmark % move, and the gap
    (asset - bench) over the same lookback. None if either side lacks data — an
    unreadable benchmark must never masquerade as a zero gap."""
    a = pct_change(asset_closes, lookback)
    b = pct_change(bench_closes, lookback)
    if a is None or b is None:
        return None
    return {"asset_pct": round(a, 4), "bench_pct": round(b, 4),
            "gap_pct": round(a - b, 4)}


def entry_signal(rel, min_gap_pct):
    """LONG the asset when it UNDERPERFORMS the benchmark by >= min_gap_pct over the
    lookback (gap_pct <= -min_gap_pct). The floor is the noise gate — 'any tick of
    divergence' flickers; a real lag persists. None when the gap doesn't qualify."""
    if not isinstance(rel, dict) or rel.get("gap_pct") is None:
        return None
    if rel["gap_pct"] <= -abs(float(min_gap_pct)):
        return {"direction": "LONG", **rel}
    return None
