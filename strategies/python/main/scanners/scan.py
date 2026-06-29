"""PYTHON — supervised scanner (Runtime 3.0 port of the v2 PYTHON producer).

Multi-asset universe, emit-one. A faithful port of the v2 "Patience Hunter"
multi-day-hold agent (SKILL.md v2.0.0 / python-producer.py v2.0.1):

  1. Build the universe — top 50 HL crypto assets by 24h notional volume
     (market_list_instruments), with XYZ + stablecoins banned, OI >= $1M and
     24h notional volume >= $1M floors.
  2. Pull smart-money positioning per coin (leaderboard_get_markets): net
     long/short lean, consensus %, trader_count (>= 30 gate), 15m contribution
     velocity.
  3. For every non-held, non-recently-signaled universe asset, fetch 15m/1h/4h/1d
     candles + funding (market_get_asset_data) and score through the pure,
     multi-gate `scoring.build_thesis` (4h trend != NEUTRAL, 1h == 4h,
     MACRO_TREND_GATE, 15m confirm, SM HARD BLOCK, RSI extremes; then the
     verbatim v2 scoring table + LONG-bias bonus).
  4. Emit ONE signal for the single highest-scoring candidate clearing
     MIN_SCORE (8) — a marginPct sizing INTENT (PERCENT, conviction-tiered
     25/30/40) plus a per-score leverage tier (3/5/7). v2 main() emitted only
     `best`.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the
runtime sizes the dollars, owns cooldowns/risk gates/slots, and trails the DSL
exit. Held-asset suppression and per-tick signal dedup live in ctx.state
(belt-and-suspenders alongside the runtime's per-asset cooldown gate).

FIDELITY NOTES vs python-producer.py v2.0.1:
  - DROPPED v2 order-lifecycle/mutation behavior: there was none beyond
    push_signal (v2 had no cancel_order / resting-order purge). The producer's
    push_signal + the LLM pass-through OPEN_POSITION action are replaced by the
    scan emitting a plain candidate; the runtime executes. The v2 LLM gate was a
    pure pass-through ("honor the signal unless structurally broken"), so the
    OPEN_POSITION action is decision_mode: rule here (gates already applied in
    scan/scoring) — behaviour-equivalent.
  - DROPPED v2 get_safe_leverage (strategy_get_asset_trading_limits read that
    clamped desired leverage to the per-asset HL max). The runtime clamps
    leverage to the venue max at execution, so the extra read is redundant in
    Runtime 3.0; the score-tier leverage (3/5/7, MAX 7) is emitted directly.
    FLAGGED — the emitted leverage may briefly exceed a thin coin's venue cap
    before the runtime clamps it; the cap was always <= the asset max anyway.
  - DROPPED v2 cfg.is_asset_cooled_down (12h per-asset cooldown via a local JSON
    state file). The runtime owns per-asset cooldown via
    risk.guard_rails.per_asset_cooldown_seconds (43200s = 12h, ported below).
    The ctx.state recent-signal dedup is an additional race-window guard.
  - v2 stored margin as a FRACTION (0.25/0.30/0.40 * account_value -> marginUsd);
    this port emits marginPct PERCENT (25/30/40) and the runtime sizes the USD.
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1/tick.
  - v2 recent-signals were implicit (no JSON cache in the producer); this port
    adds a ctx.state dedup map (TTL) as a belt-and-suspenders race-window guard,
    matching the bison/condor ports.
"""

import sys
import time

import scoring

