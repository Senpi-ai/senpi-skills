"""CUB · SHORT "have-nots" book — supervised scanner (Runtime 3.0 port of cub-producer.py CUB_LEG=short).

Shorts the laggards of a two-speed (K-shaped) world: the broad U.S. market via the SP500 index
product (the "economy suffers" core) plus a curated, gated basket of laggard crypto majors/alts
(the "rest of crypto struggles" bet), trend-confirmed with a CAPITULATION guard (never short an
exhausted bottom). A faithful port of the v2 cub-producer's curated-thematic short book. Read-only,
single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin; dual-DEX equity via
     max(), never sum())
  2. build the curated thematic universe (inputs.universe — the "have-nots"), intersected with the
     live board + a relative liquidity floor (NO hardcoded $)
  3. rank by 24h cross-sectional relative strength, take the bottom rankPoolSize (LAGGARDS first)
  4. score each pooled name with the v2 ABSOLUTE-downtrend gate + excess-as-tiebreaker thesis;
     dedup held + recently-signalled
  5. emit, best-score first, a top-level conviction-weighted marginPct INTENT (PERCENT) + a per-name
     venue-clamped leverage; cap to what the wallet can FUND.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips without
rolling back the whole universe tick.

FIDELITY NOTES vs cub-producer.py v1.0.0 (CUB_LEG=short):
  - v2 sized margin_usd = account_value * marginPct(FRACTION 0.15) * sizingWeight (USD). This port
    emits a top-level `marginPct` PERCENT with the per-name conviction weight baked in
    (marginPct = base_margin_pct_percent * sizing_weight); the runtime sizes
    (marginPct/100)*withdrawable. `<=1.0 means a pasted fraction -> x100` guard converts a fraction.
  - v2 ranked laggards-first (reverse=False for the short leg); preserved.
  - v2 funding cap (free margin, 1.1 headroom); ported.
  - v2 recent-signals JSON cache -> ctx.state dedup (180s race-window).
  - DROPPED (read-only scan cannot mutate): v2 had no order-lifecycle mutations; push_signal /
    record_signal replaced by returning plain dicts + ctx.state per the scan() contract.
"""

import sys
import time

import scoring

# v2 _DEFAULTS["short"] / cub-short-config.json
_HAVE_NOTS_DEFAULT = ["xyz:SP500", "ETH", "XRP", "DOGE", "AVAX", "LINK", "ADA", "LTC", "NEAR", "APT"]
_HAVE_NOTS_WEIGHTS_DEFAULT = {"SP500": 1.2, "_default": 0.7}
_DEFAULT_TTL = 180
_DIRECTION = "SHORT"


def _read(ctx, name, args):
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[cub.short.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _resolve_margin_pct(inputs, default_pct):
    mp = scoring._f(inputs.get("marginPct", default_pct), default_pct)
    if mp <= 1.0:                 # a fraction was pasted (e.g. 0.15) -> percent
        mp *= 100.0
    return mp


def _get_positions(ctx):
    """(account_value, [positions], free_margin). accountValue via max() across main/xyz; v2
    read-sanity guard (margin in use but empty positions -> skip tick)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = _unwrap(ch)
    if not isinstance(data, dict):
        return 0.0, [], 0.0
    positions, account_value, used = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        used = max(used, scoring._f(ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "margin": scoring._f(pos.get("marginUsed", 0)),
            })
    if used > 1.0 and not positions:
        print("[cub.short.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


def _get_universe_meta(ctx):
    """name -> {max_leverage, ctx{...}}. Skips delisted. Verbatim v2 get_universe_meta()."""
    resp = _read(ctx, "market_list_instruments", {})
    out = {}
    if not resp:
        return out
    insts = _unwrap(resp)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
    return out


def _fetch_candles(ctx, asset):
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    })
    if not resp:
        return [], []
    d = _unwrap(resp)
    if isinstance(d, dict) and d.get("success") is False:
        return [], []
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _build_universe(whitelist, meta_map, vol_floor_pct):
    """Curated whitelist ∩ live board + relative liquidity floor (>= vol_floor_pct of median 24h
    vol; NO hardcoded $). Verbatim v2 build_universe()."""
    cand = []
    for name in whitelist:
        if not isinstance(name, str):
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        vol = scoring.day_vol(meta)
        if vol <= 0:
            continue
        cand.append((name, vol))
    if not cand:
        return []
    vols = sorted(v for _, v in cand)
    median = vols[len(vols) // 2]
    floor = vol_floor_pct * median
    return [n for n, v in cand if v >= floor]


def scan(inputs, ctx):
    run_start = time.time()
    whitelist = inputs.get("universe", _HAVE_NOTS_DEFAULT)
    weights = inputs.get("sizingWeights", _HAVE_NOTS_WEIGHTS_DEFAULT)
    min_score = int(inputs.get("minScore", 5))
    base_margin_pct = _resolve_margin_pct(inputs, 15)        # PERCENT of withdrawable (0,100]
    max_lev = int(inputs.get("maxLeverage", 4))
    max_slots = int(inputs.get("maxSlots", 4))
    rank_pool = int(inputs.get("rankPoolSize", 16))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent})
        except Exception as exc:  # noqa: BLE001
            print(f"[cub.short.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        return []

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct)

    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = scoring.ret_24h(meta) if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 2:                                          # v2-quirk: thematic universe too thin
        _persist()
        print("[cub.short.scan] WAITING — thematic universe too thin to evaluate", file=sys.stderr)
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=False)               # laggards first (short leg)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)
        thesis = scoring.score_thematic(name, c1, c4, excess, own, _DIRECTION, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist()
        print(f"[cub.short.scan] WAITING — no name cleared min score {min_score}; "
              f"scanned={len(universe)} pool={len(pool)} held={held}", file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    out = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], weights)
        margin_pct = round(base_margin_pct * weight, 4)
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)
        if margin_pct <= 0 or leverage <= 0:
            continue
        margin_usd = (margin_pct / 100.0) * account_value
        if margin_usd * 1.1 > free_margin:                   # 1.1 = fee/slippage headroom (v2)
            continue
        out.append({
            "asset": th["coin"],
            "direction": _DIRECTION,
            "marginPct": margin_pct,
            "leverage": leverage,
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": _DIRECTION,
                "reasons": th["reasons"][:6],
                "trend4h": th.get("trend4h"),
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
                "weight": weight,
                "heldAssets": held,
            },
        })
        recent[th["coin"].upper()] = now
        open_slots -= 1
        free_margin -= margin_usd * 1.1

    _persist()
    print(f"[cub.short.scan] scanned={len(universe)} pool={len(pool)} candidates={len(candidates)} "
          f"emitted={len(out)} mean_rs={mean_rs:.2f} elapsed={time.time() - run_start:.2f}s",
          file=sys.stderr)
    return out
