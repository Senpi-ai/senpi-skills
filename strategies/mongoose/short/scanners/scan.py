"""MONGOOSE — supervised scanner (shared verbatim by both books).

Direction-parametrized: the `long` instance passes leg=LONG (long the on-chain financial
rails — HYPE + CRCL + COIN + HOOD + MSTR + PURRDAT); the `short` instance passes leg=SHORT
(short legacy finance + broad financial-beta — BX + SP500). A faithful Runtime 3.0 port of
the v2 mongoose-producer.py — the curated thematic universe build + absolute-trend scoring +
cross-sectional relative-strength tiebreaker + per-name conviction sizing is preserved
exactly. Read-only, single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin)
  2. build the curated thematic universe: config.universe intersected with the live board +
     a relative-to-market liquidity floor — BUT skip the median gate on a SMALL CURATED list
     (< minUniverseForMedianGate), where the curation IS the liquidity decision (v2-quirk)
  3. rank the universe by 24h relative strength (own 24h return - the universe mean), take the
     top (long) / bottom (short) rankPoolSize. Cross-sectional excess is a TIEBREAKER only —
     a 1-name universe still trades (excess vs mean = 0; scored on its own absolute trend)
  4. score each pooled name with the v2 absolute-trend gate, dedup held + recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT, conviction-weighted, the runtime sizes the $)
     + a per-name venue-clamped leverage; the runtime owns slots, dedup, execution, DSL.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (asia-ai NASDAQ-bug class). The universe was
validated against the live HL board (main + xyz) at authoring time; the per-asset guard is the
runtime backstop for a name that delists or goes thin between authoring and a live tick.

FIDELITY NOTES vs mongoose-producer.py v1.0.0:
  - v2 stored marginPct as a FRACTION (0.18 long / 0.15 short) and emitted an absolute
    marginUsd = account_value * marginPct * sizingWeights[name]. This port follows the
    Runtime 3.0 contract: it emits a top-level `marginPct` PERCENT intent = (marginPct
    fraction * 100) * sizingWeights[name], and the runtime sizes (marginPct/100)*withdrawable.
    The per-name conviction weighting is identical; only the unit (fraction->percent) and the
    sizing owner (producer->runtime) changed. A defensive guard converts an inputs.marginPct
    that was pasted as a fraction (<=1.0) by x100.
  - v2's affordability cap decremented a running free_margin and applied a 1.1 fee/slippage
    headroom per emit so a mixed-size basket never emits an un-fundable order. Preserved: the
    per-name weighted margin is checked against a running free_margin (1.1 headroom) and the
    venue-min-notional floor; un-fundable / sub-min names are skipped, not emitted.
  - DROPPED (v2 had NONE of these, but for the record): no order-lifecycle management
    (cancel_order / resting-order purge) existed in the v2 producer, so nothing to drop.
  - v2 push_signal() POSTed each emit and recorded a per-leg recent-signals.json dedup cache;
    here that becomes the ctx.state dedup map (same TTL semantics) and the runtime POSTs.
  - v2 emitted min(score/9.0, 1.0) as the [0,1] wire score; the 3.0 scaffold owns the wire
    envelope, so the raw integer score rides on data{} instead.
"""

import sys
import time

import scoring