_DEFAULT_TTL = 240            # 4min race-window dedup (matches condor/bison ports)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick (per the scan contract ANY exception -> []). Returns None
    on failure so the existing degrade paths apply (skip asset / empty universe /
    empty SM map -> emit nothing this tick)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[python.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _is_xyz(name):
    """XYZ (HIP-3) equities/commodities are identified by the 'xyz:' name prefix
    (lowercase as returned by the HL combined instruments meta). v2 banned them
    via `name.startswith('xyz:')`; preserved verbatim (case-insensitive here)."""
    return name.lower().startswith("xyz:")


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.
    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip)."""
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
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "direction": "LONG" if szi > 0 else "SHORT"})

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
        print("[python.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def get_universe(ctx, inputs):
    """Top N HL crypto assets by 24h notional volume — ported VERBATIM from v2
    get_universe. Drops XYZ ('xyz:' prefix), stablecoins, OI < $1M floor, and
    24h notional volume < $1M floor; sorts by volume desc; returns top N."""
    universe_size = int(inputs.get("universeSize", scoring.UNIVERSE_SIZE))
    min_oi = float(inputs.get("minOiUsd", scoring.MIN_OI_USD))
    min_vlm = float(inputs.get("minDayNtlVlm", scoring.MIN_DAY_NTL_VLM))
    raw = _read(ctx, "market_list_instruments", {})
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []

    filtered = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        # v2 read inst.get("name"); the live top level is "name". (context also
        # carries "coin"; either resolves to the same casing.)
        name = str(inst.get("name") or inst.get("coin") or "")
        if not name or _is_xyz(name):
            continue
        if name.upper() in scoring.STABLECOINS_BANNED:
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        day_ntl_vlm = scoring._f(ctx_block.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        oi = scoring._f(ctx_block.get("openInterest", inst.get("openInterest", 0)))
        mark_px = scoring._f(ctx_block.get("markPx", inst.get("markPx", 0)))
        oi_usd = oi * mark_px

        if oi_usd < min_oi:
            continue
        if day_ntl_vlm < min_vlm:
            continue

        filtered.append({
            "coin": name,
            "volume": day_ntl_vlm,
            "oi_usd": oi_usd,
            "markPx": mark_px,
            "maxLeverage": int(inst.get("maxLeverage", 10) or 10),
        })
    filtered.sort(key=lambda x: -x["volume"])
    return filtered[:universe_size]


def get_sm_map(ctx, inputs):
    """Per-coin net smart-money lean from leaderboard_get_markets — ported
    VERBATIM from v2 get_sm_map. Returns {TOKEN: (direction, pct, traders,
    cc_15m)}; only tokens with traders >= MIN_TRADER_COUNT (30) and non-zero
    long/short total are included. long_ratio > 58 -> LONG, < 42 -> SHORT, else
    NEUTRAL. READ-GUARDED."""
    min_traders = int(inputs.get("minTraderCount", scoring.MIN_TRADER_COUNT))
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw:
        return {}
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    by_coin = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not token:
            continue
        direction = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc_15m = scoring._f(m.get("contribution_pct_change_15m", 0))

        entry = by_coin.setdefault(token, {"long_pct": 0, "short_pct": 0,
                                           "traders": 0, "cc_15m": 0})
        if direction == "long":
            entry["long_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m
        elif direction == "short":
            entry["short_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m

    result = {}
    for token, d in by_coin.items():
        total = d["long_pct"] + d["short_pct"]
        if total == 0 or d["traders"] < min_traders:
            continue
        long_ratio = (d["long_pct"] / total) * 100
        if long_ratio > 58:
            result[token] = ("LONG", long_ratio, d["traders"], d["cc_15m"])
        elif long_ratio < 42:
            result[token] = ("SHORT", 100 - long_ratio, d["traders"], d["cc_15m"])
        else:
            result[token] = ("NEUTRAL", 50, d["traders"], d["cc_15m"])
    return result


def _asset_data(ctx, coin):
    """{candles{15m,1h,4h,1d}, funding} for `coin` or None. READ-GUARDED.
    Ported from v2 build_thesis's market_get_asset_data call (15m/1h/4h/1d +
    funding). Universe is crypto-only (XYZ banned), so dex is always main ('')."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["15m", "1h", "4h", "1d"],
        "include_funding": True,
        "include_order_book": False,
    })
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    asset_ctx = d.get("asset_context", d.get("assetContext", d)) if isinstance(d, dict) else {}
    funding = scoring._f(asset_ctx.get("funding", d.get("funding", 0)))
    return {"candles": candles, "funding": funding}


