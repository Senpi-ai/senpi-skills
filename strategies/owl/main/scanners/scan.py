"""OWL — supervised scanner (Runtime 3.0 port of the v2 OWL producer).

Multi-asset universe, emit-one. A faithful port of the v2 OWL "Pure Contrarian
Crowding-Unwind Hunter" (SKILL.md / producer v8.0.1, thesis frozen at v7.1):

  1. Build the universe — EVERY HL crypto perp with OI > $3M (no top-N
     truncation; XYZ banned), from market_list_instruments.
  2. Pull the smart-money positioning map (leaderboard_get_markets, limit=200):
     per-coin long%/trader-count, AND BTC's 4h price change for the macro gate.
  3. MACRO_TREND_GATE: if |BTC 4h move| > 3%, stand down (mean reversion fails
     in trending regimes).
  4. Score crowding per asset (funding extremity + SM tilt + OI concentration)
     and update the cross-tick PERSISTENCE ledger in ctx.state.
  5. For assets that have persisted >=1h above the crowding floor, fetch 1h/4h
     candles and detect exhaustion (volume declining, price stalling vs crowd,
     capitulation wick, 4h RSI divergence).
  6. Filter combined score (crowding + exhaustion) >= 12 + per-asset emit
     cooldown, then EMIT ONE signal for the single highest-scoring candidate —
     a marginPct sizing INTENT plus a per-score leverage tier. The entry
     direction is the OPPOSITE of the crowd direction (Owl fades the crowd).

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the
runtime sizes the dollars, owns the cooldowns/risk gates/slots, and trails the
DSL exit.

FIDELITY NOTES vs owl-producer.py v8.0.1 (thesis frozen at v7.1):
  - Crowding score, exhaustion detection, funding annualization, the persistence
    ledger semantics (firstSeen/ts/peakScore/belowThresholdCount + 2-tick
    tolerance), the MACRO_TREND_GATE (|BTC 4h| > 3%), conviction leverage tiers
    (7/8/10 by score), and all gate thresholds are ported VERBATIM (see scoring.py).
  - SIZING: v2 MARGIN_PCT 0.25 was a FRACTION; the producer emitted
    marginUsd = account_value * 0.25. This port emits a top-level `marginPct`
    PERCENT (25) and the runtime sizes (marginPct/100)*withdrawable. Defensive
    <=1.0 guard treats a pasted fraction as a percent (x100).
  - PERSISTENCE STATE: v2 persisted crowding-history.json / asset-cooldowns.json
    under state/<wallet-hash>/. This port keeps the crowding-persistence ledger
    AND a per-asset emit-cooldown map in ctx.state (transactional, rolled back on
    a failed tick). Semantics preserved verbatim.
  - DROPPED (runtime owns it now, per the v2 config _runtime_owns block): the
    producer-side PnL-aware dynamic daily cap (get_dynamic_daily_cap) +
    trade-counter.json + equity-baseline.json. The runtime guard_rails enforce
    max_entries_per_day, drawdown_halt_pct, daily_loss_limit_pct, and the
    per_asset_cooldown. FLAGGED in the port report. The producer's account-value
    read (for sizing + the dynamic cap) is therefore dropped; the runtime sizes
    from withdrawable via marginPct. Held-asset suppression is kept (the v2
    push_signal held-asset skip).
  - DROPPED: the v2 wallet-not-resolved fail-loud path and the equity-baseline
    capture (sizing is the runtime's job now).
  - v2 emitted exactly one signal (candidates[:1], top by combined score).
    Preserved: scan() emits <= 1 signal/tick.

PORT NOTE (XYZ ban): the v2 producer filtered XYZ via inst.get("dex") == "xyz"
OR a "xyz:" name prefix. The live combined market_list_instruments response
carries NO dex field — XYZ equities are identified ONLY by the "XYZ:" name
prefix (e.g. "XYZ:SP500"). This port bans XYZ by name prefix (matching condor /
kodiak), faithfully implementing the v2 INTENT (XYZ banned) against the real
response shape."""

import sys
import time

