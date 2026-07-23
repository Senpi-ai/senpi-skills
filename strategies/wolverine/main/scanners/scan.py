"""WOLVERINE — supervised scanner (Runtime 3.0 port of the v2 Wolverine HYPE alpha hunter).

Single-asset (HYPE). Reads HYPE candles (5m/15m/1h/4h) + funding + OI velocity, the
smart-money lean (leaderboard_get_markets), and the MACRO/REGIME inputs the gate needs:
  - market-wide funding regime  (market_get_funding_regime)
  - HYPE funding persistence     (market_get_funding_history)
  - BTC 15m+1h momentum          (market_get_asset_data BTC)
Scores via the pure `scoring.build_thesis`; applies FP-001 quiet hours; and emits ONE
conviction-tiered signal when the composite clears `minScore`. Read-only + single-pass —
emits a `marginPct` intent plus a per-signal `leverage` (3/5 by score); the runtime sizes
the dollars, owns the cooldowns/risk gates, and trails the DSL exit. No daemon, no push_signal.

EVERY ctx.senpi_mcp.call_tool is READ-GUARDED: the macro/regime calls (regime, persistence,
BTC) are OPTIONAL — a failure degrades that factor to neutral (None) and never crashes the
tick. Only the core HYPE asset-data read is load-bearing; if it fails we return []."""

import sys
import time
from datetime import datetime, timezone

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
# v2-quirk: producer floor was 9; config.json/runtime inputs set minScore=10 ("patient-
# conviction"). The GATE uses inputs.minScore (10); the producer's 9 constant is dead.
_DEFAULT_TIERS = [[11, 5, "apex"], [9, 3, "standard"]]


def _read(ctx, tool, args, label, default=None):
    """READ-GUARD: one read-only MCP call. Any failure logs + returns `default`
    (degrade, never crash). Mutations would raise PermissionError loudly — we only
    ever call reads here."""
    try:
        return ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[wolverine.scan] {label} read failed (degrade): {exc!r}", file=sys.stderr)
        return default


def _unwrap(raw, *keys):
    """Unwrap {data: ...} / nested keys defensively."""
    if raw is None:
        return None
    cur = raw.get("data", raw) if isinstance(raw, dict) else raw
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, cur)
    return cur


def _hype_full_picture(ctx, asset, dex):
    raw = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["5m", "15m", "1h", "4h"],
        "include_funding": True,
        "include_order_book": False,
        "dex": dex,
    }, "market_get_asset_data(HYPE)")
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    return data if isinstance(data, dict) else None