_DEFAULT_TTL = 180   # 3m: match v2 RECENT_SIGNAL_TTL_SEC — don't re-fire a name in flight


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must NOT roll back
    the whole tick. Returns None on failure so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[mongoose.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


# ── account / positions ──────────────────────────────────────────────────────

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). The 'main' and 'xyz'
    clearinghouse sections are TWO VIEWS of ONE cross-margined wallet — accountValue is taken
    ONCE via max() across sections, NEVER summed (v2-quirk: summing double-counts and makes
    every size 2x). Mongoose holds both xyz equities AND main-DEX crypto on the same wallet, so
    both sections routinely carry positions; iterate both. Free margin = equity - committed
    margin. Verbatim v2 cfg.get_positions (incl. the read-sanity guard)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return -1.0, [], 0.0                         # call failed -> non-positive => skip tick
    data = _unwrap(ch)
    if not isinstance(data, dict):
        return -1.0, [], 0.0
    positions, account_value, used = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        used = max(used, float(ms.get("totalMarginUsed", 0) or 0),
                   abs(float(ms.get("totalNtlPos", 0) or 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": float(pos.get("marginUsed", 0) or 0),
            })
    # v2-quirk read-sanity guard (funding/$0 glitch, June 2026): a corrupt read can report
    # margin/notional IN USE while returning an EMPTY positions list — sizing or held-dedup off
    # that re-enters held names (pyramiding) and mis-sizes. Skip the tick (non-positive value).
    if used > 1.0 and not positions:
        print("[mongoose.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return -1.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + 24h vol + 24h return ─────────────

def _get_universe_meta(ctx):
    """name -> {max_leverage, vol, ret24h}. Skips delisted. Verbatim v2 get_universe_meta() +
    ret_24h() + day_vol() folded together. Names are keyed both raw and uppercased; the live
    XYZ board already returns the 'xyz:' prefix, so the curated 'xyz:CRCL' etc. match directly."""
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
        ctxd = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        try:
            mark = float(ctxd.get("markPx", 0) or 0)
            prev = float(ctxd.get("prevDayPx", 0) or 0)
        except (TypeError, ValueError):
            mark = prev = 0.0
        ret24 = ((mark - prev) / prev * 100.0) if (prev > 0 and mark > 0) else None
        try:
            vol = float(ctxd.get("dayNtlVlm", 0) or 0)
        except (TypeError, ValueError):
            vol = 0.0
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ret24h": ret24,
            "vol": vol,
        }
        out[name] = entry
        out[name.upper()] = entry
    return out


def _fetch_candles(ctx, asset):
    """1h + 4h candles for ONE asset, dex-routed for xyz. Guarded — a bad name returns ([],[])
    and the universe loop skips it (asia-ai per-asset read-guard)."""
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


def _build_universe(whitelist, meta_map, vol_floor_pct, min_for_gate):
    """The curated thematic whitelist intersected with the live board + a relative liquidity
    floor (>= vol_floor_pct of the whitelist's median 24h vol; NO hardcoded $). Verbatim v2
    build_universe(), INCLUDING the curated-list exception: on a SMALL list
    (< minUniverseForMedianGate) the median gate is HARMFUL (it drops an intentionally-included
    but thinner name e.g. xyz:BX vs xyz:SP500 -> universe collapses to 1 -> the book never
    trades), so every live (vol>0) name is kept — the curation IS the liquidity decision."""
    cand = []
    for name in whitelist:
        if not isinstance(name, str):
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue                                        # not on the live board — drop
        vol = meta.get("vol", 0.0)
        if vol <= 0:
            continue
        cand.append((name, vol))
    if not cand:
        return []
    if len(cand) < int(min_for_gate):                       # v2-quirk: curated small list — keep all live
        return [n for n, _ in cand]
    vols = sorted(v for _, v in cand)
    median = vols[len(vols) // 2]
    floor = vol_floor_pct * median
    return [n for n, v in cand if v >= floor]


def scan(inputs, ctx):
    run_start = time.time()
    leg = (inputs.get("leg", "long") or "long").strip().lower()
    direction = "LONG" if leg == "long" else "SHORT"
    whitelist = inputs.get("universe", [])
    min_score = int(inputs.get("minScore", 5))

    # marginPct INTENT (PERCENT of withdrawable, (0,100]). v2 stored a FRACTION (0.18); the
    # defensive guard treats a pasted <=1.0 as a fraction and x100 (dire/koala pattern).
    margin_pct = float(inputs.get("marginPct", 18))
    if margin_pct <= 1.0:
        margin_pct *= 100.0

    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 5))
    rank_pool = int(inputs.get("rankPoolSize", 12))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    min_for_gate = int(inputs.get("minUniverseForMedianGate", 5))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist(result=None):
        if ctx.state is None:
            return
        try:
            rec = {"recent": recent}
            if result is not None:
                rec["result"] = result
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[mongoose.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        # Either a genuinely empty account OR a corrupt clearinghouse read — never size or dedup
        # off a bad read (that pyramided a name to 61% of book in v2 on 2026-06-22). Skip tick.
        _persist({"ts": now, "leg": leg, "emitted": 0,
                  "note": "skip — non-positive / inconsistent clearinghouse read"})
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist({"ts": now, "leg": leg, "emitted": 0, "held": held, "note": "slots full"})
        print(f"[mongoose.scan] leg={leg} WAITING — slots full ({len(held)}/{max_slots})",
              file=sys.stderr)
        return []

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct, min_for_gate)

    # ── Cross-sectional relative strength over the thematic universe (a score TIEBREAKER;
    #    absolute trend is the gate inside score_thematic) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = meta.get("ret24h") if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 1:
        # v2-quirk: excess vs the mean is a TIEBREAKER, not a gate — a 1-name universe still
        # trades (excess = 0; scored on its own absolute trend). Only a truly EMPTY universe
        # aborts (v2 fix: was len < 2, which bricked a 1-name book).
        _persist({"ts": now, "leg": leg, "scanned": len(universe), "emitted": 0,
                  "note": "WAITING — no live names in the thematic universe"})
        print(f"[mongoose.scan] leg={leg} WAITING — empty thematic universe (scanned={len(universe)})",
              file=sys.stderr)
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=(leg == "long"))     # leaders first (long) / laggards first (short)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue                                         # signal-dedup
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        if len(c1) < 8 or len(c4) < 6:
            continue
        thesis = scoring.score_thematic(name, c1, c4, excess, own, leg, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist({"ts": now, "leg": leg, "scanned": len(universe), "pool": len(pool),
                  "candidates": 0, "emitted": 0, "mean_rs": round(mean_rs, 2), "held": held,
                  "note": f"WAITING — no name cleared min score {min_score}"})
        print(f"[mongoose.scan] leg={leg} WAITING — no name cleared min score {min_score}; "
              f"scanned={len(universe)} pool={len(pool)} held={held}", file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: emit best-scoring first, each sized by its conviction weight, capped to what the
    # wallet can actually FUND. free_margin decrements as we commit (1.1 fee/slippage headroom)
    # so a mixed basket of different-sized names never emits an un-fundable order (which would
    # re-emit an insufficient-funds create_position every tick). Sub-min-notional names skipped.
    min_notional = max(account_value * min_notional_pct, venue_min_notional)

    out = []
    emitted = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], leg, inputs)
        name_margin_pct = round(margin_pct * weight, 4)      # PERCENT, conviction-weighted
        margin_usd = (name_margin_pct / 100.0) * account_value
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)  # v2-quirk: per-name venue clamp
        if name_margin_pct <= 0 or leverage <= 0:
            continue
        notional = margin_usd * leverage
        if notional < min_notional:                          # below HL venue minimum order value
            continue
        if margin_usd * 1.1 > free_margin:                   # 1.1 = fee/slippage headroom
            continue
        out.append({
            "asset": th["coin"],
            "direction": direction,
            "marginPct": name_margin_pct,                    # PERCENT intent — runtime sizes the $
            "leverage": leverage,                            # already venue-clamped
            "data": {
                "score": th["score"],
                "direction": direction,
                "reasons": th["reasons"][:6],
                "weight": weight,
                "trend4h": th.get("trend4h"),
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
            },
        })
        recent[th["coin"].upper()] = now
        free_margin -= margin_usd * 1.1
        open_slots -= 1
        emitted.append({"coin": th["coin"], "score": th["score"], "lev": leverage,
                        "marginPct": name_margin_pct, "weight": weight,
                        "excess": round(th.get("excess", 0), 2)})

    _persist({"ts": now, "leg": leg, "scanned": len(universe), "pool": len(pool),
              "candidates": len(candidates), "emitted": len(out), "mean_rs": round(mean_rs, 2),
              "held": held, "emits": emitted})
    if out:
        print(f"[mongoose.scan] leg={leg} EMIT {len(out)} | scanned={len(universe)} "
              f"pool={len(pool)} candidates={len(candidates)} mean_rs={mean_rs:.2f} "
              f"elapsed={time.time() - run_start:.2f}s | {emitted}", file=sys.stderr)
    else:
        print(f"[mongoose.scan] leg={leg} WAITING — candidates unfundable/sub-min; "
              f"scanned={len(universe)} candidates={len(candidates)} "
              f"elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
