"""CAMEL — supervised scanner (shared VERBATIM by both books; this file is byte-identical
in harvest/ and payout/). Direction-parametrized via the `leg` input: the `harvest`
instance passes leg="harvest" (SHORT the most-positive-funding names — short collects the
funding) and the `payout` instance passes leg="payout" (LONG the most-negative-funding
names — long gets paid to hold). A faithful Runtime 3.0 port of the v2 camel-producer.py:
the funding rank + carry scoring + leverage clamp + affordability cap are preserved exactly.
Read-only, single-pass — no daemon, no push_signal, no create_position, no order lifecycle.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin)
  2. build the live universe ONCE: all liquid main-DEX crypto perps on the live instrument
     board (XYZ excluded — XYZ funding is sparse), capped to universeMaxNames by 24h volume,
     then a relative liquidity floor (>= volFloorPctOfMedian of the cohort median; NO $ floor)
  3. rank that universe by FUNDING — harvest: DESC (most positive); payout: ASC (most
     negative) — and take the top rankPoolSize
  4. fetch 1h+4h candles for each pooled name, score with the v2 carry gates, dedup held +
     recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT, the runtime sizes) + a per-name
     venue-clamped leverage; the runtime owns slots, dedup, execution, DSL.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (per the contract ANY exception rolls the tick
back to []). The universe is built live from the instrument board, so a name that delists or
goes thin between the board pull and a per-asset candle fetch is skipped, not fatal.

FIDELITY NOTES vs camel-producer.py v1.0.0:
  - DROPPED v2's order-lifecycle / re-emit-spam plumbing that depended on MUTATIONS or daemon
    state: v2 wrote a recent-signals JSON file (race-window dedup) — ported to ctx.state
    (same 180s TTL semantics). v2 had no cancel_order/has_resting_orders, so nothing
    mutation-bearing was dropped beyond the daemon loop + push_signal + the JSON cache file.
  - v2 config stored marginPct as a FRACTION (0.18). This port treats inputs.marginPct as a
    PERCENT (18) and emits top-level marginPct; the runtime sizes (marginPct/100)*withdrawable.
    The runtime.yaml inputs ship marginPct: 18. A defensive guard converts a value <= 1.0
    (a pasted fraction) to a percent so a mis-set config can't silently 1/100th the size.
  - v2 computed margin_usd = account_value * margin_pct then affordability off free margin.
    This port emits the marginPct INTENT (runtime owns the $ sizing) but preserves the v2
    affordability cap so an open slot with no free margin doesn't re-emit an un-fillable order
    every tick — sized as (marginPct/100)*accountValue per name + 1.1 fee/slippage headroom.
  - v2 minNotional gate (max(accountValue*minNotionalPctOfEquity, venueMinNotionalUsd) vs
    marginPct*leverage) is preserved: drop a pooled name whose intended notional is below the
    HL venue minimum order value.
  - v2's first-seen ledger helpers were declared-but-UNUSED scaffold; not ported.
"""

import sys
import time

import scoring

