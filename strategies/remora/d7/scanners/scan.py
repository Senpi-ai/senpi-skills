"""REMORA — supervised scanner (one instance = one lookback tranche).

Benchmark-laggard reversion: LONG the traded asset (default HYPE) when the benchmark
(default BTC, READ but never traded) has outperformed it by >= minGapPct over THIS
instance's lookback (lookbackBars of 4h candles: d3=18, d7=42, d30=180). Per tick:
  - read account state + held positions (dual-DEX equity via max(), never sum();
    read-sanity guard for the funding/$0 corrupt-clearinghouse glitch — verbatim
    from the iguana engine),
  - if the asset is already held → emit nothing (the DSL owns the exit; one tranche
    per wallet by design),
  - skip if recently signaled (race-window dedup via ctx.state),
  - fetch 4h candles for asset + benchmark, compute the relative gap (pure
    scoring.relative_gap), and emit ONE LONG signal when the lag qualifies.

Read-only + single-pass. Emits a flat `marginPct` intent (PERCENT of this instance's
wallet — the conviction ladder is 15/30/60 across d3/d7/d30) plus a clamped
`leverage`; the runtime sizes the dollars and owns cooldowns / daily caps / drawdown
halt / the mean-reversion DSL exit. No daemon, no push_signal, no create_position.

EXIT NOTE (honest scope): the source thesis exits "when the asset outperforms the
benchmark on the entry lookback." The runtime's CLOSE_POSITION action has no live
fleet precedent, so this template expresses the exit via the mean_reversion DSL
ladder — which locks the snapback progressively as the gap closes (ROE rises) and
time-cuts an unresolved fade at 48h. Relative-exit via a scanner-driven close is a
flagged follow-up once the runtime close-signal contract is verified.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DEFAULT_ASSET = "HYPE"
_DEFAULT_BENCHMARK = "BTC"
_DEFAULT_LOOKBACK_BARS = 18       # 3 days x 6 4h-bars/day (d7=42, d30=180 via inputs)
_DEFAULT_MIN_GAP_PCT = 0.5        # noise floor: benchmark must lead by at least this
_DEFAULT_MARGIN_PCT = 15          # PERCENT of THIS instance's withdrawable (d7=30, d30=60)
_DEFAULT_LEVERAGE = 7             # the source spec's 7x
_DEFAULT_MAX_LEVERAGE = 10        # HYPE max on Hyperliquid
_DEFAULT_TTL = 3000               # race-window dedup, just under the hourly cadence


def _dex_for(asset, inputs):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''. Honor an
    explicit inputs.dex if set, else derive from the xyz: prefix."""
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [coin,...]) from strategy_get_clearinghouse_state.

    READ-GUARDED — a read error must never roll back the whole tick. Dual-DEX equity
    collapse: account_value via max() across main/xyz (two views of ONE cross-margined
    wallet — summing double-counts the shared free balance -> 2x sizing).
    assetPositions are per-sub-DEX, enumerated across both sections. Ported VERBATIM
    from the iguana engine, including the read-sanity guard (margin in use + empty
    positions -> skip tick, returns (0.0, []))."""
    if not ctx.wallet:
        return 0.0, []
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — read error must not roll back the whole tick
        print(f"[remora.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    if not isinstance(data, dict):
        return 0.0, []

    held = []
    account_value = 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        try:
            account_value = max(account_value, float(ms.get("accountValue", 0)))
        except (TypeError, ValueError):
            pass
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                continue
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held.append(coin)

    # read-sanity guard (funding/$0 glitch 2026-06, verbatim from the iguana engine):
    # margin/notional IN USE but an EMPTY positions list = corrupt read; sizing or the
    # held-asset dedup off that re-enters held names. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        try:
            _use = max(_use,
                       float(_ms.get("totalMarginUsed", 0) or 0),
                       abs(float(_ms.get("totalNtlPos", 0) or 0)))
        except (TypeError, ValueError):
            pass
    if _use > 1.0 and not held:
        return 0.0, []
    return account_value, held


def _fetch_4h_candles(ctx, asset, dex):
    """READ-GUARD: the asset's 4h candles, [] on any error (degrade, never crash)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[remora.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return []
    if not md:
        return []
    data = md.get("data", md) if isinstance(md, dict) else {}
    if not isinstance(data, dict):
        return []
    candles = data.get("candles", {}) or {}
    return candles.get("4h", []) or []


def _load_recent(ctx):
    """Dedup map from the last clean tick's state record."""
    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = last.get("recent", {})
    return dict(recent) if isinstance(recent, dict) else {}


def _prune_recent(recent, ttl, now):
    cutoff = now - (ttl * 4)
    return {k: v for k, v in recent.items() if v >= cutoff}


def scan(inputs, ctx):
    now = time.time()
    asset = str(inputs.get("asset", _DEFAULT_ASSET))
    benchmark = str(inputs.get("benchmark", _DEFAULT_BENCHMARK))
    lookback = int(inputs.get("lookbackBars", _DEFAULT_LOOKBACK_BARS))
    min_gap = float(inputs.get("minGapPct", _DEFAULT_MIN_GAP_PCT))
    leverage_cfg = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    max_leverage = int(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct: PERCENT in (0,100]. Defensive fraction->percent (a stray 0.15 -> 15).
    raw_margin = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    margin_pct = round(raw_margin * 100, 2) if 0 < raw_margin <= 1.0 else round(raw_margin, 2)

    leverage = max(1, min(leverage_cfg, max_leverage))

    account_value, held_assets = _get_account(ctx)
    held_set = {h.upper() for h in held_assets}
    recent = _prune_recent(_load_recent(ctx), ttl, now)

    def _record(**kw):
        rec = {"ts": now, "asset": asset, "benchmark": benchmark,
               "lookback_bars": lookback, "recent": recent}
        rec.update(kw)
        if ctx.state:
            ctx.state.append(rec)

    if account_value <= 0:
        _record(emitted=False, gate="no_account_value")
        return []
    if asset.upper() in held_set:
        # one tranche per wallet BY DESIGN — the DSL owns this position's exit.
        _record(emitted=False, gate="held")
        return []
    last_sig = recent.get(asset.upper())
    if last_sig is not None and (now - last_sig) < ttl:
        _record(emitted=False, gate="recently_signaled")
        return []

    asset_closes = scoring.closes(_fetch_4h_candles(ctx, asset, _dex_for(asset, inputs)))
    bench_closes = scoring.closes(_fetch_4h_candles(ctx, benchmark, _dex_for(benchmark, inputs)))
    rel = scoring.relative_gap(asset_closes, bench_closes, lookback)
    if rel is None:
        _record(emitted=False, gate="insufficient_candles",
                asset_bars=len(asset_closes), bench_bars=len(bench_closes))
        return []

    sig = scoring.entry_signal(rel, min_gap)
    if sig is None:
        _record(emitted=False, gate="gap_below_floor", **rel)
        return []

    recent[asset.upper()] = now
    _record(emitted=True, **rel)
    return [{
        "asset": asset,
        "direction": "LONG",
        "marginPct": margin_pct,
        "leverage": leverage,
        "data": {
            "gap_pct": sig["gap_pct"],
            "asset_pct": sig["asset_pct"],
            "bench_pct": sig["bench_pct"],
            "benchmark": benchmark,
            "lookback_bars": lookback,
        },
    }]
