"""GRIZZLY — supervised scanner (Runtime 3.0 port of the v2 Grizzly BTC alpha hunter).

Single-asset (BTC). Reads BTC candles (5m/15m/1h/4h) + funding + OI velocity, the
smart-money lean (leaderboard_get_markets), the funding regime + funding-history
persistence; scores via the pure `scoring.build_thesis` (which owns the six hard
gates INCLUDING the v5.5 macro V-recovery gate, the SM-opposes hard block, and the
RSI hard band); then applies the v2 main()-level gates in the same order —
minScore, FP-001 quiet hours (apex bypass), FP-003 require-all-confirmations — and
emits ONE conviction-tiered signal. Read-only + single-pass — emits a `marginPct`
intent plus a per-signal `leverage` (7/10/10 by score); the runtime sizes the
dollars, owns the cooldowns/risk gates, and trails the DSL exit. No daemon, no
push_signal. Every ctx.senpi_mcp.call_tool is READ-GUARDED: a read error degrades
that one factor and never crashes the tick."""

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


_DEFAULT_TTL = 3600           # 60m — mirror the v2 per-asset cooldown (anti re-fire)
_DEFAULT_TIERS = [[14, 10], [12, 10]]
_DEFAULT_LEVERAGE = 7


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _asset_data(ctx, asset, dex):
    """BTC full picture: candles + funding + OI velocity. Read-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["5m", "15m", "1h", "4h"],
            "include_funding": True,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[grizzly.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _sm_for_asset(ctx, asset):
    """Port of v2 get_btc_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns {direction, pct, traders, cc_15m} or None.
    On a read failure returns None -> scoring treats SM as absent (no align bonus,
    no opposes block, and the 15m-stale penalty fires) — same as v2's (None,0,0,0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {"limit": 100})
    except Exception as exc:  # noqa: BLE001 — smart-money is optional; never crash the tick on it
        print(f"[grizzly.scan] leaderboard_get_markets read failed (smart-money -> neutral): {exc!r}", file=sys.stderr)
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
    cc_15m = 0.0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", ""))).upper()
        if not _sm_row_matches(m, token, want):
            continue
        found = True
        direction = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0) or 0)
        cc = scoring._f(m.get("contribution_pct_change_15m", 0))
        if direction == "LONG":
            long_pct = pct
            traders_sum += traders
            cc_15m = cc
        elif direction == "SHORT":
            short_pct = pct
            traders_sum += traders
            cc_15m = cc
    if not found:
        return None

    total = long_pct + short_pct
    if total == 0:
        return {"direction": "NEUTRAL", "pct": 0, "traders": traders_sum, "cc_15m": cc_15m}
    long_ratio = (long_pct / total) * 100
    if long_ratio > 58:
        return {"direction": "LONG", "pct": long_pct, "traders": traders_sum, "cc_15m": cc_15m}
    if long_ratio < 42:
        return {"direction": "SHORT", "pct": short_pct, "traders": traders_sum, "cc_15m": cc_15m}
    return {"direction": "NEUTRAL", "pct": max(long_pct, short_pct), "traders": traders_sum, "cc_15m": cc_15m}


def _funding_regime(ctx):
    """Port of v2 get_funding_regime. Read-guarded -> None on failure."""
    try:
        fr = ctx.senpi_mcp.call_tool("market_get_funding_regime", {})
    except Exception as exc:  # noqa: BLE001
        print(f"[grizzly.scan] market_get_funding_regime read failed: {exc!r}", file=sys.stderr)
        return None
    if not fr:
        return None
    data = fr.get("data", fr) if isinstance(fr, dict) else None
    return data.get("regime") if isinstance(data, dict) else None


def _funding_persistence_h(ctx, asset):
    """Port of v2 get_funding_history_btc -> persistence_hours. Read-guarded -> None."""
    try:
        fh = ctx.senpi_mcp.call_tool("market_get_funding_history", {"asset": asset})
    except Exception as exc:  # noqa: BLE001
        print(f"[grizzly.scan] market_get_funding_history read failed: {exc!r}", file=sys.stderr)
        return None
    if not fh:
        return None
    data = fh.get("data", fh) if isinstance(fh, dict) else None
    if not isinstance(data, dict):
        return None
    return data.get("persistence_hours")