_DEFAULT_TTL = 180   # 3m: match v2 RECENT_SIGNAL_TTL_SEC — don't re-fire a name in flight
HOURS_PER_YEAR = scoring.HOURS_PER_YEAR


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must NOT roll
    back the whole tick. Returns None on failure so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[camel.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── account / positions ──────────────────────────────────────────────────────

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). The 'main' and 'xyz'
    clearinghouse sections are TWO VIEWS of ONE cross-margined wallet — accountValue is
    taken ONCE via max() across sections, NEVER summed (v2-quirk: summing double-counts and
    makes every size 2x). Free margin = equity - committed margin. READ-GUARDED."""
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
        account_value = max(account_value, _f(ms.get("accountValue", 0)))
        used = max(used, _f(ms.get("totalMarginUsed", 0)), abs(_f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = _f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": _f(pos.get("marginUsed", 0)),
            })
    # v2-quirk read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but an
    # EMPTY positions list is a corrupt read — sizing or running the held-dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    if used > 1.0 and not positions:
        print("[camel.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + funding + 24h vol + 24h return ───────

def _get_universe_meta(ctx):
    """name -> {max_leverage, funding, vol, ret24h}. Skips delisted + XYZ. One instrument-
    board call carries funding / markPx / prevDayPx / dayNtlVlm per asset. Verbatim v2
    get_universe_meta() + funding_hourly() + ret_24h() + day_vol() folded together."""
    resp = _read(ctx, "market_list_instruments", {})
    out, canonical = {}, []
    if not resp:
        return out, canonical
    insts = _unwrap(resp)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name or not isinstance(name, str):
            continue
        if name.lower().startswith("xyz:"):                 # v2-quirk: XYZ excluded (funding sparse)
            continue
        ctxd = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        mark, prev = _f(ctxd.get("markPx", 0)), _f(ctxd.get("prevDayPx", 0))
        ret24 = ((mark - prev) / prev * 100.0) if (prev > 0 and mark > 0) else 0.0
        try:
            funding = float(ctxd.get("funding", 0) or 0)
        except (TypeError, ValueError):
            funding = None
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "funding": funding,
            "vol": _f(ctxd.get("dayNtlVlm", 0)),
            "ret24h": ret24,
        }
        out[name] = entry
        out[name.upper()] = entry
        canonical.append(name)
    return out, canonical


def _fetch_candles(ctx, asset):
    """1h + 4h candles for ONE asset. Guarded — a bad name returns ([], []) and the loop
    skips it. Verbatim v2 fetch_candles(["1h","4h"])."""
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    })
    if not resp:
        return [], []
    if isinstance(resp, dict) and resp.get("success") is False:
        return [], []
    d = _unwrap(resp)
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _build_universe(canonical, meta_map, max_names, vol_floor_pct):
    """Liquid main-DEX crypto perps (XYZ already excluded in _get_universe_meta), capped to
    the top max_names by 24h volume, then a relative liquidity floor (>= vol_floor_pct of the
    top-N cohort median; NO hardcoded $). Verbatim v2 build_universe()."""
    seen, pool = set(), []
    for name in canonical:
        if not isinstance(name, str):
            continue
        key = name.upper()
        if key in seen:
            continue
        meta = meta_map.get(name) or meta_map.get(key)
        if not meta:
            continue
        vol = meta.get("vol", 0.0)
        if vol <= 0:
            continue
        seen.add(key)
        pool.append((name, vol))
    pool.sort(key=lambda x: x[1], reverse=True)
    pool = pool[:max_names]
    if not pool:
        return []
    vols = sorted(v for _, v in pool)
    median = vols[len(vols) // 2]
    floor = vol_floor_pct * median
    return [n for n, v in pool if v >= floor]


def scan(inputs, ctx):
    run_start = time.time()
    leg = (inputs.get("leg", "harvest") or "harvest").strip().lower()
    direction = "SHORT" if leg == "harvest" else "LONG"
    min_score = int(inputs.get("minScore", 4))

    # marginPct is a PERCENT in (0,100]. v2 stored it as a FRACTION (0.18); a defensive
    # guard converts a pasted fraction (<= 1.0) to a percent so a mis-set config can't
    # silently 1/100th the size (dire/koala guard).
    margin_pct = float(inputs.get("marginPct", 18))
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0

    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 4))
    rank_pool = int(inputs.get("rankPoolSize", 12))
    max_names = int(inputs.get("universeMaxNames", 60))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
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
            print(f"[camel.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        return []                                            # no value / corrupt read — skip tick
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        print(f"[camel.scan] leg={leg} WAITING — slots full ({len(held)}/{max_slots})",
              file=sys.stderr)
        return []                                            # book full — runtime also caps via slots

    meta_map, canonical = _get_universe_meta(ctx)
    universe = _build_universe(canonical, meta_map, max_names, vol_floor_pct)

    # ── Rank by FUNDING (no candle fetch): harvest = most POSITIVE, payout = most NEGATIVE ──
    funded = []  # (name, funding, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        f = meta.get("funding") if meta else None
        if f is None:
            continue
        funded.append((name, f, meta))
    if len(funded) < 3:                                      # v2-quirk: too thin to rank funding
        _persist()
        print(f"[camel.scan] leg={leg} WAITING — no funding data to rank (universe={len(universe)})",
              file=sys.stderr)
        return []

    funded.sort(key=lambda x: x[1], reverse=(leg == "harvest"))
    pool = funded[:rank_pool]

    candidates = []
    for name, f, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue                                         # signal-dedup
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        if len(c1) < 8 or len(c4) < 6:
            continue
        own = meta.get("ret24h") if meta else 0.0
        thesis = scoring.score_carry(name, c1, c4, f, own, leg, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist()
        top_ann = round(pool[0][1] * HOURS_PER_YEAR * 100, 1) if pool else 0
        print(f"[camel.scan] leg={leg} WAITING — no name cleared min score {min_score}; "
              f"scanned={len(universe)} pool={len(pool)} top_funding_annpct={top_ann}",
              file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: never emit more than the wallet can actually FUND. An open slot with no free
    # margin re-emits an un-fillable order every tick (insufficient-funds spam). free margin
    # sized as (marginPct/100)*accountValue per name + 1.1 fee/slippage headroom.
    per_name_margin = (margin_pct / 100.0) * account_value
    affordable = int(free_margin / (per_name_margin * 1.1)) if per_name_margin > 0 else 0
    to_emit = candidates[:max(0, min(open_slots, affordable))]

    # v2 minNotional gate: drop a name whose intended notional is below the HL venue minimum.
    min_notional = max(account_value * min_notional_pct, venue_min_notional)

    out = []
    for th in to_emit:
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)  # v2-quirk: per-name venue clamp
        if leverage <= 0:
            continue
        notional = per_name_margin * leverage
        if notional < min_notional:
            continue
        out.append({
            "asset": th["coin"],
            "direction": direction,
            "marginPct": margin_pct,                           # PERCENT intent — runtime sizes the $
            "leverage": leverage,                              # already venue-clamped
            "data": {
                "score": th["score"],
                "direction": direction,
                "reasons": th["reasons"][:6],
                "trend4h": th.get("trend4h"),
                "fundingAnnPct": round(th.get("fundingAnnPct", 0), 1),
                "own24h": round(th.get("own24h", 0), 2),
                "heldAssets": held,
            },
        })
        recent[th["coin"].upper()] = now

    _persist()
    print(f"[camel.scan] leg={leg} EMIT={len(out)} scanned={len(universe)} pool={len(pool)} "
          f"candidates={len(candidates)} open_slots={open_slots} "
          f"top_funding_annpct={round(pool[0][1] * HOURS_PER_YEAR * 100, 1)} "
          f"elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
