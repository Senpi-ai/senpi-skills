"""FALCON — supervised scanner (Runtime 3.0 port of the v2 FALCON producer).

UNIVERSE SCANNER, emit-one. A faithful port of the v2 "Conversion-Event
Momentum" strategy (falcon-producer.py v1.0.1 / SKILL.md v1.0.0): trade the
moment a Hyperliquid XYZ Pre-IPO Perpetual (IPOP) CONVERTS to a standard equity
perp (funding jumps ~100x, the leverage cap lifts, the trade.xyz Discovery-Bounds
throttle is removed), riding the post-conversion price-discovery momentum.

Per tick it:
  1. READ-GUARDED market_list_instruments(dex="xyz") -> classify every xyz name
     IPOP vs STANDARD by funding signature (|funding|<=ipopFundingMaxAbs AND
     max_leverage<=ipopMaxLeverageCap = IPOP, else STANDARD).
  2. Compare against the prior-tick class cache (ctx.state) -> detect
     IPOP->STANDARD flips. A first sighting (no prior class) is NEVER a flip.
  3. A flip stamps the name into a conversionWindowHours eligibility window
     (also in ctx.state) so momentum that develops over hours/days stays
     tradeable. Stale stamps are pruned.
  4. Inside the window, require confirmed 1h-candle momentum
     (|move over momentumLookbackBars| >= minMomentumPct) and SM agreement
     bonus; score via the pure `scoring.build_thesis`.
  5. Emit ONE signal for the single highest-scoring candidate clearing minScore
     — a marginPct sizing INTENT plus a leverage clamped to the post-conversion
     cap and MAX_LEVERAGE.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the
runtime sizes the dollars, owns the cooldowns/risk gates/slots, and trails the
DSL exit. Held-asset suppression and per-tick signal dedup live in ctx.state.

FIDELITY NOTES vs the v2 producer (falcon-producer.py v1.0.1):
  - v2 persisted THREE on-disk JSON caches: the recent-signals race-window
    dedup (recent-signals.json), the instrument-class cache (instrument-
    class.json), and the conversion-window cache (conversions.json). All three
    are ported into a SINGLE ctx.state record per tick:
      {"signaled": {coin: ts}, "classes": {name: cls}, "conversions": {name: ts},
       "result": {...}}.
    Same TTL/window semantics; transactional rollback replaces the atomic-write
    pattern. The very first tick seeds the class cache with NO flips (verbatim).
  - v2 main() ran `reconcile_conversions` UNCONDITIONALLY every tick (persist the
    new class cache + stamp flips) even before any account/value gate. Preserved:
    class-cache reconciliation runs every clean tick via the persisted state, so
    flips are never missed while we wait for momentum.
  - v2 fetch_sm_direction read leaderboard_get_markets PER candidate. Preserved
    (read-guarded); SM is a score bonus, not a gate (sparse on fresh listings),
    so an SM read failure degrades to NEUTRAL/no-bonus, never skips the tick.
  - v2 margin was a FRACTION (0.15) * account_value -> marginUsd. This port emits
    the PERCENT (15) as `marginPct`; the runtime sizes (marginPct/100)*
    withdrawable. Defensive <=1.0 fraction->percent guard applied (koala/dire
    pattern). Leverage clamp min(config, post-conversion cap, 10) is verbatim.
  - DROPPED (read-only scan() cannot mutate): nothing — v2 Falcon had NO
    order-lifecycle management (no cancel_order / resting-order purge). The
    producer NEVER closed positions; the DSL owns exits, ported verbatim.
  - v2's "no account value -> skip" and "held-asset -> never re-emit" gates are
    preserved; the held check reads both sub-DEX views (dual-DEX equity collapse
    via max(), never sum()).
"""

import sys
import time

import scoring

