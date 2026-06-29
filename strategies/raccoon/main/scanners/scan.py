"""RACCOON — supervised scanner (Runtime 3.0 port of the v2 RACCOON producer).

Weekend XYZ reconciliation trader. ONLY emits during the trade.xyz no-external-
price window (Fri 22:00 UTC -> Mon 00:00 UTC) when XYZ runs on its internal
30-min EWMA oracle. Per tick it:
  - hard-gates on the weekend window (outside it: emit nothing, one stderr line),
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - builds the XYZ universe via market_list_instruments(dex="xyz") filtered to
    non-delisted names with 24h vol >= minVolUsd and max_leverage >= minMaxLeverage
    (the >=10 floor excludes 5x IPOPs, which are Lemur's territory),
  - for each non-held, non-recently-signaled XYZ name: detects a >= minMoveAbsPct
    48h directional move, confirms smart-money agreement + tilt floor, scores it,
  - emits the SINGLE highest-scoring candidate at/above minScore (v2 main()
    emitted only `best`), sized by a flat margin PERCENT + leverage clamp.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates/slots, and trails the DSL
exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (raccoon-producer.py v1.0.1):
  - market_list_instruments universe filter ported VERBATIM (xyz: prefix,
    non-delisted, dayNtlVlm >= minVolUsd, max_leverage >= minMaxLeverage). The
    live response shape (success/data/instruments/name/max_leverage/is_delisted/
    context.dayNtlVlm) matches the v2 extraction exactly — confirmed against
    market_list_instruments(dex="xyz").
  - in_weekend_window + detect_directional_move + the 5-component scoring table
    ported VERBATIM (scoring.py). The wall clock is read once here (datetime.now)
    and passed to the pure scoring.in_weekend_window so scoring.py stays clock-free.
  - v2 fetch_sm_direction (per-asset leaderboard_get_markets lookup, long_ratio
    derivation) ported VERBATIM as _get_sm_direction. v2 re-fetched the FULL
    leaderboard once PER asset inside build_thesis; this port fetches it ONCE per
    tick and caches a {token -> long/short pct} map, then resolves each asset from
    the cache. Identical thesis values, far fewer reads (universe-sized N -> 1).
    FLAGGED as the only behavioural optimisation.
  - v2 sizing used marginPct (a FRACTION, 0.15 in raccoon-config.json) *
    account_value -> marginUsd. This port uses marginPct=15 (a PERCENT) and emits
    `marginPct`; the runtime sizes (marginPct/100)*withdrawable. Value preserved
    (0.15 -> 15%). Defensive koala-pattern guard: an input <= 1.0 is treated as a
    pasted v2 fraction and x100'd.
  - leverage clamp min(leverage, MAX_LEVERAGE=5) preserved verbatim.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup map
    (same TTL semantics, same 4x-TTL prune horizon).
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - DROPPED v2 order-lifecycle behaviour: NONE. The v2 producer never managed order
    lifecycle (no cancel_order / has_resting_orders / stale-order purge) — push_signal
    is the only mutation and is replaced by the return list, so nothing is dropped.
  - DROPPED: the v2 fixed-time wall-clock gate is now read from the supervisor's tick
    rather than a daemon loop. Behaviour identical (gate evaluated every tick).
"""

import sys
import time
from datetime import datetime, timezone

import scoring

_DEFAULT_RECENT_TTL = 240        # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _read(ctx, name, args):
    """READ-GUARD: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[raccoon.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    if not getattr(ctx, "wallet", None):
        return 0.0, []
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", "")})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[raccoon.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def fetch_weekend_universe(ctx, inputs):
    """XYZ universe via market_list_instruments(dex="xyz"), filtered VERBATIM from
    v2 fetch_weekend_universe: xyz: prefix, non-delisted, dayNtlVlm >= minVolUsd,
    max_leverage >= minMaxLeverage (the >=10 floor excludes 5x IPOPs). READ-GUARDED."""
    min_vol = float(inputs.get("minVolUsd", scoring.DEFAULT_MIN_VOL_USD))
    min_max_lev = int(inputs.get("minMaxLeverage", scoring.DEFAULT_MIN_MAX_LEV))

    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw:
        return []
    if isinstance(raw, dict) and raw.get("success") is False:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []

    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = str(inst.get("name", ""))
        if not name.startswith("xyz:"):
            continue
        if inst.get("is_delisted", False):
            continue
        # Exclude IPOPs (max_leverage < floor) which are Lemur's territory.
        if int(scoring._f(inst.get("max_leverage", 0))) < min_max_lev:
            continue
        cblk = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        vol_usd = scoring._f(cblk.get("dayNtlVlm", 0))
        if vol_usd < min_vol:
            continue
        universe.append({"name": name, "vol_usd": vol_usd})
    return universe


def _fetch_sm_map(ctx):
    """ONE leaderboard_get_markets read -> {TOKEN: (long_pct, short_pct)} cache.

    v2 re-fetched the whole leaderboard once per asset inside build_thesis; this
    port fetches it ONCE per tick and resolves each asset from the cache (same
    thesis values). READ-GUARDED — degrades to {} so SM resolves to (None, 0)
    and every asset's SM-agreement gate fails (emit nothing, never crash)."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw:
        return {}
    if isinstance(raw, dict) and raw.get("success") is False:
        return {}
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not token:
            continue
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        lp, sp = sm_map.get(token, (0.0, 0.0))
        if d == "LONG":
            lp = pct
        elif d == "SHORT":
            sp = pct
        sm_map[token] = (lp, sp)
    return sm_map


