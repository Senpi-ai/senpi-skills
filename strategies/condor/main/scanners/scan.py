"""CONDOR — supervised scanner (Runtime 3.0 port of the v2 CONDOR producer).

Multi-asset universe, emit-one. A faithful port of the v2 "One Amazing Trade
per Day" trend-continuation apex sniper (SKILL.md v4.0.1):

  1. Build the universe — top 50 HL crypto assets by 24h notional volume
     (market_list_instruments), with XYZ + stablecoins banned and an OI >= $1M
     floor.
  2. Pull smart-money positioning per coin (leaderboard_get_markets):
     direction, consensus %, trader_count, 4h/1h price change, 15m velocity.
  3. Score every universe asset through the pure `scoring.evaluate_trend_
     continuation` (hard gates: SM consensus >=70%, trader_count >=50, 3TF
     alignment, MACRO_TREND_GATE; then the verbatim v2 scoring table).
  4. Emit ONE signal for the single highest-scoring candidate clearing
     MIN_SCORE — a marginPct sizing INTENT plus a per-score leverage tier.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the
runtime sizes the dollars, owns the cooldowns/risk gates/slots, and trails the
DSL exit. Held-asset suppression and per-tick signal dedup live in ctx.state
(belt-and-suspenders alongside the runtime's per-asset cooldown gate).

PORT NOTE (XYZ ban): the v2 producer filtered XYZ via inst.get("dex") == "xyz".
The live combined market_list_instruments response carries NO dex field — XYZ
equities are identified ONLY by the "XYZ:" name prefix (e.g. "XYZ:SP500"). The
v2 dex check is therefore a silent no-op against the real shape and would let
~33 XYZ equities flood the top-50-by-volume. This port bans XYZ by name prefix
(matching kodiak's canonical _dex_for), faithfully implementing the v2 INTENT
(XYZ banned) against the real response. See RETURN notes / fidelity concerns."""

import sys
import time

import scoring