_DEFAULT_TTL = 240            # 4min — mirror v2 RECENT_SIGNAL_TTL_SEC (held-asset race-fix)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick (per the scan contract, ANY exception -> []). Returns
    None on failure so the existing degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[falcon.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# Universe scan + conversion detection
# ═══════════════════════════════════════════════════════════════

def scan_instruments(ctx, inputs):
    """READ-GUARDED port of v2 scan_instruments: pull the xyz instrument list and
    classify each IPOP vs STANDARD. Returns
    {name: {"class", "max_leverage", "funding", "vol_usd"}} or {} on read failure."""
    ipop_funding_max = float(inputs.get("ipopFundingMaxAbs", scoring.DEFAULT_IPOP_FUNDING_MAX))
    ipop_lev_cap = int(inputs.get("ipopMaxLeverageCap", scoring.DEFAULT_IPOP_LEV_CAP))

    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw:
        return {}
    if isinstance(raw, dict) and raw.get("success") is False:
        return {}
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return {}

    out = {}
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = str(inst.get("name", ""))
        if not name.startswith("xyz:"):
            continue
        if inst.get("is_delisted", False):
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(scoring._f(ctx_block.get("funding", 0)))
        try:
            max_lev = int(inst.get("max_leverage", 5))
        except (TypeError, ValueError):
            max_lev = 5
        out[name] = {
            "class": scoring.classify_instrument(funding_abs, max_lev, ipop_funding_max, ipop_lev_cap),
            "max_leverage": max_lev,
            "funding": funding_abs,
            "vol_usd": scoring._f(ctx_block.get("dayNtlVlm", 0)),
        }
    return out


def reconcile_conversions(scan_map, prev_classes, prev_conversions, inputs, now):
    """Port of v2 reconcile_conversions, over ctx.state instead of disk caches.

    Compares this scan against the prior class cache, stamps any new
    IPOP->STANDARD flip into the conversion window, prunes stale stamps. Returns
    (new_classes, live_conversions, names_in_window)."""
    window_hours = float(inputs.get("conversionWindowHours", scoring.DEFAULT_CONVERSION_WINDOW_HOURS))

    new_classes = {}
    conversions = dict(prev_conversions)
    for name, info in scan_map.items():
        curr_class = info["class"]
        prev_class = prev_classes.get(name)
        if scoring.detect_conversion(prev_class, curr_class):
            conversions[name] = now
        new_classes[name] = curr_class

    cutoff = now - (window_hours * 3600.0)
    live = {k: v for k, v in conversions.items() if v >= cutoff}
    return new_classes, live, set(live.keys())


# ═══════════════════════════════════════════════════════════════
# Data fetchers (READ-GUARDED)
# ═══════════════════════════════════════════════════════════════

def fetch_candles(ctx, asset):
    """1h candles for `asset`. READ-GUARDED. Ported from v2 fetch_candles."""
    data = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": "xyz",
    })
    if not data:
        return []
    if isinstance(data, dict) and data.get("success") is False:
        return []
    d = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(d, dict):
        return []
    return d.get("candles", {}).get("1h", []) or []


