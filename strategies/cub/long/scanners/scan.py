"""CUB · LONG "haves" book — supervised scanner (Runtime 3.0 port of cub-producer.py CUB_LEG=long).

Longs the structural winners of a two-speed (K-shaped) world: the AI complex on Hyperliquid XYZ
(NVDA/AMD/MRVL/TSM/ASML/ARM/AVGO/CRWV/PLTR/ORCL/…) plus the crypto winners (HYPE large, SOL
modest), trend-confirmed. A faithful port of the v2 cub-producer's curated-thematic long book.
Read-only, single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin; dual-DEX equity via
     max(), never sum() — main + xyz are two views of ONE cross-margined wallet)
  2. build the curated thematic universe (inputs.universe — the "haves"), intersected with the live
     instrument board + a relative liquidity floor (>= volFloorPctOfMedian of the whitelist median
     24h vol; NO hardcoded $)
  3. rank the universe by 24h cross-sectional relative strength (own 24h - universe mean), take the
     top rankPoolSize (LEADERS first)
  4. score each pooled name with the v2 ABSOLUTE-trend gate + excess-as-tiebreaker thesis; dedup
     held + recently-signalled
  5. emit, best-score first, a top-level conviction-weighted marginPct INTENT (PERCENT, the runtime
     sizes the $) + a per-name venue-clamped leverage; cap to what the wallet can FUND.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips without
rolling back the whole universe tick (asia-ai NASDAQ-bug class).

FIDELITY NOTES vs cub-producer.py v1.0.0 (CUB_LEG=long):
  - v2 sized margin_usd = account_value * marginPct(FRACTION 0.18) * sizingWeight, then emitted a
    USD figure. This port emits a top-level `marginPct` as a PERCENT and bakes the per-name
    conviction weight INTO it: marginPct = base_margin_pct_percent * sizing_weight (then the runtime
    sizes (marginPct/100)*withdrawable). The defensive `<=1.0 means a pasted fraction -> x100` guard
    converts a fraction supplied via inputs (so 0.18 -> 18). Sizing is otherwise identical.
  - v2 ranked the WHOLE board sort then took rankPoolSize; preserved (leaders first for the long leg).
  - v2 funding cap: never emit more than free margin can fund (1.1 fee/slippage headroom); ported.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL: 180s race-window).
  - v2 SM lean was NOT used in the long/short books' score_thematic (it is a curated thematic book,
    not a smart-money book) — nothing dropped there.
  - DROPPED (read-only scan cannot mutate): the v2 producer had no order-lifecycle mutations
    (cancel_order / stale-order purge), so nothing is dropped on that front; push_signal /
    record_signal are replaced by returning plain dicts + ctx.state per the scan() contract.
"""

import sys
import time

import scoring

