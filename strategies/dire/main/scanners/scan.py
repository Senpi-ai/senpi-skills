"""DIRE — supervised scanner (Runtime 3.0 port of the v2 DIRE BRENTOIL specialist).

Single-asset, single non-crypto XYZ instrument (xyz:BRENTOIL, HIP-3 DEX). Reads
BRENTOIL candles (5m/15m/1h/4h) + asset_context (markPx/oraclePx premium proxy for
smart-money) + OI velocity; scores via the pure `scoring.build_thesis`; applies the
v2 gate stack (minScore, FP-003 require-all-confirmations, FP-001 quiet-hours); and
emits ONE conviction-tiered signal when every gate passes.

Read-only + single-pass. Emits per-signal `leverage` AND `marginPct` (both scaled by
conviction tier, ported from the v2 sizingTiers); the runtime sizes the dollars, owns
the cooldowns / daily caps / drawdown halt, and trails the DSL exit. No daemon, no
push_signal.

XYZ / oil notes (preserved from v2, do NOT redesign):
  - asset = xyz:BRENTOIL, dex = "xyz" — prefix mandatory for the HIP-3 DEX.
  - macroAsset = "" — no BTC-correlation factor (oil is not crypto-correlated).
  - 24/7 incl weekends — NO market-hours gate. FP-001 quiet-hours is a thin-
    liquidity filter (00-04 UTC), not a session gate; apex setups bypass it.
"""

import sys
import time

import scoring

