"""EEL — supervised scanner (shared verbatim by both books).

Direction-parametrized: the `long` instance passes leg=LONG (long the AI-power complex —
uranium / gas-fired power / grid copper / fuel cells / rare-earth); the `short` instance
passes leg=SHORT (short crude oil — Brent + WTI). A faithful Runtime 3.0 port of the v2
eel-producer.py — the curated thematic universe build, the ABSOLUTE-trend gate (RS as a
tiebreaker), and the per-group conviction sizing are preserved exactly. Read-only,
single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin; dual-DEX
     equity via max(), never sum())
  2. build the live universe: the curated thematic whitelist (config.universe — haves for
     the long leg, have-nots for the short leg) intersected with the live instrument board +
     a relative-to-market liquidity floor (NO hardcoded $; small-list median-gate BYPASS so
     a tiny curated list never collapses to 1 — the Mongoose-short fix)
  3. cross-sectional relative-strength rank over the thematic universe (own 24h - universe
     mean), take the top (long) / bottom (short) rankPoolSize
  4. score each pooled name with the v2 ABSOLUTE-trend gate (RS tiebreaker), dedup held +
     recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT, runtime sizes) = base marginPct ×
     per-group conviction weight, + a per-name venue-clamped leverage; the runtime owns
     slots, dedup, execution, DSL.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (asia-ai NASDAQ-bug class). The universe was
validated against the live HL xyz board at authoring time (all 7 names present, none
delisted); the per-asset guard is the runtime backstop for a name that delists or goes thin
between authoring and a live tick.

FIDELITY NOTES vs eel-producer.py v1.0.0:
  - v2 was a long-lived producer_daemon driven by EEL_LEG env into one of two books; this
    port is two supervised scan() instances (long/, short/) selected by inputs.leg. The
    per-tick decision logic (universe → RS rank → score_thematic gate → conviction sizing →
    fund-cap) is identical.
  - v2 marginPct was a FRACTION (long 0.18, short 0.15) used as account_value*marginPct.
    This port takes marginPct as a PERCENT (18 / 15) and emits a top-level marginPct =
    base_pct × sizing_weight; the runtime sizes (marginPct/100)*withdrawable. A defensive
    <=1.0 -> ×100 guard catches a pasted v2 fraction. The per-group sizing WEIGHTS and the
    [0.1,3.0] clamp are verbatim.
  - v2 push_signal() emitted score min(score/9.0, 1.0) on the wire; the 3.0 scaffold owns the
    wire envelope, so the raw integer score rides on data{} (NORM_DIV kept in scoring for
    reference only).
  - DROPPED: nothing order-lifecycle — v2 Eel had no cancel_order / resting-order purge; the
    only mutation path was push_signal (now the scaffold's job). v2 record_signal() /
    was_recently_signaled() JSON cache -> ctx.state dedup map (same TTL semantics).
  - v2 recent-signal TTL was 180s; preserved via recentSignalTtlSeconds.
"""

import sys
import time

import scoring