def scan(inputs, ctx):
    max_positions = int(inputs.get("maxPositions", 2))     # v2 MAX_POSITIONS 2
    min_score = float(inputs.get("minScore", scoring.MIN_SCORE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist(result=None):
        if ctx.state is None:
            return
        rec = {"recent": recent}
        if result is not None:
            rec["result"] = result
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[python.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    # Account + held positions. v2 required account_value > 0 to act.
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[python.scan] WAITING — no account value", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_account_value"})
        return []

    held = {p["coin"].upper() for p in positions if p.get("coin")}
    held_assets = [p["coin"] for p in positions if p.get("coin")]

    # v2: at MAX_POSITIONS the producer still scanned but the LLM/dedup never
    # opened a new slot. Honor it as a clean gate — DSL manages the held exits.
    if len(held) >= max_positions:
        print(f"[python.scan] RIDING {sorted(held)} — at maxPositions={max_positions}. "
              f"DSL manages exits.", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "riding", "held": sorted(held)})
        return []

    universe = get_universe(ctx, inputs)
    if not universe:
        print("[python.scan] market_list_instruments empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe"})
        return []

    sm_map = get_sm_map(ctx, inputs)   # may be {} — SM is a contributor, not a hard requirement

    # Score every eligible universe asset through the verbatim v2 scorer. Held +
    # recently-signaled coins are filtered BEFORE the per-asset MCP fetch (as in
    # v2 main()'s open_coins / cooldown checks).
    candidates = []
    scanned = 0
    for asset in universe:
        coin = asset["coin"]
        cu = coin.upper()
        if cu in held:
            continue
        if recent.get(cu) is not None and (now - recent[cu]) < ttl:
            continue
        scanned += 1
        md = _asset_data(ctx, coin)
        if not md:
            continue
        candles = md["candles"]
        sm_info = sm_map.get(cu)
        th = scoring.build_thesis(
            coin,
            candles.get("15m", []), candles.get("1h", []),
            candles.get("4h", []), candles.get("1d", []),
            md["funding"], asset["maxLeverage"], sm_info,
        )
        if th and th["score"] >= min_score:
            candidates.append(th)

    if not candidates:
        print(f"[python.scan] HUNTING — no patience-hold candidate >= MIN_SCORE={min_score:.0f} "
              f"(scanned {scanned}/{len(universe)}) held={held_assets}", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidate",
                  "scanned": scanned, "min_score": min_score, "held": held_assets})
        return []

    # Pick the single highest-scoring candidate (v2 emitted only `best`).
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    bu = best["coin"].upper()

    # Defense in depth: never emit on a held coin; dedup the race window.
    if bu in held:
        print(f"[python.scan] SKIP {best['coin']} — already held", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "held", "best": best["coin"]})
        return []
    if recent.get(bu) is not None and (now - recent[bu]) < ttl:
        print(f"[python.scan] DEDUP_SKIP {best['coin']} — pushed within {ttl:.0f}s (race window)",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "dedup", "best": best["coin"]})
        return []

    # Conviction-scaled sizing — per-score leverage tier + margin PERCENT intent.
    leverage = scoring.get_leverage_for_score(best["score"])
    margin_pct = scoring.get_margin_pct(best["score"])
    # Defensive fraction->percent guard (per dire/koala): a value <= 1.0 is a
    # pasted FRACTION (e.g. 0.25); scale ×100. v2 tiers already emit 25/30/40.
    if margin_pct <= 1.0:
        margin_pct = margin_pct * 100
    recent[bu] = now

    result = {"ts": now, "emitted": True, "gate": "pass", "coin": best["coin"],
              "direction": best["direction"], "score": best["score"],
              "leverage": leverage, "marginPct": round(margin_pct, 4),
              "scanned": scanned, "held": held_assets, "reasons": best["reasons"]}
    print(f"[python.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
          f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:6]}", file=sys.stderr)
    _persist(result)

    return [{
        "asset": best["coin"],
        "direction": best["direction"],
        "marginPct": margin_pct,                 # SIZING INTENT — PERCENT (0,100]; runtime sizes USD
        "leverage": leverage,                    # score-tiered 3/5/7 (MAX 7); runtime clamps to venue max
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": best["reasons"],
            "trend4h": best["trend_4h"],
            "trend1h": best["trend_1h"],
            "mom1h": best["mom_1h"],
            "mom4h": best["mom_4h"],
            "mom1d": best["mom_1d"],
            "funding": best["funding"],
            "rsi": best["rsi"],
            "volRatio": best["vol_ratio"],
            "heldAssets": held_assets,
        },
    }]
