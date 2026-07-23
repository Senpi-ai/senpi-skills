"""POLAR — supervised scanner (Runtime 3.0 port of the v2 Polar ETH alpha hunter).

Single-asset, SM-LED hybrid. Reads ETH candles (5m/15m/1h/4h) + funding/OI-velocity,
a macro driver (BTC 15m+1h momentum), and smart-money positioning
(leaderboard_get_markets); scores via the pure `scoring.build_thesis`; and emits ONE
conviction-tiered signal when the composite clears `minScore` AND it is outside the
FP-001 quiet-hours window (apex-score bypass). Read-only + single-pass — emits a
`marginPct` intent plus a per-signal `leverage` (5/7/10 by score); the runtime sizes
the dollars, owns the cooldowns/risk gates, and trails the DSL exit. No daemon, no
push_signal."""

import sys
import time

import scoring

def _sm_row_matches(row, token, target):
    """True if leaderboard row `row` is the market for `target`.

    `leaderboard_get_markets` returns BARE tickers (`NVDA`) plus a separate `dex`
    field, while our universe carries the qualified name (`xyz:NVDA`). A raw
    `token != target` compare therefore NEVER matches an xyz name, so every xyz
    instrument reads as "no smart-money data" and a hard SM gate blocks it
    permanently. Compare bare tickers, and require the dex to agree so a main-DEX
    name cannot cross-match its xyz twin (e.g. main `GOLD` vs `xyz:GOLD`)."""
    tok = str(token or "").upper()
    want = str(target or "").upper()
    if tok.split(":", 1)[-1] != want.split(":", 1)[-1]:
        return False
    row_xyz = (str((row or {}).get("dex", "")).strip().lower() == "xyz"
               or tok.startswith("XYZ:"))
    return row_xyz == want.startswith("XYZ:")