_DEFAULT_TTL = 240            # 4min — mirror v2 RECENT_SIGNAL_TTL_SEC (held-asset race-fix)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick. Returns None on failure so the existing degrade paths
    apply (empty universe / empty SM map -> emit nothing this tick)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[condor.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _is_xyz(coin):
    """XYZ equities/commodities are identified by the 'XYZ:' name prefix in the
    live combined market_list_instruments response (no dex field exists)."""
    return coin.upper().startswith("XYZ:")


def fetch_universe(ctx, inputs):
    """Top N HL crypto assets by 24h notional volume — ported verbatim from v2
    fetch_universe, with the XYZ ban switched from the (absent) dex field to the
    real 'XYZ:' name prefix. Drops stablecoins, XYZ, delisted, OI < floor."""
    universe_size = int(inputs.get("universeSize", scoring.UNIVERSE_SIZE))
    min_oi = float(inputs.get("minOiUsd", scoring.MIN_OI_USD))
    raw = _read(ctx, "market_list_instruments", {})
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []

    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        # v2 read inst.get("coin") OR inst.get("name"); live top level is "name",
        # context also carries "coin". Either resolves to the same casing.
        coin = str(inst.get("coin") or inst.get("name") or ctx_block.get("coin", "")).upper()
        if not coin or coin in scoring.STABLECOINS_BANNED:
            continue
        if _is_xyz(coin):                          # XYZ ban (name-prefix; see module docstring)
            continue
        if inst.get("is_delisted"):                # PORT-ADD: drop delisted instruments (v2 didn't
            continue                               # check; harmless safety — they carry stale ctx)

        oi = scoring._f(ctx_block.get("openInterest", inst.get("openInterest", 0)))
        mark_px = scoring._f(ctx_block.get("markPx", ctx_block.get("midPx",
                             inst.get("markPx", inst.get("midPx", 0)))))
        volume_24h = scoring._f(ctx_block.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        funding = scoring._f(ctx_block.get("funding", inst.get("funding", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0

        if oi_usd < min_oi or mark_px <= 0:
            continue

        assets.append({
            "coin": coin, "oi_usd": oi_usd,
            "volume_24h": volume_24h, "price": mark_px, "funding": funding,
        })

    assets.sort(key=lambda x: x["volume_24h"], reverse=True)
    return assets[:universe_size]


def fetch_sm_map(ctx, inputs):
    """Hyperfeed SM leaderboard: per-coin direction, gain share, dominance, 4h/1h
    price, 15m velocity. Ported from v2 fetch_sm_map (XYZ banned by name).

    NOTE on `gain_share_pct`: this is `pct_of_top_traders_gain`, a share of the
    TOTAL top-trader gain attributed to this market — it is NOT a "% of traders
    leaning this way". It is distributed across every market in the response
    (observed: sums to ~125 across 271 markets, max ~26, median ~0.01), so a
    threshold on the 0-100 "consensus" scale can never be met. The v2 port named
    this `consensus_pct` and gated it at >=70, which silently blocked every
    signal. Directional agreement is carried separately by `is_dominant`
    (`is_dominant_direction`), which is the field that actually expresses it."""
    limit = int(inputs.get("smLimit", 100))
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return {}
    markets = raw
    if isinstance(markets, dict):
        data = markets.get("data", markets)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    if not isinstance(markets, list):
        return {}

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        if not token or _is_xyz(token):            # XYZ ban (name-prefix; v2 used dex)
            continue
        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "gain_share_pct": scoring._f(m.get("pct_of_top_traders_gain", 0)),
            "is_dominant": bool(m.get("is_dominant_direction", False)),
            "traders": int(m.get("trader_count", 0) or 0),
            "p4h": scoring._f(m.get("token_price_change_pct_4h", 0)),
            "p1h": scoring._f(m.get("token_price_change_pct_1h",
                              m.get("price_change_1h", 0))),
            "c15m": scoring._f(m.get("contribution_pct_change_15m", 0)),
            "c1h": scoring._f(m.get("contribution_pct_change_1h", 0)),
        }
    return sm_map


def get_btc_macro(sm_map):
    """BTC's dominant direction + 4h magnitude as macro context (verbatim v2)."""
    btc = sm_map.get("BTC")
    if not btc:
        return None
    return {"direction": btc["direction"], "p4h": btc["p4h"]}


def _held_assets(ctx, inputs):
    """Read open positions (both sub-DEX views) so we never emit on a coin the
    runtime already holds — belt-and-suspenders alongside the runtime cooldown.
    A failed read returns [] (the runtime per_asset_cooldown is the safety floor)."""
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
            for ap in s.get("assetPositions", []):
                pos = ap.get("position", ap) if isinstance(ap, dict) else {}
                if scoring._f(pos.get("szi", 0)) != 0:
                    c = str(pos.get("coin", "")).upper()
                    if c:
                        held.append(c)
    return held


def scan(inputs, ctx):
    max_positions = int(inputs.get("maxPositions", 1))     # v2 "one amazing trade per day"
    min_score = float(inputs.get("minScore", scoring.MIN_SCORE))
    margin_default = float(inputs.get("marginPct", 50))    # PERCENT of withdrawable (0,100]
    tiers = inputs.get("leverageTiers")                    # optional [[min_score, lev, margin_pct], ...]
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()
    hour = time.gmtime(now).tm_hour

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
            print(f"[condor.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    # v2: if a position is already open, no new signal — runtime DSL manages exit.
    held = {h.upper() for h in _held_assets(ctx, inputs)}
    if len(held) >= max_positions:
        print(f"[condor.scan] RIDING {sorted(held)} — at maxPositions={max_positions}. DSL manages exit.",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "riding", "held": sorted(held)})
        return []

    universe = fetch_universe(ctx, inputs)
    if not universe:
        print("[condor.scan] market_list_instruments empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe"})
        return []

    sm_map = fetch_sm_map(ctx, inputs)
    if not sm_map:
        print("[condor.scan] leaderboard_get_markets empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_sm"})
        return []

    btc_macro = get_btc_macro(sm_map)

    # Evaluate every universe asset through the verbatim v2 scorer.
    candidates = []
    for asset_info in universe:
        coin = asset_info["coin"]
        sm = sm_map.get(coin)
        if not sm:
            continue
        sig = scoring.evaluate_trend_continuation(asset_info, sm, btc_macro, hour, inputs)
        if sig:
            candidates.append(sig)

    if not candidates:
        print(f"[condor.scan] WAITING — no apex setup >= MIN_SCORE={min_score:.0f} "
              f"(scanned {len(universe)})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidate",
                  "scanned": len(universe), "min_score": min_score})
        return []

    # Pick the single highest-scoring candidate (v2 "one amazing trade").
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    bu = best["coin"].upper()

    # Defense in depth: never emit on a held coin, and dedup the race window.
    if bu in held:
        print(f"[condor.scan] SKIP {best['coin']} — already held", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "held", "best": best["coin"]})
        return []
    if recent.get(bu) is not None and (now - recent[bu]) < ttl:
        print(f"[condor.scan] DEDUP_SKIP {best['coin']} — pushed within {ttl:.0f}s (race window)",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "dedup", "best": best["coin"]})
        return []

    # Score-scaled sizing — per-tier leverage + marginPct PERCENT intent.
    leverage, margin_pct = scoring.get_sizing_for_score(
        best["score"], _coerce_tiers(tiers))
    margin_pct = margin_pct if margin_pct else margin_default
    recent[bu] = now

    result = {"ts": now, "emitted": True, "gate": "pass", "coin": best["coin"],
              "direction": best["direction"], "score": best["score"],
              "leverage": leverage, "marginPct": margin_pct, "reasons": best["reasons"]}
    print(f"[condor.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
          f"{leverage}x margin={margin_pct}% | {best['reasons']}", file=sys.stderr)
    _persist(result)

    return [{
        "asset": best["coin"],
        "direction": best["direction"],
        "marginPct": margin_pct,                 # SIZING INTENT — PERCENT (0,100]; runtime sizes USD
        "leverage": leverage,                    # score-tiered (10x cap); runtime clamps to venue max
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": best["reasons"],
            "priceChange4hPct": best["p4h"],
            "priceChange1hPct": best["p1h"],
            "contribChange15m": best["c15m"],
            "smGainSharePct": best["sm_gain_share"],
            "smTraders": best["sm_traders"],
            "oiUsd": best["oi_usd"],
            "funding": best["funding"],
        },
    }]


def _coerce_tiers(tiers):
    """Accept the runtime-yaml tier shape [[min_score, leverage, margin_pct], ...]
    and convert to the dict form scoring.get_sizing_for_score expects. None -> v2
    defaults."""
    if not tiers:
        return None
    out = []
    for t in tiers:
        if isinstance(t, dict):
            out.append(t)
        elif isinstance(t, (list, tuple)) and len(t) >= 3:
            out.append({"min_score": t[0], "leverage": t[1], "margin_pct": t[2]})
    return out or None
