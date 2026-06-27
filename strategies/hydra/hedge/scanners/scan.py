"""HYDRA HEDGE — supervised scanner (Runtime 3.0 port of v2 hydra-producer.py hedge_main,
LEG=hedge). Cross-asset SHORT basket. Builds the hedge blend (hedgeUniverse minus the
thesis coin, unless hedgeIncludesThesis), computes the thesis-stress multiplier (reads the
thesis coin's 4h), scans each blend asset's 1h/4h, scores via the pure
`scoring.score_hedge_one`, vol-parity sizes the margin (scaled by thesis stress, total
capped at hedgeMaxTotalPct), and emits a basket of SHORT signals with explicit per-signal
`marginUsd` (vol-parity dollars) + `leverage`. Read-only + single-pass — the runtime owns
slots/dedup/risk and trails the DSL. No daemon.

PER-COIN PARAMETERIZATION: the thesis coin is inputs.coin (mirrors v2 HYDRA_COIN env); the
blend auto-excludes it unless inputs.hedgeIncludesThesis is true (the HYPE hybrid).
"""

import sys
import time

import scoring

_DEFAULT_TTL = 240


def _dex_for(asset):
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on ONE blend name must NOT roll
    back the whole basket tick. Returns None so the per-asset/degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[hydra-hedge.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _account_value(ctx):
    """max(main, xyz) account value — two views of ONE cross-margined wallet (v2
    get_positions: never sum). Returns (account_value, held_assets, deployed_margin)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    account_value, held, deployed = 0.0, [], 0.0
    use = 0.0
    has_pos = False
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s.get("marginSummary"), dict) else {}
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        use = max(use, float(ms.get("totalMarginUsed", 0) or 0),
                  abs(float(ms.get("totalNtlPos", 0) or 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            has_pos = True
            held.append(pos.get("coin", ""))
            deployed += float(pos.get("marginUsed", 0) or 0)
    if use > 1.0 and not has_pos:
        return 0.0, [], 0.0
    return account_value, [h for h in held if h], deployed


def _fetch_candles(ctx, asset, intervals):
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset, "candle_intervals": list(intervals),
        "dex": _dex_for(asset), "include_funding": False, "include_order_book": False,
    })
    if not md:
        return None
    d = md.get("data", md) if isinstance(md, dict) else {}
    return d.get("candles", {}) or {}


def scan(inputs, ctx):
    coin = str(inputs.get("coin", "ETH"))
    cu = coin.upper()
    min_score = float(inputs.get("minScore", 4))
    max_slots = int(inputs.get("maxSlots", 5))
    max_lev = int(inputs.get("maxLeverage", 3))
    std_lev = int(inputs.get("stdLeverage", 3))
    allow_thesis = bool(inputs.get("hedgeIncludesThesis", False))
    universe_cfg = inputs.get("hedgeUniverse", [])
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    total_cap_pct = float(inputs.get("hedgeMaxTotalPct", 0.45))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    recent = {k: v for k, v in ((ctx.state.last() or {}).get("recent", {}) if ctx.state else {}).items()
              if (now - v) < ttl}

    def _persist(extra=None):
        if ctx.state is None:
            return
        rec = {"recent": recent}
        if extra:
            rec.update(extra)
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[hydra-hedge.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, held, deployed = _account_value(ctx)
    if account_value <= 0:
        _persist({"result": {"ts": now, "coin": coin, "emitted": 0, "note": "no account value"}})
        return []
    held_set = {h.upper() for h in held}

    # the blend: exclude the thesis coin (unless hybrid) and any name already held
    universe = [a for a in universe_cfg
                if (allow_thesis or a.upper() != cu) and a.upper() not in held_set]
    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist({"result": {"ts": now, "coin": coin, "emitted": 0, "note": "hedge slots full"}})
        return []

    # thesis-stress multiplier: read the thesis coin's 4h, size the hedge UP if it's breaking
    thesis_c = _fetch_candles(ctx, coin, ["4h"])
    mult, thesis_dd = scoring.thesis_stress_from_candles((thesis_c or {}).get("4h", []), inputs)

    candidates = []
    scanned = 0
    for asset in universe:
        if recent.get(asset.upper()) is not None:    # signal-dedup (already filtered by ttl above)
            continue
        candles = _fetch_candles(ctx, asset, ["1h", "4h"])
        if not candles:
            continue
        scanned += 1
        th = scoring.score_hedge_one(asset, candles.get("1h", []), candles.get("4h", []), inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    if not candidates:
        result = {"ts": now, "coin": coin, "scanned": scanned, "emitted": 0,
                  "thesis_drawdown_pct": thesis_dd, "stress_mult": mult,
                  "note": "nothing in the hedge blend is breaking down"}
        print(f"[hydra-hedge.scan] {coin} HOLD: {result}", file=sys.stderr)
        _persist({"result": result})
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)
    free_margin = max(0.0, account_value - deployed)
    total_cap = account_value * total_cap_pct
    min_notional = max(account_value * min_notional_pct, venue_min_notional)
    leverage = scoring.clamp_lev(std_lev, max_lev)

    out, emitted = [], []
    slots_left = open_slots
    for th in candidates:
        if slots_left <= 0:
            break
        margin_usd = scoring.vol_parity_margin(account_value, th["vol_pct"], inputs, mult=mult)
        notional = margin_usd * leverage
        if notional < min_notional:
            continue
        if deployed + margin_usd > total_cap:          # cap TOTAL hedge margin
            continue
        if margin_usd * 1.1 > free_margin:
            continue
        # hedgeFor drives the runtime's "never short the thesis coin" guard. When the
        # hybrid is on (allow_thesis), leave it empty so a deliberate thesis-coin short
        # isn't blocked; otherwise set it to COIN to keep the guard active. (v2-quirk)
        hedge_for = "" if allow_thesis else coin
        out.append({
            "asset": th["coin"],
            "direction": "SHORT",
            "marginUsd": margin_usd,         # VOL-PARITY DOLLARS — explicit top-level USD margin
            "leverage": leverage,            # strict 3x (short squeezes are violent)
            "data": {
                "score": th["score"], "leverage": leverage, "direction": "SHORT",
                "trend4h": th["trend4h"], "rsi": th.get("rsi", 0), "hedgeFor": hedge_for,
                "stressMult": mult, "volPct": th["vol_pct"], "reasons": th["reasons"],
            },
        })
        recent[th["coin"].upper()] = now
        slots_left -= 1
        free_margin -= margin_usd
        deployed += margin_usd
        emitted.append({"coin": th["coin"], "score": th["score"], "margin_usd": margin_usd,
                        "vol_pct": th["vol_pct"]})

    result = {"ts": now, "coin": coin, "scanned": scanned, "candidates": len(candidates),
              "emitted": len(out), "thesis_drawdown_pct": thesis_dd, "stress_mult": mult,
              "hedge_deployed_usd": round(deployed, 2), "hedge_cap_usd": round(total_cap, 2),
              "picks": emitted}
    print(f"[hydra-hedge.scan] {coin} EMIT {len(out)}: stress_mult={mult} dd={thesis_dd}% | {emitted}",
          file=sys.stderr)
    _persist({"result": result})
    return out