# v2 defaults (producer code, preferred over config.json per the port directive)
_DEFAULT_MIN_SCORE = 11               # v1.6 "hit fewer, win bigger" floor
_DEFAULT_TTL = 7200                   # 120m — mirror the v2 per-asset cooldown (anti re-fire)
_DEFAULT_QUIET_START = 0
_DEFAULT_QUIET_END = 4
_DEFAULT_APEX_BYPASS = 12
_DEFAULT_MAX_LEVERAGE = 10
_DEFAULT_SIZING_TIERS = [
    {"minScore": 9,  "leverage": 3,  "marginPct": 0.20, "label": "cautious"},
    {"minScore": 10, "leverage": 5,  "marginPct": 0.25, "label": "standard"},
    {"minScore": 11, "leverage": 7,  "marginPct": 0.30, "label": "conviction"},
    {"minScore": 12, "leverage": 10, "marginPct": 0.30, "label": "apex"},
]


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _asset_data(ctx, asset, dex):
    """READ-GUARD: a read error must never roll back the whole tick. Returns the
    inner data dict or None (degrade — emit nothing rather than crash)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["5m", "15m", "1h", "4h"],
            "include_funding": False,        # XYZ DEX — no funding expected
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[dire.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _held_brentoil(ctx, wallet):
    """READ-GUARD single-asset dedup: are we already holding BRENTOIL? Account
    value summed correctly across the main + xyz sub-DEX views (one wallet, two
    views per HIP-3 — count equity ONCE, never sum/double-count). Returns
    (account_value, held_brentoil: bool). Degrades to (0.0, False) on any error so
    the runtime's own slot/cooldown gates remain the backstop."""
    if not wallet:
        return 0.0, False
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    except Exception as exc:  # noqa: BLE001
        print(f"[dire.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
        return 0.0, False
    if not ch:
        return 0.0, False
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    if not isinstance(data, dict):
        return 0.0, False
    account_value = 0.0
    held = False
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        try:
            # one wallet, two sub-DEX views -> count equity ONCE (max, not sum;
            # summing double-counts the shared free balance -> 2x sizing).
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
            coin = str(pos.get("coin", "")).upper()
            if coin in ("BRENTOIL", "XYZ:BRENTOIL"):
                held = True
    return account_value, held


def scan(inputs, ctx):
    asset = (inputs.get("asset", "xyz:BRENTOIL") or "xyz:BRENTOIL")
    dex = _dex_for(asset, inputs)
    macro_asset = inputs.get("macroAsset", "")        # "" disables the BTC factor — oil is not crypto-correlated
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    tiers = inputs.get("sizingTiers", _DEFAULT_SIZING_TIERS)
    max_leverage = int(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    require_all = bool(inputs.get("requireAllConfirmations", True))
    quiet_start = int(inputs.get("quietHoursStartUtc", _DEFAULT_QUIET_START))
    quiet_end = int(inputs.get("quietHoursEndUtc", _DEFAULT_QUIET_END))
    apex_bypass = int(inputs.get("quietHoursApexBypassScore", _DEFAULT_APEX_BYPASS))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
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
    candles_by_tf = {
        "5m": candles.get("5m", []) or [],
        "15m": candles.get("15m", []) or [],
        "1h": candles.get("1h", []) or [],
        "4h": candles.get("4h", []) or [],
    }
    asset_context = data.get("asset_context", {}) or {}
    oi_vel = scoring.extract_oi_velocity_1h(data)

    th = scoring.build_thesis(candles_by_tf, asset_context, oi_vel, inputs)

    out = []
    result = None
    if not th:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked",
                  "score": None, "direction": None}
        print(f"[dire.scan] {asset} HOLD (gate blocked: 4TF/SM hard gate failed)", file=sys.stderr)
    else:
        score = th["score"]
        direction = th["direction"]
        reasons = th["reasons"]

        # ── gate: minScore floor ──
        if score < min_score:
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "score_low",
                      "score": score, "direction": direction, "reasons": reasons}
            print(f"[dire.scan] {asset} HOLD: score={score}/{min_score:.0f} {direction} | {reasons}",
                  file=sys.stderr)
        # ── gate: FP-003 require-all-confirmations ──
        elif require_all and not scoring.all_confirmations_present(reasons)[0]:
            _, missing = scoring.all_confirmations_present(reasons)
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "confirmations_incomplete",
                      "score": score, "direction": direction, "missing": missing, "reasons": reasons}
            print(f"[dire.scan] {asset} HOLD: confirmations_incomplete missing={missing}", file=sys.stderr)
        # ── gate: FP-001 quiet hours (apex bypass) — liquidity filter, NOT market hours ──
        elif scoring.in_quiet_hours(hour, quiet_start, quiet_end) and score < apex_bypass:
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "quiet_hours",
                      "score": score, "direction": direction, "hour": hour, "reasons": reasons}
            print(f"[dire.scan] {asset} HOLD: QUIET_HOURS hour={hour}_UTC score={score}<apex_{apex_bypass}",
                  file=sys.stderr)
        else:
            # ── single-asset dedup: refuse to re-emit if already holding BRENTOIL ──
            _account_value, held = _held_brentoil(ctx, ctx.wallet)
            if held:
                result = {"ts": now, "asset": asset, "emitted": False, "gate": "already_held",
                          "score": score, "direction": direction, "reasons": reasons}
                print(f"[dire.scan] {asset} HOLD: already holding BRENTOIL", file=sys.stderr)
            else:
                tier = scoring.resolve_sizing_tier(score, tiers) or {}
                leverage = scoring.compute_leverage(score, tiers, max_leverage)
                # v2 sizingTiers carry marginPct as a FRACTION (0.20/0.25/0.30);
                # Runtime 3.0 wants a PERCENT in (0,100], so *100 -> 20/25/30.
                margin_pct = round(float(tier.get("marginPct", 0.20)) * 100, 2)
                tier_label = str(tier.get("label", ""))
                if leverage <= 0 or margin_pct <= 0:
                    result = {"ts": now, "asset": asset, "emitted": False, "gate": "sizing_unresolved",
                              "score": score, "direction": direction, "reasons": reasons}
                    print(f"[dire.scan] {asset} HOLD: sizing unresolved lev={leverage} marginPct={margin_pct}",
                          file=sys.stderr)
                else:
                    recent[au] = now
                    result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass",
                              "score": score, "direction": direction, "leverage": leverage,
                              "tier": tier_label, "marginPct": margin_pct, "reasons": reasons}
                    print(f"[dire.scan] {asset} EMIT: score={score} {direction} {leverage}x "
                          f"{tier_label} marginPct={margin_pct} | {reasons}", file=sys.stderr)
                    out = [{
                        "asset": asset,
                        "direction": direction,
                        "marginPct": margin_pct,      # PERCENT of withdrawable — runtime sizes (marginPct/100)*withdrawable
                        "leverage": leverage,         # conviction-tiered (3/5/7/10); runtime applies it
                        "data": {
                            "score": float(score),
                            "leverage": float(leverage),
                            "marginPct": margin_pct,
                            "tier": tier_label,
                            "direction": direction,
                            "trend4h": th["trend_4h"], "trend1h": th["trend_1h"],
                            "trend15m": th["trend_15m"], "trend5m": th["trend_5m"],
                            "smPremiumAbsPct": round(scoring._f(th["sm_premium_abs"]) * 100, 5),
                            "smDetail": th["sm_detail"],
                            "oiChange1h": th["oi_vel"],
                            "markPx": th["mark_px"],
                            "priceChange5m": th["mom_5m"], "priceChange15m": th["mom_15m"],
                            "priceChange1h": th["mom_1h"], "priceChange4h": th["mom_4h"],
                            "reasons": reasons,
                        },
                    }]

    # ── persist dedup map + this tick's result EVERY tick; self-trims at
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[dire.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