def scan(inputs, ctx):
    asset = (inputs.get("asset", "BTC") or "BTC")
    dex = _dex_for(asset, inputs)
    min_score = float(inputs.get("minScore", 12))
    margin_pct = float(inputs.get("marginPct", 50))   # PERCENT of withdrawable (0,100], not a fraction
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    default_leverage = float(inputs.get("defaultLeverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    require_all = bool(inputs.get("requireAllConfirmations", True))
    qh_start = int(inputs.get("quietHoursStartUtc", 0))
    qh_end = int(inputs.get("quietHoursEndUtc", 4))
    qh_apex = int(inputs.get("quietHoursApexBypassScore", 14))
    now = time.time()
    hour = time.gmtime(now).tm_hour

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []

    data = _asset_data(ctx, asset, dex)
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    c5 = candles.get("5m", [])
    c15 = candles.get("15m", [])
    c1h = candles.get("1h", [])
    c4h = candles.get("4h", [])
    asset_ctx = data.get("asset_context", data.get("assetContext", {})) or {}
    funding = scoring._f(asset_ctx.get("funding", 0))
    oi_velocity = data.get("oi_velocity") if isinstance(data.get("oi_velocity"), dict) else None

    sm = _sm_for_asset(ctx, asset)
    funding_regime = _funding_regime(ctx)
    funding_persistence_h = _funding_persistence_h(ctx, asset)

    th = scoring.build_thesis(
        c5, c15, c1h, c4h, funding, oi_velocity, sm,
        funding_regime, funding_persistence_h, inputs,
    )

    out = []
    if not th:
        # blocked by a hard gate (4h/1h/15m/base-tech/macro-V-recovery/SM-opposes/RSI)
        t4, s4 = scoring.trend_structure(c4h)
        t1, _ = scoring.trend_structure(c1h)
        m15 = scoring.mom(c15, 1)
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked", "score": None,
                  "direction": None, "trend4h": t4, "ts4h": round(s4, 3), "trend1h": t1,
                  "mom15m": round(m15, 3)}
        print(f"[grizzly.scan] {asset} HOLD (hard gate): 4h={t4} {s4:.0%} (need!=NEUTRAL & >=75%) | "
              f"1h={t1} | 15m={m15:+.2f}%", file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "score_low", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"],
                  "trend1h": th["trend_1h"], "rsi": th["rsi"], "reasons": th["reasons"]}
        print(f"[grizzly.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_strength_4h']:.0%} rsi={th['rsi']} | {th['reasons']}",
              file=sys.stderr)
    elif scoring.in_quiet_hours(hour, qh_start, qh_end) and th["score"] < qh_apex:
        # FP-001 quiet hours — sub-apex setups wait for the active window
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "quiet_hours", "score": th["score"],
                  "direction": th["direction"], "hour": hour, "apexBypass": qh_apex}
        print(f"[grizzly.scan] {asset} HOLD (quiet hours): hour={hour}_UTC score={th['score']}<apex_{qh_apex}",
              file=sys.stderr)
    else:
        # FP-003 require-all-confirmations
        ok, missing = (True, [])
        if require_all:
            ok, missing = scoring.all_confirmations_present(th["reasons"])
        if not ok:
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "missing_confirmations",
                      "score": th["score"], "direction": th["direction"], "missing": missing,
                      "reasons": th["reasons"]}
            print(f"[grizzly.scan] {asset} HOLD (missing confirmations): {missing} score={th['score']}",
                  file=sys.stderr)
        else:
            leverage = scoring.get_leverage(th["score"], tiers, default_leverage)
            tier_label = "apex" if th["score"] >= 14 else ("conviction" if th["score"] >= 12 else "default")
            recent[au] = now
            result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                      "direction": th["direction"], "leverage": leverage, "tier": tier_label,
                      "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"], "trend1h": th["trend_1h"],
                      "rsi": th["rsi"], "reasons": th["reasons"]}
            print(f"[grizzly.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x ({tier_label}) | "
                  f"{th['reasons']}", file=sys.stderr)
            out = [{
                "asset": asset,
                "direction": th["direction"],
                "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
                "leverage": leverage,             # conviction-tiered (7/10/10); runtime applies it
                # `required: false` in signal_data_schema permits an ABSENT key, never a present-and-null:
                # the intake discards the WHOLE candidate on a null optional. Drop them here rather than
                # coercing to 0/"" — a coerced value would assert a measurement that was never taken.
                "data": {k: v for k, v in {
                    "score": th["score"], "tier": tier_label, "leverage": leverage,
                    "direction": th["direction"],
                    "trend4h": th["trend_4h"], "trendStrength4h": th["trend_strength_4h"],
                    "trend1h": th["trend_1h"], "rsi": th["rsi"],
                    "funding": th["funding"], "fundingRegime": th["regime"],
                    "fundingPersistenceHours": th["persistence_h"],
                    "oiChange1h": th["oi_change_1h"], "vol1h": th["vol_1h"],
                    "smPct": th["sm_pct"], "smTraders": th["sm_traders"], "smCc15m": th["sm_cc_15m"],
                    "priceChange5m": th["mom_5m"], "priceChange15m": th["mom_15m"],
                    "priceChange1h": th["mom_1h"], "priceChange4h": th["mom_4h"],
                    "reasons": th["reasons"],
                }.items() if v is not None},
            }]

    # ── persist dedup map + this tick's result EVERY tick; self-trims at
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[grizzly.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