def fetch_sm_direction(ctx, asset):
    """Net smart-money lean for `asset` from leaderboard_get_markets. READ-GUARDED.
    Returns (direction, tilt_pct) or (None, 0.0). Ported verbatim from v2;
    SM is a score bonus, so a failed read degrades to (None, 0.0)."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw:
        return None, 0.0
    if isinstance(raw, dict) and raw.get("success") is False:
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def _held_assets(ctx):
    """Open positions across both sub-DEX views so we never emit on a coin the
    runtime already holds. READ-GUARDED. Ported from v2 cfg.get_positions,
    including the (account_value <= 0 -> skip) gate and the corrupt-read sanity
    guard (margin in use + empty positions -> treat as no account value)."""
    if not getattr(ctx, "wallet", None):
        return 0.0, []
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    held, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        # one wallet, two sub-DEX views -> count equity ONCE via max() (summing
        # double-counts the shared free balance -> 2x sizing).
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring._f(pos.get("szi", 0)) != 0:
                c = str(pos.get("coin", "")).upper()
                if c:
                    held.append(c)

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim): a corrupt
    # clearinghouse read can report margin/notional IN USE while returning an
    # EMPTY positions list; sizing or running the held dedup off that re-enters
    # held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not held:
        print("[falcon.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, held


# ═══════════════════════════════════════════════════════════════
# scan() — detect conversions, score momentum, emit the best one
# ═══════════════════════════════════════════════════════════════

def scan(inputs, ctx):
    now = time.time()
    min_score = int(inputs.get("minScore", scoring.DEFAULT_MIN_SCORE))
    config_leverage = int(inputs.get("leverage", scoring.DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value
    # <= 1 (an operator who pasted the v2 FRACTION 0.15) into a PERCENT so it
    # never silently sizes ~100x small (runtime sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", scoring.DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[falcon.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    # ── load cross-tick state (the v2 class/conversion/signal caches) ──
    last = (ctx.state.last() or {}) if ctx.state else {}
    prev_classes = dict(last.get("classes") or {})
    prev_conversions = {k: float(v) for k, v in (last.get("conversions") or {}).items()
                        if isinstance(v, (int, float))}
    signaled = {k: float(v) for k, v in (last.get("signaled") or {}).items()
                if isinstance(v, (int, float)) and (now - float(v)) < (ttl * 4)}

    def _persist(classes, conversions, result):
        if ctx.state is None:
            return
        rec = {"signaled": signaled, "classes": classes,
               "conversions": conversions, "result": result}
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[falcon.scan] WARNING: state append failed; next tick may miss a "
                  f"flip or re-emit a suppressed signal: {exc!r}", file=sys.stderr)

    # ── account / held gate (v2 main(): no account value -> skip) ──
    account_value, held = _held_assets(ctx)
    held_set = {h.upper() for h in held}
    if account_value <= 0:
        # do NOT advance the class cache off a failed/empty account read; keep
        # the prior caches so we don't lose flip history (verbatim intent).
        print("[falcon.scan] no account value / clearinghouse read failed — skipping tick",
              file=sys.stderr)
        _persist(prev_classes, prev_conversions,
                 {"ts": now, "emitted": False, "gate": "no_account_value"})
        return []

    # ── classify the xyz universe + reconcile conversions EVERY tick ──
    scan_map = scan_instruments(ctx, inputs)
    if not scan_map:
        # market_list_instruments degraded to empty/cached: do not flip the cache
        # to empty (that would erase known IPOP classes and miss the next flip).
        print("[falcon.scan] market_list_instruments empty/failed — preserving caches, no signal",
              file=sys.stderr)
        # still prune the conversion window so stale stamps expire.
        window_hours = float(inputs.get("conversionWindowHours", scoring.DEFAULT_CONVERSION_WINDOW_HOURS))
        cutoff = now - (window_hours * 3600.0)
        live = {k: v for k, v in prev_conversions.items() if v >= cutoff}
        _persist(prev_classes, live,
                 {"ts": now, "emitted": False, "gate": "no_universe"})
        return []

    new_classes, live_conversions, in_window = reconcile_conversions(
        scan_map, prev_classes, prev_conversions, inputs, now)

    if not in_window:
        ipops_now = sorted(n for n, i in scan_map.items() if i["class"] == "IPOP")
        print(f"[falcon.scan] WAITING — no IPOP->equity conversion inside the window "
              f"(tracked={len(scan_map)}, ipops_now={len(ipops_now)})", file=sys.stderr)
        _persist(new_classes, live_conversions,
                 {"ts": now, "emitted": False, "gate": "no_conversion",
                  "tracked": len(scan_map), "ipops_now": ipops_now,
                  "held": sorted(held_set)})
        return []

    # ── score every in-window candidate (held + recently-signaled filtered
    #    BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    for name in sorted(in_window):
        if name.upper() in held_set:
            continue
        last_sig = signaled.get(name.upper())
        if last_sig is not None and (now - last_sig) < ttl:
            continue
        info = scan_map.get(name)
        if not info:
            continue  # converted name no longer listed
        candles = fetch_candles(ctx, name)
        if len(candles) < 8:
            continue
        sm_dir, sm_tilt = fetch_sm_direction(ctx, name)
        th = scoring.build_thesis(name, info, candles, sm_dir, sm_tilt, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    if not candidates:
        print(f"[falcon.scan] WAITING — conversion(s) in window but no confirmed momentum "
              f">= minScore={min_score} (window={sorted(in_window)})", file=sys.stderr)
        _persist(new_classes, live_conversions,
                 {"ts": now, "emitted": False, "gate": "no_momentum",
                  "conversions_in_window": sorted(in_window), "held": sorted(held_set)})
        return []

    # ── pick the single highest-scoring candidate (v2 emits only `best`) ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    bu = best["coin"].upper()

    leverage = scoring.leverage_for(config_leverage, best.get("max_leverage_cap", scoring.MAX_LEVERAGE))
    signaled[bu] = now

    result = {"ts": now, "emitted": True, "gate": "pass", "coin": best["coin"],
              "direction": best["direction"], "score": best["score"],
              "leverage": leverage, "marginPct": margin_pct,
              "momentum_pct": best["momentum_pct"],
              "conversions_in_window": sorted(in_window),
              "candidates": len(candidates), "reasons": best["reasons"]}
    print(f"[falcon.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
          f"{leverage}x margin={margin_pct}% mom={best['momentum_pct']:+.1f}% | {best['reasons'][:5]}",
          file=sys.stderr)
    _persist(new_classes, live_conversions, result)

    return [{
        "asset": best["coin"],
        "direction": best["direction"],
        "marginPct": margin_pct,                 # SIZING INTENT — PERCENT (0,100]; runtime sizes USD
        "leverage": leverage,                    # clamped to post-conversion cap + 10x; runtime clamps to venue max
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": best["reasons"],
            "momentumPct": best.get("momentum_pct") or 0.0,
            "smDirection": best.get("sm_direction") or "NONE",
            "smTiltPct": best.get("sm_tilt_pct") or 0.0,
            "volumeTrendPct": best.get("volume_trend_pct") or 0.0,
            "conversionEvent": True,
            "heldAssets": sorted(held_set),
        },
    }]