import scoring


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick (per the scan contract, ANY exception rolls the tick to
    []). Returns None on failure so the existing degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[owl.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _is_xyz(coin):
    """XYZ equities/commodities are identified by the 'XYZ:' name prefix in the
    live combined market_list_instruments response (no dex field exists)."""
    return str(coin).upper().startswith("XYZ:")


# ═══════════════════════════════════════════════════════════════
# UNIVERSE FETCH — v6.1: ALL crypto perps with OI > $3M, no top-N truncation
# ═══════════════════════════════════════════════════════════════

def fetch_all_assets(ctx, inputs):
    """Every HL crypto perp with OI > minOiUsd (XYZ banned). Ported verbatim
    from v2 fetch_all_assets, with the XYZ ban switched from the (absent) dex
    field to the real 'XYZ:' name prefix. Sorted by OI desc (v2 parity)."""
    min_oi = float(inputs.get("minOiUsd", scoring.MIN_OI_USD))
    raw = _read(ctx, "market_list_instruments", {})
    if not raw:
        return []
    instruments = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return []

    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = inst.get("coin") or inst.get("name", "")
        # CASE-PRESERVED: market_list_instruments is the source of truth for exact
        # coin-name casing, and HL names are CASE-SENSITIVE — the 1000x names carry
        # a lowercase k (kPEPE/kSHIB/kBONK) and `KPEPE` is rejected as
        # INVALID_ARGUMENT. This symbol is passed straight to market_get_asset_data
        # and emitted as the signal asset, so upper-casing it here silently
        # no-traded every k-denominated name. Comparisons upper-case at their site.
        coin = str(coin) if coin else ""
        if not coin:
            continue
        if _is_xyz(coin):                          # XYZ ban (name-prefix; see module docstring)
            continue
        if inst.get("is_delisted"):                # PORT-ADD: drop delisted (harmless; stale ctx)
            continue
        # v1.3: funding/OI/price are nested in `context`, not top-level.
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = scoring._f(ctx_block.get("openInterest", inst.get("openInterest", 0)))
        mark_px = scoring._f(ctx_block.get("markPx", ctx_block.get("midPx",
                             inst.get("markPx", inst.get("midPx", 0)))))
        funding = scoring._f(ctx_block.get("funding", inst.get("funding", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if oi_usd >= min_oi:
            assets.append({
                "coin": coin,
                "oi": oi,
                "oi_usd": oi_usd,
                "price": mark_px,
                "funding": funding,
            })
    assets.sort(key=lambda x: x["oi_usd"], reverse=True)
    return assets


# ═══════════════════════════════════════════════════════════════
# SM POSITIONING MAP + BTC 4h MACRO — one MCP call, shared across all assets
# ═══════════════════════════════════════════════════════════════

def fetch_sm_positioning_map(ctx, inputs):
    """Returns (sm_map, btc_p4h) where sm_map = {coin: (long_pct, trader_count)}
    for crypto markets and btc_p4h is BTC's 4h price change percent (macro gate).

    Ported verbatim from v2 fetch_sm_positioning_map (v7.0/v7.1): ONE call per
    scan; BTC 4h move extracted from the same response (no extra MCP cost)."""
    limit = int(inputs.get("smLimit", 200))
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return {}, 0.0
    sm = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(sm, dict):
        sm = sm.get("markets", sm.get("leaderboard", sm))
    if isinstance(sm, dict):
        sm = sm.get("markets", [])
    if not isinstance(sm, list):
        return {}, 0.0

    out = {}
    btc_p4h = 0.0
    for m in sm:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not token or _is_xyz(token):            # v2: skip dex=="xyz"; live shape -> name prefix
            continue
        # v7.1: capture BTC's 4h move from the same scan (macro gate input)
        if token == "BTC":
            btc_p4h = scoring._f(m.get("token_price_change_pct_4h",
                                 m.get("price_change_4h", 0)))
        direction = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        trader_count = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        if direction == "long":
            out[token] = (pct * 100, trader_count)
        elif direction == "short":
            out[token] = ((1 - pct) * 100, trader_count)
    return out, btc_p4h


def fetch_candles(ctx, coin):
    """1h + 4h candle lists for `coin` (exhaustion inputs). READ-GUARDED.
    Ported from v2 detect_exhaustion's market_get_asset_data call."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["1h", "4h"],
        "include_funding": True,
        "include_order_book": False,
    })
    if not md:
        return [], []
    if isinstance(md, dict) and md.get("success") is False:
        return [], []
    inner = md.get("data", md) if isinstance(md, dict) else {}
    candles = (inner.get("candles", {}) or {}) if isinstance(inner, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _held_assets(ctx):
    """Currently-held coins (both sub-DEX views) so we never emit on a coin the
    runtime already holds — belt-and-suspenders alongside the runtime cooldown.
    READ-GUARDED. A failed read returns [] (runtime per_asset_cooldown is the
    safety floor). Ported from v2 fetch_held_assets / push_signal held-skip."""
    if not getattr(ctx, "wallet", None):
        return []
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    held = []
    if isinstance(data, dict):
        for section in ("main", "xyz"):
            s = data.get(section, {})
            if not isinstance(s, dict):
                continue
            for ap in s.get("assetPositions", []) or []:
                pos = ap.get("position", ap) if isinstance(ap, dict) else {}
                if scoring._f(pos.get("szi", 0)) != 0:
                    c = str(pos.get("coin", "")).upper()
                    if c:
                        held.append(c)
    return held


# ═══════════════════════════════════════════════════════════════
# PERSISTENCE LEDGER (ctx.state) — port of v2 crowding-history.json
# ═══════════════════════════════════════════════════════════════

def _load_state(ctx):
    """Return (history, cooldowns) from the last clean tick. history tracks
    crowding persistence per coin; cooldowns tracks per-asset emit timestamps."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}
    last = ctx.state.last() or {}
    hist = last.get("history", {})
    cools = last.get("cooldowns", {})
    return (dict(hist) if isinstance(hist, dict) else {},
            dict(cools) if isinstance(cools, dict) else {})


def _check_persistence(history, coin, crowd_score, now, min_persist_hours):
    """Track how long crowding has been elevated. Returns (persisted, hours, peak).
    Ported verbatim from v2 check_persistence (resets belowThresholdCount on a
    hit; tracks peakScore; persisted once hours >= MIN_PERSIST_HOURS)."""
    if coin not in history:
        history[coin] = {
            "firstSeen": now,
            "ts": now,
            "peakScore": crowd_score,
            "belowThresholdCount": 0,
        }
        return False, 0, crowd_score

    entry = history[coin]
    hours = (now - scoring._f(entry.get("ts", now))) / 3600
    if crowd_score > entry.get("peakScore", 0):
        entry["peakScore"] = crowd_score
    entry["belowThresholdCount"] = 0
    return hours >= min_persist_hours, hours, entry.get("peakScore", crowd_score)


def _mark_below_threshold(history, coin, tolerance):
    """Mark a coin below threshold. Returns True if persistence should be cleared
    (exceeded tolerance). v5.3: 2 consecutive below-threshold scans before clear.
    Ported verbatim from v2 mark_below_threshold."""
    if coin not in history:
        return True  # nothing to track
    entry = history[coin]
    below_count = entry.get("belowThresholdCount", 0) + 1
    entry["belowThresholdCount"] = below_count
    return below_count > tolerance


def _is_asset_cooled_down(cooldowns, coin, now, cooldown_minutes):
    """v2 is_asset_cooled_down: True if last emit was within cooldown_minutes."""
    entry = cooldowns.get(coin)
    if not entry:
        return False
    last_emit = scoring._f(entry.get("emittedTimestamp", 0))
    return ((now - last_emit) / 60) < cooldown_minutes


# ═══════════════════════════════════════════════════════════════
# SIZING
# ═══════════════════════════════════════════════════════════════

def _coerce_tiers(tiers):
    """Accept the runtime-yaml tier shape [[min_score, leverage], ...] and convert
    to the dict form scoring.get_leverage_for_score expects. None -> v2 defaults."""
    if not tiers:
        return None
    out = []
    for t in tiers:
        if isinstance(t, dict):
            out.append(t)
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            out.append({"min_score": t[0], "leverage": t[1]})
    return out or None


def scan(inputs, ctx):
    now = time.time()
    min_crowding = float(inputs.get("minCrowdingScore", scoring.MIN_CROWDING_SCORE))
    min_persist_hours = float(inputs.get("minPersistHours", scoring.MIN_PERSIST_HOURS))
    tolerance = int(inputs.get("belowThresholdTolerance", scoring.BELOW_THRESHOLD_TOLERANCE))
    min_ex_score = float(inputs.get("minExhaustionScore", scoring.MIN_EXHAUSTION_SCORE))
    min_ex_signals = int(inputs.get("minExhaustionSignals", scoring.MIN_EXHAUSTION_SIGNALS))
    min_combined = float(inputs.get("minCombinedScore", scoring.MIN_COMBINED_SCORE))
    min_funding_ann = float(inputs.get("minFundingAnnualizedPct", scoring.MIN_FUNDING_ANNUALIZED_PCT))
    macro_gate = float(inputs.get("macroGateBtc4hPct", scoring.MACRO_GATE_BTC_4H_PCT))
    cooldown_minutes = float(inputs.get("assetCooldownMinutes", scoring.ASSET_COOLDOWN_MINUTES))
    margin_pct = float(inputs.get("marginPct", 25))             # PERCENT in (0,100]
    if margin_pct <= 1.0:                                       # defensive: a pasted fraction -> percent
        margin_pct *= 100.0
    tiers = _coerce_tiers(inputs.get("leverageTiers"))

    history, cooldowns = _load_state(ctx)

    def _persist(result=None):
        if ctx.state is None:
            return
        rec = {"history": history, "cooldowns": cooldowns}
        if result is not None:
            rec["result"] = result
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[owl.scan] WARNING: state append failed; persistence/cooldown may "
                  f"reset next tick: {exc!r}", file=sys.stderr)

    # 1. Universe
    assets = fetch_all_assets(ctx, inputs)
    if not assets:
        print("[owl.scan] market_list_instruments empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe"})
        return []

    # 2. SM positioning map + BTC 4h macro (one call)
    sm_map, btc_p4h = fetch_sm_positioning_map(ctx, inputs)

    # 3. MACRO_TREND_GATE — fades unsafe in trending regimes (v7.1, VERBATIM)
    if abs(btc_p4h) > macro_gate:
        print(f"[owl.scan] MACRO_GATE — BTC 4h {btc_p4h:+.2f}% > {macro_gate}% — "
              f"fades unsafe; standing down (scanned {len(assets)})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "macro", "btc_p4h": round(btc_p4h, 3),
                  "totalAssets": len(assets), "smCovered": len(sm_map)})
        return []

    # 4. Score crowding per asset + update persistence ledger
    crowding_results = []
    for asset in assets:
        coin = asset["coin"]
        # sm_map is keyed upper-case (see the token parse above); `coin` is now
        # case-preserved, so upper-case at the lookup to keep the join intact.
        sm_long_pct, sm_count = sm_map.get(coin.upper(), (50, 0))
        crowd_score, crowd_direction, details = scoring.score_crowding(
            asset, sm_long_pct, sm_count, min_funding_ann)

        if crowd_score >= min_crowding and crowd_direction:
            persisted, hours, peak = _check_persistence(
                history, coin, crowd_score, now, min_persist_hours)
            crowding_results.append({
                "asset": coin,
                "crowd_score": crowd_score,
                "crowd_direction": crowd_direction,
                "details": details,
                "asset_data": asset,
                "sm_long_pct": sm_long_pct,
                "sm_tilt": abs(sm_long_pct - 50),
                "persisted": persisted,
                "hours": hours,
                "peak_score": peak,
            })
        else:
            if _mark_below_threshold(history, coin, tolerance):
                history.pop(coin, None)

    # 5. Filter to persisted candidates only (>= min_persist_hours above floor)
    persisted = [c for c in crowding_results if c["persisted"]]
    if not persisted:
        print(f"[owl.scan] WAITING — {len(crowding_results)} above crowding floor, none "
              f"persisted >={min_persist_hours:.0f}h (scanned {len(assets)})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_persisted",
                  "totalAssets": len(assets), "smCovered": len(sm_map),
                  "crowding_above_floor": len(crowding_results), "btc_p4h": round(btc_p4h, 3)})
        return []

    # 6. Detect exhaustion only for persisted candidates (saves MCP calls)
    candidates = []
    for c in persisted:
        coin = c["asset"]
        crowd_dir = c["crowd_direction"]
        candles_1h, candles_4h = fetch_candles(ctx, coin)
        ex_score, ex_signals, p4h, rsi = scoring.detect_exhaustion(
            candles_1h, candles_4h, crowd_dir)
        if ex_score < min_ex_score or len(ex_signals) < min_ex_signals:
            continue
        combined = c["crowd_score"] + ex_score
        if combined < min_combined:
            continue
        # Per-asset emit cooldown (defense-in-depth alongside runtime cooldown)
        if _is_asset_cooled_down(cooldowns, coin, now, cooldown_minutes):
            continue

        funding_ann = scoring.funding_annualized_pct(c["asset_data"]["funding"])
        reasons = list(c["details"]) + ex_signals + [f"crowding has held for {c['hours']:.1f}h"]
        candidates.append({
            "asset": coin,
            "crowd_direction": crowd_dir,
            "fade_direction": scoring.fade_direction(crowd_dir),
            "crowding_score": c["crowd_score"],
            "exhaustion_score": ex_score,
            "combined_score": combined,
            "persistence_hours": c["hours"],
            "peak_crowding_score": c["peak_score"],
            "funding_ann": funding_ann,
            "sm_long_pct": c["sm_long_pct"],
            "sm_tilt": c["sm_tilt"],
            "oi_usd": c["asset_data"]["oi_usd"],
            "price_chg_4h": p4h,
            "rsi": rsi,
            "exhaustion_signals": ex_signals,
            "reasons": reasons,
        })

    if not candidates:
        print(f"[owl.scan] WAITING — {len(persisted)} persisted but no exhaustion "
              f"confluence >= {min_combined:.0f} (scanned {len(assets)})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_exhaustion",
                  "totalAssets": len(assets), "smCovered": len(sm_map),
                  "persisted": len(persisted), "btc_p4h": round(btc_p4h, 3)})
        return []

    # 7. Pick the single highest-scoring candidate (v2 candidates[:1])
    candidates.sort(key=lambda c: c["combined_score"], reverse=True)
    held = {h.upper() for h in _held_assets(ctx)}

    best = None
    for c in candidates:
        if c["asset"].upper() in held:                  # v2 push_signal held-asset skip
            continue
        best = c
        break

    if best is None:
        print(f"[owl.scan] SKIP — top candidates all already held {sorted(held)}",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "held",
                  "candidates": len(candidates), "held": sorted(held)})
        return []

    leverage = scoring.get_leverage_for_score(best["combined_score"], tiers)
    cooldowns[best["asset"]] = {"emittedTimestamp": now}    # v2 mark_asset_emitted

    result = {"ts": now, "emitted": True, "gate": "pass", "coin": best["asset"],
              "direction": best["fade_direction"], "crowdDirection": best["crowd_direction"],
              "score": best["combined_score"], "crowdingScore": best["crowding_score"],
              "exhaustionScore": best["exhaustion_score"], "leverage": leverage,
              "marginPct": margin_pct, "persistenceHours": round(best["persistence_hours"], 2),
              "btc_p4h": round(btc_p4h, 3), "candidates": len(candidates)}
    print(f"[owl.scan] EMIT {best['asset']} {best['fade_direction']} (fade {best['crowd_direction']}) "
          f"combined={best['combined_score']} crowd={best['crowding_score']} ex={best['exhaustion_score']} "
          f"persist={best['persistence_hours']:.1f}h {leverage}x margin={margin_pct:.0f}% | "
          f"{best['reasons']}", file=sys.stderr)
    _persist(result)

    return [{
        "asset": best["asset"],
        "direction": best["fade_direction"],            # OPPOSITE of the crowd — Owl is contrarian
        "marginPct": margin_pct,                         # SIZING INTENT — PERCENT (0,100]; runtime sizes USD
        "leverage": leverage,                            # conviction-scaled 7/8/10 (10x cap); runtime clamps
        "data": {
            "score": best["combined_score"],
            "leverage": leverage,
            "crowdDirection": best["crowd_direction"],
            "crowdingScore": best["crowding_score"],
            "exhaustionScore": best["exhaustion_score"],
            "persistenceHours": round(best["persistence_hours"], 2),
            "fundingAnnualizedPct": round(best["funding_ann"], 2),
            "smTilt": round(best["sm_tilt"], 2),
            "smLongPct": round(best["sm_long_pct"], 2),
            "oiUsd": round(best["oi_usd"], 2),
            "priceChg4hPct": round(best["price_chg_4h"], 3),
            "rsi4h": round(best["rsi"], 1) if best.get("rsi") is not None else 0,
            "peakCrowdingScore": best["peak_crowding_score"],
            "exhaustionSignals": " | ".join(best.get("exhaustion_signals", [])),
            "reasons": best["reasons"],
        },
    }]