def _sm_direction(sm_map, asset):
    """Net smart-money lean for `asset` from the cached SM map. Ported VERBATIM
    from v2 fetch_sm_direction long_ratio derivation. Returns (direction, pct)."""
    entry = sm_map.get(asset.upper())
    if entry is None:
        return None, 0.0
    long_pct, short_pct = entry
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def _asset_candles_1h(ctx, asset):
    """1h candles for `asset` (XYZ dex) or [] on failure. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h candles, no funding, no order book)."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": "xyz",
    })
    if not md:
        return []
    if isinstance(md, dict) and md.get("success") is False:
        return []
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return []
    candles = d.get("candles", {}) or {}
    c1h = candles.get("1h", [])
    return c1h if isinstance(c1h, list) else []


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    now_dt = datetime.now(timezone.utc)
    min_score = float(inputs.get("minScore", scoring.DEFAULT_MIN_SCORE))
    lev_default = int(inputs.get("leverage", scoring.DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # marginPct is a PERCENT in (0,100]. Defensive koala-pattern guard: a value
    # <= 1.0 (operator pasted the v2 FRACTION 0.15) is treated as a fraction and
    # x100'd so it never silently sizes ~100x small.
    margin_pct = float(inputs.get("marginPct", scoring.DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[raccoon.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    def _persist(result=None):
        if ctx.state is None:
            return
        rec = {"signaled": signaled}
        if result is not None:
            rec["result"] = result
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[raccoon.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    # ── HARD GATE: weekend window (Fri 22:00 UTC -> Mon 00:00 UTC) ──
    if not scoring.in_weekend_window(now_dt):
        print("[raccoon.scan] WAITING — OUTSIDE_WEEKEND_WINDOW "
              "(Raccoon only fires Fri 22:00 UTC -> Mon 00:00 UTC)", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "outside_window"})
        return []

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[raccoon.scan] WAITING — no account value", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_account"})
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    universe = fetch_weekend_universe(ctx, inputs)
    if not universe:
        print("[raccoon.scan] WAITING — no XYZ instruments match liquidity/leverage filter",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe", "held": held_assets})
        return []

    sm_map = _fetch_sm_map(ctx)   # one read/tick; SM-agreement gate fails closed if empty

    candidates = []
    scanned = 0
    for inst in universe:
        coin = inst["name"]
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        c1h = _asset_candles_1h(ctx, coin)
        if len(c1h) < 24:
            continue
        sm_dir, sm_tilt = _sm_direction(sm_map, coin)
        th = scoring.build_thesis(coin, c1h, sm_dir, sm_tilt, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    if not candidates:
        print(f"[raccoon.scan] WAITING — in weekend window but no qualifying XYZ move + "
              f"SM agreement (universe={len(universe)}, scanned={scanned})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidate",
                  "universe": len(universe), "scanned": scanned, "held": held_assets})
        return []

    # v2 emitted exactly the single best (highest score).
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    bu = best["coin"].upper()

    # Defense in depth: never emit on a held coin, and dedup the race window.
    if bu in held_set:
        print(f"[raccoon.scan] SKIP {best['coin']} — already held", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "held", "best": best["coin"]})
        return []
    if _was_recently_signaled(signaled, best["coin"], ttl, now):
        print(f"[raccoon.scan] DEDUP_SKIP {best['coin']} — pushed within {ttl:.0f}s (race window)",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "dedup", "best": best["coin"]})
        return []

    # leverage: clamp v2 default into [1, MAX_LEVERAGE=5] (verbatim).
    leverage = min(lev_default, scoring.MAX_LEVERAGE)

    signaled[bu] = now
    result = {"ts": now, "emitted": True, "gate": "pass", "coin": best["coin"],
              "direction": best["direction"], "score": best["score"], "leverage": leverage,
              "marginPct": round(margin_pct, 4), "universe": len(universe),
              "candidates": len(candidates), "held": held_assets, "reasons": best["reasons"]}
    print(f"[raccoon.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
          f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
    _persist(result)

    return [{
        "asset": best["coin"],
        "direction": best["direction"],
        "marginPct": margin_pct,                 # PERCENT in (0,100] — runtime sizes the dollars
        "leverage": leverage,                    # <= 5; runtime clamps to venue max
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": best["reasons"],
            "movePct": best["move_pct"],
            "volRatio": best["vol_ratio"],
            "smDirection": best["sm_direction"],
            "smTiltPct": best["sm_tilt_pct"],
            "weekendWindow": True,
            "heldAssets": held_assets,
        },
    }]