# v2 _DEFAULTS["long"] / cub-long-config.json
_HAVES_DEFAULT = [
    "xyz:NVDA", "xyz:AMD", "xyz:MRVL", "xyz:ARM", "xyz:AVGO", "xyz:INTC",
    "xyz:TSM", "xyz:ASML", "xyz:CBRS", "xyz:MU", "xyz:SMSN", "xyz:SKHX",
    "xyz:SNDK", "xyz:CRWV", "xyz:NBIS", "xyz:DELL", "xyz:LITE", "xyz:GOOGL",
    "xyz:MSFT", "xyz:META", "xyz:AMZN", "xyz:ORCL", "xyz:PLTR", "xyz:NOW",
    "xyz:IBM", "xyz:SPCX", "xyz:QNT", "HYPE", "SOL",
]
_HAVES_WEIGHTS_DEFAULT = {
    "HYPE": 1.5, "SOL": 0.6, "SPCX": 0.6, "QNT": 0.5,
    "CBRS": 0.7, "NBIS": 0.7, "_default": 1.0,
}
_DEFAULT_TTL = 180   # v2 RECENT_SIGNAL_TTL_SEC — don't re-fire a name in flight
_DIRECTION = "LONG"


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must NOT roll back the
    whole tick (per the contract ANY exception rolls the tick to []). Returns None so the caller's
    degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[cub.long.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _resolve_margin_pct(inputs, default_pct):
    """marginPct base intent as a PERCENT in (0,100]. v2 stored a FRACTION (0.18); convert with
    the defensive `<=1.0 means a pasted fraction -> x100` guard."""
    mp = scoring._f(inputs.get("marginPct", default_pct), default_pct)
    if mp <= 1.0:                 # a fraction was pasted (e.g. 0.18) -> percent
        mp *= 100.0
    return mp


# ── account / positions (dual-DEX collapse + read-sanity guard, verbatim v2) ──

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). accountValue via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts -> 2x sizing). Free
    margin = equity - committed margin. Includes the v2 read-sanity guard."""
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
    # v2 read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but EMPTY positions
    # is a corrupt read — sizing/held-dedup off that re-enters held names. Skip the tick.
    if used > 1.0 and not positions:
        print("[cub.long.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + 24h vol + 24h return + ctx ──

def _get_universe_meta(ctx):
    """name -> {max_leverage, ctx{...}}. Skips delisted. Verbatim v2 get_universe_meta() — keeps the
    raw instrument context so ret_24h/day_vol/IPOP-signature reads work off it."""
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
    """1h + 4h candles for ONE asset, dex-routed for xyz. Guarded — a bad name returns ([],[]) and
    the universe loop skips it."""
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
    """The curated thematic whitelist intersected with the live board + a relative liquidity floor
    (>= vol_floor_pct of the whitelist's MEDIAN 24h vol; NO hardcoded $). Names not live / too thin
    are dropped, so new listings auto-join. Verbatim v2 build_universe()."""
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
    whitelist = inputs.get("universe", _HAVES_DEFAULT)
    weights = inputs.get("sizingWeights", _HAVES_WEIGHTS_DEFAULT)
    min_score = int(inputs.get("minScore", 5))
    base_margin_pct = _resolve_margin_pct(inputs, 18)        # PERCENT of withdrawable (0,100]
    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 5))
    rank_pool = int(inputs.get("rankPoolSize", 30))
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
            print(f"[cub.long.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        return []                                            # book full — runtime also caps via slots

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct)

    # ── Cross-sectional relative-strength rank (excess vs mean is a TIEBREAKER, not a gate) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = scoring.ret_24h(meta) if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 2:                                          # v2-quirk: thematic universe too thin
        _persist()
        print("[cub.long.scan] WAITING — thematic universe too thin to evaluate", file=sys.stderr)
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=True)                # leaders first (long leg)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue                                         # signal-dedup
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        thesis = scoring.score_thematic(name, c1, c4, excess, own, _DIRECTION, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist()
        print(f"[cub.long.scan] WAITING — no name cleared min score {min_score}; "
              f"scanned={len(universe)} pool={len(pool)} held={held}", file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # ── Emit best-scoring first, conviction-weighted size, capped to what the wallet can FUND.
    #    free margin decremented as we commit (1.1 fee/slippage headroom) so a mixed basket never
    #    emits an un-fundable order (which would re-emit insufficient-funds every tick). ──
    out = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], weights)
        margin_pct = round(base_margin_pct * weight, 4)      # PERCENT intent (conviction-weighted)
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)
        if margin_pct <= 0 or leverage <= 0:
            continue
        # fund check: equivalent $ margin vs free margin (the runtime sizes from withdrawable;
        # account_value is the available proxy here, matching v2's free-margin gate).
        margin_usd = (margin_pct / 100.0) * account_value
        if margin_usd * 1.1 > free_margin:                   # 1.1 = fee/slippage headroom (v2)
            continue
        out.append({
            "asset": th["coin"],
            "direction": _DIRECTION,
            "marginPct": margin_pct,                          # PERCENT intent — runtime sizes the $
            "leverage": leverage,                             # already venue-clamped
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
    print(f"[cub.long.scan] scanned={len(universe)} pool={len(pool)} candidates={len(candidates)} "
          f"emitted={len(out)} mean_rs={mean_rs:.2f} elapsed={time.time() - run_start:.2f}s",
          file=sys.stderr)
    return out