_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (anti re-fire)
_DEFAULT_TIERS = [[17, 10], [15, 7], [14, 5]]


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _asset_data(ctx, asset, dex, intervals, funding):
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "include_funding": funding,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[polar.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _btc_correlation(ctx, macro_asset):
    """Port of v2 get_btc_correlation: BTC 15m + 1h 1-bar momentum, or (None, None)
    on any read failure (BTC factor degrades to neutral, never crashes the tick)."""
    if not macro_asset:
        return None, None
    data = _asset_data(ctx, macro_asset, "", ["15m", "1h"], False)
    if not data:
        return None, None
    candles = data.get("candles", {}) or {}
    c15 = candles.get("15m", []) or []
    c1h = candles.get("1h", []) or []
    m15 = scoring.mom(c15, 1) if len(c15) >= 2 else None
    m1h = scoring.mom(c1h, 1) if len(c1h) >= 2 else None
    return m15, m1h


def _sm_for_asset(ctx, asset):
    """Port of v2 get_eth_sm_signal: net smart-money lean for `asset` from
    leaderboard_get_markets, using the RAW pct_of_top_traders_gain (NOT a 0-100
    long_ratio — that is polar's hard-gate scale). Returns
    {direction, pct, traders, cc_15m, cc_1h, cc_4h} or None."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {"limit": 100})
    except Exception as exc:  # noqa: BLE001 — smart-money is the directional driver; on read failure -> None (thesis blocks safely)
        print(f"[polar.scan] leaderboard_get_markets read failed (smart-money -> None): {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    want = asset.upper()
    long_pct = short_pct = 0.0
    traders_sum = 0
    cc_15m = cc_1h = cc_4h = 0.0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", ""))).upper()
        if not _sm_row_matches(m, token, want):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0) or 0)
        cc15 = scoring._f(m.get("contribution_pct_change_15m", 0))
        cc1h = scoring._f(m.get("contribution_pct_change_1h", 0))
        cc4h = scoring._f(m.get("contribution_pct_change_4h", 0))
        if d == "LONG":
            long_pct = pct
            traders_sum += traders
            cc_15m, cc_1h, cc_4h = cc15, cc1h, cc4h
        elif d == "SHORT":
            short_pct = pct
            traders_sum += traders
            cc_15m, cc_1h, cc_4h = cc15, cc1h, cc4h

    if not found:
        return None
    total = long_pct + short_pct
    if total == 0:
        return {"direction": "NEUTRAL", "pct": 0, "traders": traders_sum,
                "cc_15m": cc_15m, "cc_1h": cc_1h, "cc_4h": cc_4h}
    long_ratio = (long_pct / total) * 100
    if long_ratio > 58:
        return {"direction": "LONG", "pct": long_pct, "traders": traders_sum,
                "cc_15m": cc_15m, "cc_1h": cc_1h, "cc_4h": cc_4h}
    if long_ratio < 42:
        return {"direction": "SHORT", "pct": short_pct, "traders": traders_sum,
                "cc_15m": cc_15m, "cc_1h": cc_1h, "cc_4h": cc_4h}
    return {"direction": "NEUTRAL", "pct": max(long_pct, short_pct), "traders": traders_sum,
            "cc_15m": cc_15m, "cc_1h": cc_1h, "cc_4h": cc_4h}


def _oi_change_1h(data):
    """Extract v2 polar's oi_velocity.oi_change_pct_1h, or None if absent."""
    oi_vel = data.get("oi_velocity")
    if isinstance(oi_vel, dict):
        return oi_vel.get("oi_change_pct_1h")
    return None


def _in_quiet_hours(hour, start, end, apex_bypass, score):
    """FP-001: True if `hour` is inside the low-liquidity window AND `score` is
    below the apex bypass. start==end disables. Ported from v2 in_quiet_hours."""
    if start == end:
        return False
    if start < end:
        in_window = (start <= hour < end)
    else:  # wrap past midnight
        in_window = (hour >= start or hour < end)
    return in_window and score < apex_bypass


def scan(inputs, ctx):
    asset = (inputs.get("asset", "ETH") or "ETH")
    dex = _dex_for(asset, inputs)
    macro_asset = inputs.get("macroAsset", "BTC")     # "" disables the BTC factor
    min_score = float(inputs.get("minScore", 12))
    margin_pct = float(inputs.get("marginPct", 50))   # PERCENT of withdrawable (0,100], not a fraction
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    qh_start = int(inputs.get("quietHoursStartUtc", 0))
    qh_end = int(inputs.get("quietHoursEndUtc", 4))
    qh_apex = float(inputs.get("quietHoursApexBypassScore", 17))
    now = time.time()
    hour = time.gmtime(now).tm_hour

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []

    # ── SM is the directional driver — fetch first (mirrors v2 ordering) ──
    sm = _sm_for_asset(ctx, asset)

    data = _asset_data(ctx, asset, dex, ["5m", "15m", "1h", "4h"], True)
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    ctx_block = data.get("asset_context", {}) or {}
    funding = scoring._f(ctx_block.get("funding", 0))
    oi_change_1h = _oi_change_1h(data)

    btc_mom_15m, btc_mom_1h = _btc_correlation(ctx, macro_asset)

    th = scoring.build_thesis(
        candles.get("5m", []), candles.get("15m", []), candles.get("1h", []), candles.get("4h", []),
        funding, oi_change_1h, btc_mom_15m, btc_mom_1h, sm, inputs,
    )

    # ── per-tick result record → scan-results history (ctx.state, bounded by
    #    state_history_max_count). Read back with ctx.state.recent(n); persists to state.json. ──
    out = []
    if not th:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked",
                  "score": None, "direction": None,
                  "smDir": (sm or {}).get("direction"), "smPct": (sm or {}).get("pct")}
        print(f"[polar.scan] {asset} HOLD (gate): sm={(sm or {}).get('direction')} "
              f"pct={(sm or {}).get('pct')} — structural/sm gate blocked", file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"],
                  "trend1h": th["trend_1h"], "mom15m": th["mom_15m"], "rsi": th["rsi"],
                  "smPct": th["sm_pct"], "reasons": th["reasons"]}
        print(f"[polar.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_strength_4h']:.0%} rsi={th['rsi']} smPct={th['sm_pct']} | {th['reasons']}",
              file=sys.stderr)
    elif _in_quiet_hours(hour, qh_start, qh_end, qh_apex, th["score"]):
        # FP-001 — passed score but inside the low-liquidity window and not apex
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "quiet_hours",
                  "score": th["score"], "direction": th["direction"], "hourUtc": hour,
                  "apexBypass": qh_apex, "reasons": th["reasons"]}
        print(f"[polar.scan] {asset} HOLD (quiet_hours): hour={hour}_UTC score={th['score']} "
              f"< apex {qh_apex:.0f} {th['direction']}", file=sys.stderr)
    else:
        leverage = scoring.get_leverage(th["score"], tiers)
        tier_label = scoring.leverage_label(th["score"], tiers)
        recent[au] = now
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "leverage": leverage, "tier": tier_label,
                  "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"], "trend1h": th["trend_1h"],
                  "mom15m": th["mom_15m"], "rsi": th["rsi"], "smPct": th["sm_pct"], "reasons": th["reasons"]}
        print(f"[polar.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x ({tier_label}) | {th['reasons']}",
              file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # conviction-tiered (5/7/10); runtime applies it
            # Runtime schema validation REJECTS a null for a field declared `type: number|string`,
            # even when `required: false` — the whole candidate is dropped (`candidate_rejected`),
            # silently. An optional field that does not apply must be OMITTED, never set to None.
            "data": {k: v for k, v in {
                "score": th["score"], "leverage": leverage, "direction": th["direction"], "tier": tier_label,
                "trend4h": th["trend_4h"], "trendStrength4h": th["trend_strength_4h"], "trend1h": th["trend_1h"],
                "mom5mPct": th["mom_5m"], "mom15mPct": th["mom_15m"], "mom1hPct": th["mom_1h"], "mom4hPct": th["mom_4h"],
                "fundingRate": th["funding"], "oiChange1h": th["oi_change_1h"],
                "btcMom15mPct": th["btc_mom_15m"], "btcMom1hPct": th["btc_mom_1h"], "rsi": th["rsi"],
                "smPct": th["sm_pct"], "smTraders": th["sm_traders"], "smCc15m": th["sm_cc15m"],
                "smCc1h": th["sm_cc1h"], "smCc4h": th["sm_cc4h"],
                "reasons": th["reasons"],
            }.items() if v is not None},
        }]

    # ── persist dedup map + this tick's result EVERY tick; self-trims at
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[polar.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