_DEFAULT_TTL = 180   # 3m: match v2 RECENT_SIGNAL_TTL_SEC — don't re-fire a name in flight


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must NOT roll
    back the whole tick. Returns None on failure so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[eel.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


# ── account / positions ──────────────────────────────────────────────────────

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). The 'main' and 'xyz'
    clearinghouse sections are TWO VIEWS of ONE cross-margined wallet — accountValue is
    taken ONCE via max() across sections, NEVER summed (v2-quirk: summing double-counts and
    makes every size 2x). Free margin = equity - committed margin. Includes the v2 read-
    sanity guard (margin in use but empty positions -> corrupt read, skip tick)."""
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
    # v2-quirk read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but an
    # EMPTY positions list is a corrupt read — sizing or held-dedup off that re-enters held
    # names (pyramiding). Skip the tick.
    if used > 1.0 and not positions:
        print("[eel.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + 24h vol + 24h return ─────────────

def _get_universe_meta(ctx):
    """name -> {max_leverage, vol (dayNtlVlm), ret24h}. Skips delisted. Verbatim v2
    get_universe_meta() + ret_24h() + day_vol() folded together."""
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
    """1h + 4h candles for ONE asset, dex-routed for xyz. Guarded — a bad name returns
    ([], []) and the universe loop skips it (asia-ai per-asset read-guard)."""
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


def _build_universe(whitelist, meta_map, vol_floor_pct, min_universe_for_gate):
    """The curated thematic whitelist intersected with the live board + a relative liquidity
    floor (>= vol_floor_pct of the whitelist's median 24h vol; NO hardcoded $). Verbatim v2
    build_universe() — INCLUDING the small-list BYPASS: below min_universe_for_gate live
    names, skip the median gate entirely (on a tiny curated list it drops an intentionally-
    thinner name and can collapse the universe to 1, bricking the book — the Mongoose-short
    fix, 2026-06-22). The curation IS the liquidity decision below the threshold."""
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
    if len(cand) < int(min_universe_for_gate):              # v2-quirk: small-list bypass
        return [n for n, _ in cand]
    vols = sorted(v for _, v in cand)
    median = vols[len(vols) // 2]
    floor = vol_floor_pct * median
    return [n for n, v in cand if v >= floor]


def _norm_pct(p):
    """Defensive marginPct guard (dire/koala): a value <=1.0 is a pasted v2 FRACTION
    (0.18) — multiply by 100 to recover the PERCENT (18). A value already in (1,100] is
    treated as a percent as-is."""
    p = float(p)
    return p * 100.0 if 0 < p <= 1.0 else p


def scan(inputs, ctx):
    run_start = time.time()
    leg = (inputs.get("leg", "long") or "long").strip().lower()
    direction = "LONG" if leg == "long" else "SHORT"
    whitelist = inputs.get("universe", [])
    sizing_weights = inputs.get("sizingWeights", {"_default": 1.0})
    min_score = float(inputs.get("minScore", 5))
    base_margin_pct = _norm_pct(inputs.get("marginPct", 18))   # PERCENT of withdrawable (0,100]
    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 4))
    rank_pool = int(inputs.get("rankPoolSize", 12))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    min_universe_for_gate = int(inputs.get("minUniverseForMedianGate", 5))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if (ctx.state is not None and len(ctx.state) > 0) else {}
    signaled = {k: v for k, v in (last.get("signaled") or {}).items()
                if isinstance(v, (int, float)) and (now - v) < (ttl * 4)}  # prune at 4x TTL (v2)

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[eel.scan] WARNING: state append failed; next tick may re-emit a "
                  f"suppressed signal: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist({"ts": now, "leg": leg, "emitted": False, "note": "no account value / corrupt read"})
        return []                                            # no value / corrupt read — skip tick
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist({"ts": now, "leg": leg, "emitted": False, "held": held, "note": "slots full"})
        return []                                            # book full — runtime also caps via slots

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct, min_universe_for_gate)

    # ── Cross-sectional relative-strength rank over the thematic universe (v2-quirk: used as
    #    a score tiebreaker; absolute trend is the gate inside score_thematic) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = meta.get("ret24h") if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 1:                                          # v2-quirk: thematic universe too thin
        _persist({"ts": now, "leg": leg, "scanned": len(universe), "emitted": False,
                  "note": "WAITING — thematic universe too thin to evaluate"})
        print(f"[eel.scan] leg={leg} WAITING — thematic universe too thin "
              f"(scanned={len(universe)})", file=sys.stderr)
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=(leg == "long"))     # haves-up first (long) / have-nots-down first (short)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        last_sig = signaled.get(name.upper())
        if last_sig is not None and (now - last_sig) < ttl:  # signal-dedup
            continue
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        if len(c1) < 8 or len(c4) < 6:
            continue
        thesis = scoring.score_thematic(name, c1, c4, excess, own, leg, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist({"ts": now, "leg": leg, "scanned": len(universe), "ranked_pool": len(pool),
                  "candidates": 0, "emitted": False, "mean_rs_24h": round(mean_rs, 2),
                  "held": held, "note": f"WAITING — no name cleared min score {min_score:.0f}"})
        print(f"[eel.scan] leg={leg} WAITING — no name cleared min score {min_score:.0f}; "
              f"scanned={len(universe)} pool={len(pool)} held={held}", file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: never emit more than the wallet can actually FUND. An open slot with no free
    # margin re-emits an un-fillable order every tick (insufficient-funds spam). free margin
    # is decremented per emit as (marginPct/100)*accountValue × weight × 1.1 fee headroom —
    # a mixed-size basket (URNM big, BE small) never emits an un-fundable order.
    out = []
    emitted = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], sizing_weights)
        margin_pct = base_margin_pct * weight                # PERCENT × conviction weight
        margin_usd = (margin_pct / 100.0) * account_value    # for the fund-cap check only
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)  # v2-quirk: per-name venue clamp
        if margin_pct <= 0 or leverage <= 0:
            continue
        if margin_usd * 1.1 > free_margin:                   # 1.1 = fee/slippage headroom
            continue
        out.append({
            "asset": th["coin"],
            "direction": direction,
            "marginPct": round(margin_pct, 4),               # PERCENT intent — runtime sizes the $
            "leverage": leverage,                            # already venue-clamped
            "data": {
                "score": th["score"],
                "direction": direction,
                "reasons": th["reasons"][:6],
                "weight": round(weight, 3),
                "trend4h": th.get("trend4h"),
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
            },
        })
        emitted.append({"coin": th["coin"], "score": th["score"], "leverage": leverage,
                        "marginPct": round(margin_pct, 2), "weight": round(weight, 2)})
        open_slots -= 1
        free_margin -= margin_usd * 1.1
        signaled[th["coin"].upper()] = now

    _persist({"ts": now, "leg": leg, "scanned": len(universe), "ranked_pool": len(pool),
              "candidates": len(candidates), "emitted": bool(out), "signals": emitted,
              "mean_rs_24h": round(mean_rs, 2), "held": held})
    print(f"[eel.scan] leg={leg} {'EMIT' if out else 'WAITING'} scanned={len(universe)} "
          f"pool={len(pool)} candidates={len(candidates)} emitted={len(out)} "
          f"mean_rs={mean_rs:.2f} elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