def _sm_for_asset(ctx, asset):
    """Port of v2 get_hype_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns {direction, pct, traders, cc_15m} or None."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": 100},
                "leaderboard_get_markets (smart-money -> neutral)")
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
    """MACRO/REGIME GATE input — market-wide funding regime. Optional: a failure
    (e.g. 401/timeout) degrades to None (neutral)."""
    raw = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime (regime -> neutral)")
    data = _unwrap(raw)
    if isinstance(data, dict):
        return data.get("regime")
    return None


def _funding_persistence_h(ctx, asset, dex):
    """MACRO/REGIME GATE input — how long HYPE's funding regime has persisted.
    Optional: a failure degrades to None."""
    raw = _read(ctx, "market_get_funding_history", {"asset": asset, "dex": dex},
                "market_get_funding_history (persistence -> none)")
    data = _unwrap(raw)
    if isinstance(data, dict):
        ph = data.get("persistence_hours")
        try:
            return float(ph) if ph is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _btc_correlation(ctx, macro_asset):
    """v2 get_btc_correlation: (mom_15m, mom_1h) for the macro driver. Optional."""
    if not macro_asset:
        return None, None
    raw = _read(ctx, "market_get_asset_data", {
        "asset": macro_asset,
        "candle_intervals": ["15m", "1h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": "",
    }, f"market_get_asset_data({macro_asset}) (btc-corr -> neutral)")
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return None, None
    candles = data.get("candles", {}) or {}
    c15 = candles.get("15m", []) or []
    c1h = candles.get("1h", []) or []
    mom_15m = scoring.mom(c15, 1) if len(c15) >= 2 else None
    mom_1h = scoring.mom(c1h, 1) if len(c1h) >= 2 else None
    return mom_15m, mom_1h


def _in_quiet_hours(inputs, now_utc_hour):
    """FP-001: skip emission in a low-liquidity UTC window unless apex-tier.
    Returns (quiet, apex_bypass_score). start==end disables."""
    qh = inputs.get("quietHours") or {}
    start = int(qh.get("startUtc", 0))
    end = int(qh.get("endUtc", 4))
    apex = int(qh.get("apexBypassScore", 11))
    if start == end:
        return False, apex
    if start < end:
        return (start <= now_utc_hour < end), apex
    return (now_utc_hour >= start or now_utc_hour < end), apex


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def scan(inputs, ctx):
    asset = (inputs.get("asset", "HYPE") or "HYPE")
    dex = _dex_for(asset, inputs)
    macro_asset = inputs.get("macroAsset", "BTC")     # "" disables the BTC factor
    min_score = float(inputs.get("minScore", 10))     # config "patient-conviction" floor
    margin_pct = float(inputs.get("marginPct", 25))   # PERCENT of withdrawable (0,100], not a fraction
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()
    hour = datetime.now(timezone.utc).hour

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []

    # ── core read (load-bearing) ──
    data = _hype_full_picture(ctx, asset, dex)
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    asset_ctx = data.get("asset_context", data.get("assetContext", {})) or {}
    funding = scoring._f(asset_ctx.get("funding", 0))
    oi_velocity = data.get("oi_velocity") if isinstance(data.get("oi_velocity"), dict) else {}

    # ── smart-money + MACRO/REGIME inputs (all optional, degrade to neutral) ──
    sm = _sm_for_asset(ctx, asset)
    regime = _funding_regime(ctx)
    persistence_h = _funding_persistence_h(ctx, asset, dex)
    btc_mom_15m, btc_mom_1h = _btc_correlation(ctx, macro_asset)

    th = scoring.build_thesis(
        candles.get("5m", []), candles.get("15m", []), candles.get("1h", []), candles.get("4h", []),
        funding, oi_velocity, sm, regime, persistence_h, btc_mom_15m, btc_mom_1h, inputs,
    )

    out = []
    if not th:
        t4, s4 = scoring.trend_structure(candles.get("4h", []))
        t1, _ = scoring.trend_structure(candles.get("1h", []))
        m15 = scoring.mom(candles.get("15m", []), 1)
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked", "score": None,
                  "direction": None, "trend4h": t4, "ts4h": round(s4, 3), "trend1h": t1,
                  "mom15m": round(m15, 3), "regime": regime}
        print(f"[wolverine.scan] {asset} HOLD (gate/hard-block): 4h={t4} {s4:.0%} | "
              f"1h={t1} | 15m={m15:+.2f}% | regime={regime}", file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"],
                  "trend1h": th["trend_1h"], "mom15m": th["mom_15m"], "rsi": th["rsi"],
                  "regime": th["regime"], "reasons": th["reasons"]}
        print(f"[wolverine.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_strength_4h']:.0%} rsi={th['rsi']} regime={th['regime']} | "
              f"{th['reasons']}", file=sys.stderr)
    else:
        # FP-001 quiet hours: skip non-apex emission in the low-liquidity window.
        quiet, apex_bypass = _in_quiet_hours(inputs, hour)
        if quiet and th["score"] < apex_bypass:
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "quiet_hours",
                      "score": th["score"], "direction": th["direction"], "regime": th["regime"]}
            print(f"[wolverine.scan] {asset} QUIET_HOURS hour={hour}_UTC score={th['score']}<apex_{apex_bypass}",
                  file=sys.stderr)
        else:
            leverage, tier_label = scoring.get_leverage_tier(th["score"], tiers)
            recent[au] = now
            result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                      "direction": th["direction"], "leverage": leverage, "tier": tier_label,
                      "trend4h": th["trend_4h"], "ts4h": th["trend_strength_4h"], "trend1h": th["trend_1h"],
                      "mom15m": th["mom_15m"], "rsi": th["rsi"], "regime": th["regime"],
                      "reasons": th["reasons"]}
            print(f"[wolverine.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x "
                  f"({tier_label}) regime={th['regime']} | {th['reasons']}", file=sys.stderr)
            out = [{
                "asset": asset,
                "direction": th["direction"],
                "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
                "leverage": leverage,             # conviction-tiered (3/5); runtime applies it
                "data": {
                    "score": th["score"], "tier": tier_label, "leverage": leverage,
                    "direction": th["direction"],
                    "trend4h": th["trend_4h"], "trendStrength4h": th["trend_strength_4h"],
                    "trend1h": th["trend_1h"],
                    "mom5mPct": th["mom_5m"], "mom15mPct": th["mom_15m"], "mom1hPct": th["mom_1h"],
                    "mom4hPct": th["mom_4h"],
                    "fundingRate": th["funding"], "fundingRegime": th["regime"],
                    "fundingPersistenceHours": th["persistence_h"],
                    "oiChange1h": th["oi_change_1h"], "vol1h": th["vol_1h"],
                    "btcMom15m": th["btc_mom_15m"], "btcMom1h": th["btc_mom_1h"],
                    "rsi": th["rsi"],
                    "smPct": th["sm_pct"], "smTraders": th["sm_traders"], "smCc15m": th["sm_cc_15m"],
                    "smAligned": th["sm_aligned"],
                    "reasons": th["reasons"],
                },
            }]

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[wolverine.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
