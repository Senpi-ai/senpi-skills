"""CARACAL (BREAKOUT book) — supervised scanner (Runtime 3.0 port of the v2
caracal-producer.py, CARACAL_LEG=breakout).

Volatility compression->expansion on the liquid main-DEX CRYPTO universe.
It trades MOVEMENT, not a directional view: each name that breaks its recent
range FROM a low-volatility coil is taken LONG (break up) or SHORT (break
down). One of two books; the CATALYST book runs the identical engine on XYZ
(see ../../catalyst/). Per tick scan() does:

  1. read the wallet clearinghouse — account value, held names, free margin
     (dual-DEX equity via max(), NEVER sum — two views of one cross-margined
     wallet; v2 cfg.get_positions, incl. the read-sanity $0/funding guard).
  2. build the live CRYPTO universe (market_list_instruments, top
     universeMaxNames by 24h volume, relative-to-market liquidity floor — NO
     hardcoded $).
  3. per non-held / non-recently-signaled name, fetch 1h+4h candles and score
     via the pure scoring.score_vol_breakout (breakout + coil + surge + 4h
     agreement); keep candidates >= minScore.
  4. emit up to min(open_slots, affordable) candidates as marginPct sizing
     INTENTS (PERCENT) + per-name venue-clamped leverage; the runtime sizes
     the dollars, owns slots/cooldowns/risk gates, and trails the DSL exit.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position.

FIDELITY NOTES vs caracal-producer.py v1.0.0 (CARACAL_LEG=breakout):
  - v2 stored marginPct=0.18 as a FRACTION and computed marginUsd =
    account_value * 0.18 itself. This port emits `marginPct`=18 (PERCENT) at
    the top level; the runtime sizes (marginPct/100)*withdrawable. A defensive
    "<=1.0 means a pasted fraction -> ×100" guard is applied. TIERS/CLAMPS
    otherwise verbatim.
  - v2's recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=180) -> ctx.state
    dedup map with the same TTL + 4x-TTL prune semantics.
  - v2's free-margin AFFORDABILITY cap (free_margin / (margin_usd*1.1)) is
    preserved as read-only math so we never emit more entries than the wallet
    can fund (avoids insufficient-funds create spam). It uses an absolute
    marginUsd derived from account_value*fraction for the affordability count
    ONLY; the emitted wire size remains the PERCENT intent.
  - v2's per-tick `min_notional` venue/equity floor is preserved: a candidate
    whose notional (margin_usd*leverage) is below the floor is dropped.
  - DROPPED (mutations the read-only scan() cannot perform; the runtime owns
    them): nothing — the v2 producer had NO order-lifecycle management
    (no cancel_order / has_resting_orders / stale-order purge). push_signal +
    record_signal are replaced by the return list + ctx.state.
  - v2 emitted up to `open_slots` best candidates; preserved (emit-all up to
    the slot/affordability cap, runtime applies the ceiling).
"""

import sys
import time

import scoring

_DEFAULT_TTL = 180        # v2 RECENT_SIGNAL_TTL_SEC — held-asset race-fix dedup window
_WANT_XYZ = False         # BREAKOUT book = main-DEX crypto


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must
    NOT roll back the whole tick (the contract rolls a raised exception back to
    []). Returns None so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad read must not kill the universe tick
        print(f"[caracal-breakout.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _dex_for(asset):
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


# ── account / positions (READ-GUARDED; ported from v2 cfg.get_positions) ──

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin).

    The 'main' and 'xyz' clearinghouse sections are TWO VIEWS of ONE
    cross-margined wallet — accountValue is taken ONCE via max() across
    sections, NEVER summed (summing double-counts the shared balance -> 2x
    sizing). assetPositions ARE per-sub-DEX so they are enumerated across both.
    Includes the v2 read-sanity guard: margin in use + empty positions list =
    corrupt read -> skip the tick (avoids pyramiding / mis-sizing)."""
    if not getattr(ctx, "wallet", None):
        return 0.0, [], 0.0
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = _unwrap(ch)
    if not isinstance(data, dict):
        return 0.0, [], 0.0

    positions, account_value, committed = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s.get("marginSummary"), dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            margin = scoring._f(pos.get("marginUsed", 0))
            committed += margin
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": margin,
            })

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim): margin in
    # use while positions is empty == corrupt read; skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[caracal-breakout.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0

    free_margin = max(0.0, account_value - committed)
    return account_value, positions, free_margin


# ── universe (READ-GUARDED; ported from v2 get_universe_meta + build_universe) ──

def _build_universe(ctx, inputs):
    """Liquid names on the leg's DEX (crypto), capped to universeMaxNames by 24h
    volume, then a relative-to-market liquidity floor (NO hardcoded $). Returns
    (universe[str], meta_map{name->{max_leverage}}). Ported verbatim from v2
    get_universe_meta + build_universe."""
    max_names = int(inputs.get("universeMaxNames", scoring.UNIVERSE_MAX_NAMES))
    pct = float(inputs.get("volFloorPctOfMedian", scoring.VOL_FLOOR_PCT_OF_MEDIAN))

    raw = _read(ctx, "market_list_instruments", {})
    if not raw:
        return [], {}
    data = _unwrap(raw)
    insts = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(insts, list):
        return [], {}

    meta_map, pool = {}, []
    for inst in insts:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        name = inst.get("name") or ctx_block.get("coin")
        if not name or not isinstance(name, str):
            continue
        is_xyz = name.lower().startswith("xyz:")
        if is_xyz != _WANT_XYZ:          # BREAKOUT = crypto only (drop XYZ)
            continue
        vol = scoring._f(ctx_block.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        if vol <= 0:
            continue
        meta_map[name] = {"max_leverage": inst.get("max_leverage", inst.get("maxLeverage"))}
        meta_map[name.upper()] = meta_map[name]
        pool.append((name, vol))

    if not pool:
        return [], {}
    pool.sort(key=lambda x: x[1], reverse=True)
    pool = pool[:max_names]
    # relative-to-market liquidity gate (NO hardcoded $): keep names whose 24h
    # volume is >= volFloorPctOfMedian of the top-N cohort's median.
    vols = sorted(v for _, v in pool)
    median = vols[len(vols) // 2]
    floor = pct * median
    universe = [n for n, v in pool if v >= floor]
    return universe, meta_map


def _fetch_candles(ctx, asset):
    """{c1, c4} 1h/4h candle lists for `asset`, or None. READ-GUARDED.
    Ported from v2 fetch_candles."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    })
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = _unwrap(md)
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    if not isinstance(candles, dict):
        return None
    return {"c1": candles.get("1h", []) or [], "c4": candles.get("4h", []) or []}


# ── ctx.state: recent-signal dedup (port of v2 recent-signals-<leg>.json) ──

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


def _norm_margin_pct(raw):
    """Defensive fraction->percent guard (dire/koala pattern): a value <=1.0 was
    almost certainly pasted as a FRACTION (v2 stored 0.18); ×100 -> a PERCENT."""
    mp = float(raw)
    if 0 < mp <= 1.0:
        mp *= 100.0
    return mp


def scan(inputs, ctx):
    now = time.time()
    min_score = float(inputs.get("minScore", scoring.MIN_SCORE))
    margin_pct = _norm_margin_pct(inputs.get("marginPct", scoring.MARGIN_PCT))   # PERCENT (0,100]
    max_lev = int(inputs.get("maxLeverage", scoring.MAX_LEVERAGE))
    max_slots = int(inputs.get("maxSlots", scoring.MAX_SLOTS))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        print("[caracal-breakout.scan] no account value — skipping tick", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    open_slots = max_slots - len(held_assets)
    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[caracal-breakout.scan] WARNING: state append failed; next tick may "
                  f"re-emit a suppressed signal: {exc!r}", file=sys.stderr)

    if open_slots <= 0:
        print(f"[caracal-breakout.scan] WAITING — slots full ({len(held_assets)}/{max_slots}) "
              f"held={held_assets}; DSL manages exits", file=sys.stderr)
        _persist({"ts": now, "emitted": 0, "gate": "slots_full", "held": held_assets})
        return []

    universe, meta_map = _build_universe(ctx, inputs)
    if not universe:
        print("[caracal-breakout.scan] market_list_instruments empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": 0, "gate": "no_universe"})
        return []

    candidates = []
    scanned = 0
    for name in universe:
        if name.upper() in held_set:
            continue
        if _was_recently_signaled(signaled, name, ttl, now):
            continue
        scanned += 1
        md = _fetch_candles(ctx, name)
        if not md:
            continue
        th = scoring.score_vol_breakout(name, md["c1"], md["c4"], inputs)
        if th and th["score"] >= min_score:
            th["_meta"] = meta_map.get(name) or meta_map.get(name.upper()) or {}
            candidates.append(th)

    if not candidates:
        print(f"[caracal-breakout.scan] WAITING — no coiled breakout >= minScore {min_score:.0f} "
              f"(scanned {scanned})", file=sys.stderr)
        _persist({"ts": now, "emitted": 0, "gate": "no_candidate", "scanned": scanned,
                  "min_score": min_score, "held": held_assets})
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # AFFORDABILITY cap (v2): never emit more entries than the wallet can FUND.
    # margin_usd is the ABSOLUTE dollar margin for the affordability count only;
    # the emitted wire size is the PERCENT intent (runtime sizes the dollars).
    margin_usd = round(account_value * (margin_pct / 100.0), 2)
    affordable = int(free_margin / (margin_usd * 1.1)) if margin_usd > 0 else 0  # 1.1 = fee/slippage headroom
    min_notional = max(account_value * min_notional_pct, venue_min_notional)
    cap = min(open_slots, affordable)
    to_emit = candidates[:cap]

    out, emitted = [], []
    for th in to_emit:
        leverage = scoring.clamp_leverage(max_lev, (th.get("_meta") or {}).get("max_leverage"))
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        signaled[th["coin"].upper()] = now
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # <=5, venue-clamped; runtime applies it
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": th["direction"],
                "reasons": th["reasons"],
                "trend4h": th.get("trend4h"),
                "squeeze": round(scoring._f(th.get("squeeze")), 3),
                "surge": round(scoring._f(th.get("surge")), 2),
                "heldAssets": held_assets,
            },
        })
        emitted.append({"coin": th["coin"], "direction": th["direction"],
                        "score": th["score"], "leverage": leverage,
                        "squeeze": round(scoring._f(th.get("squeeze")), 2),
                        "surge": round(scoring._f(th.get("surge")), 2)})

    if out:
        summary = ", ".join(f"{e['coin']} {e['direction']} s{e['score']} {e['leverage']}x" for e in emitted)
        print(f"[caracal-breakout.scan] EMIT {len(out)} | {summary} "
              f"marginPct={margin_pct:.0f}% scanned={scanned}", file=sys.stderr)
    else:
        print(f"[caracal-breakout.scan] WAITING — candidates found but none affordable/clear notional "
              f"(scanned {scanned}, free_margin={free_margin:.0f})", file=sys.stderr)

    _persist({"ts": now, "emitted": len(out), "gate": "pass" if out else "unaffordable",
              "scanned": scanned, "candidates": len(candidates), "open_slots": open_slots,
              "account_value": round(account_value, 2), "held": held_assets,
              "emit": emitted})
    return out
