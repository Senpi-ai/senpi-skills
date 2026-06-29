"""LION — supervised scanner (Runtime 3.0 port of the v2 lion-producer.py).

Two-Speed-Market (K-shaped) cross-asset long/short. ONE scan.py serves BOTH books;
the runtime instance passes `leg` ("long" = the "haves" book, longs the AI complex +
crypto winners; "short" = the "have-nots" book, shorts the broad U.S. market via SP500
+ laggard alts). A faithful port — the curated thematic universe, the ABSOLUTE-trend
hard gate, the relative-strength TIEBREAKER, the per-group conviction sizing, the
venue-leverage clamp, the affordability cap, and the held/recent dedup are preserved.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin; the
     'main' + 'xyz' sections are TWO VIEWS of ONE cross-margined wallet — max() not sum)
  2. build the curated thematic universe (inputs.universe — haves for long / have-nots
     for short) intersected with the live board + a relative liquidity floor (NO $ floor;
     SKIPPED for small curated lists, per v2 Mongoose-2026-06-22 fix)
  3. cross-sectional relative-strength rank (own 24h return - the leg-universe mean),
     take the top (long) / bottom (short) rankPoolSize
  4. score each pooled name through pure scoring.score_thematic (ABSOLUTE trend is the
     gate; excess is a bonus), dedup held + recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT = marginPctBase * conviction-weight,
     capped at marginPctCap) + a per-name venue-clamped leverage; the runtime owns
     slots, dedup, execution, DSL.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the runtime
sizes the dollars, owns cooldowns/risk gates/slots, and trails the DSL exit.

FIDELITY NOTES vs lion-producer.py v1.0.0:
  - v2 conviction sizing emitted marginUsd = account_value * marginPct(FRACTION 0.18) *
    sizingWeight. The Runtime 3.0 runtime sizes from a top-level PERCENT in (0,100], so
    this port emits marginPct = marginPctBase(PERCENT 18) * sizingWeight, capped at
    marginPctCap (25, the fleet <=25% per-position rule). Same fraction of equity, same
    conviction multiplier — the cap binds only the highest-weight name (HYPE 18*1.5=27 ->
    25). A defensive guard converts a marginPctBase <= 1 (an operator who pasted the v2
    fraction 0.18) into a PERCENT (*100) and logs it. FLAGGED.
  - v2 LEG was an env var (LION_LEG) read once at import; the Runtime 3.0 port passes
    `leg` via runtime.yaml inputs (one shared scan.py, two instances). Same two books.
  - v2 emitted ALL gated, affordable candidates up to open_slots; preserved (emit-all,
    runtime applies the slots ceiling). The per-candidate AFFORDABILITY cap (never emit
    an order the wallet can't FUND, with 1.1 fee headroom) is ported verbatim — but per
    NAME since each carries a different conviction weight -> different marginPct.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=180) -> ctx.state dedup map (same
    TTL semantics, race-window held-asset suppression).
  - v2 get_positions read-sanity guard (margin/notional IN USE but EMPTY positions =
    corrupt read -> skip tick to avoid pyramiding / mis-sizing) is ported verbatim.
  - DROPPED (Runtime 3.0 read-only boundary): the v2 had NO order-lifecycle management
    (no cancel_order / resting-order purge), so nothing to drop there. The v2 LLM entry
    gate was an explicit pass-through ("honor the signal unless malformed"); the Runtime
    3.0 action is decision_mode: rule — the scan IS the decision, so the pass-through LLM
    step is correctly dropped. FLAGGED.
"""

import sys
import time

import scoring

_DEFAULT_TTL = 180   # v2 RECENT_SIGNAL_TTL_SEC — don't re-fire a name in flight
_MIN_UNIVERSE_FOR_MEDIAN_GATE = 5   # v2 minUniverseForMedianGate default (Mongoose fix)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must NOT roll
    back the whole tick. Returns None on failure so the caller's degrade path applies
    (asia-ai NASDAQ-bug class — one bad name must not kill the universe tick)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad read must not kill the universe tick
        print(f"[lion.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


# ── account / positions ──────────────────────────────────────────────────────

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). The 'main' and 'xyz'
    clearinghouse sections are TWO VIEWS of ONE cross-margined wallet — accountValue is
    taken ONCE via max() across sections, NEVER summed (v2-quirk: summing double-counts
    and makes every size 2x). Lion holds BOTH xyz equities and main-DEX crypto on one
    wallet, so both sections routinely carry positions. Free margin = equity - committed
    margin. Verbatim v2 get_positions() + the read-sanity guard."""
    if not getattr(ctx, "wallet", None):
        return 0.0, [], 0.0
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = _unwrap(ch)
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
    # v2-quirk read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but
    # an EMPTY positions list is a corrupt read — sizing or held-dedup off that re-enters
    # held names (pyramiding) and mis-sizes. Skip the tick.
    if used > 1.0 and not positions:
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + 24h vol + 24h return ─────────────

def _get_universe_meta(ctx):
    """name -> {max_leverage, vol, ret24h}. Skips delisted. Verbatim v2 get_universe_meta()
    + ret_24h() + day_vol() folded together. Keys under BOTH the raw name and its upper
    form so a 'xyz:NVDA' whitelist entry resolves against a 'XYZ:NVDA' live board name."""
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
        ctxd = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        name = inst.get("name") or ctxd.get("coin")
        if not name:
            continue
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


def _meta_for(meta_map, name):
    """Resolve a whitelist name against the live board, tolerating the xyz:/XYZ: case
    skew (v2 whitelist uses 'xyz:NVDA'; the live board returns 'XYZ:NVDA')."""
    return meta_map.get(name) or meta_map.get(name.upper())


def _fetch_candles(ctx, asset):
    """1h + 4h candles for ONE asset, dex-routed for xyz. Guarded — a bad name returns
    ([], []) and the universe loop skips it (asia-ai per-asset read-guard). Verbatim v2
    fetch_candles()."""
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
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _build_universe(whitelist, meta_map, vol_floor_pct, min_for_gate):
    """The curated thematic whitelist (haves for the long leg / have-nots for the short
    leg) intersected with the live board + a relative liquidity floor. Verbatim v2
    build_universe(): a SMALL curated list (< min_for_gate live names) SKIPS the
    median-vol gate — on a tiny list it drops an intentionally-thinner name and can
    collapse the universe to 1, bricking the book (v2 Mongoose short, 2026-06-22). Below
    the threshold, keep every live (vol>0) name — the curation IS the liquidity decision."""
    cand = []
    for name in whitelist:
        if not isinstance(name, str):
            continue
        meta = _meta_for(meta_map, name)
        if not meta:
            continue                                        # not on the live board — drop
        vol = meta.get("vol", 0.0)
        if vol <= 0:
            continue
        cand.append((name, vol))
    if not cand:
        return []
    if len(cand) < int(min_for_gate):
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
    sizing_weights = inputs.get("sizingWeights") or (
        scoring.HAVES_WEIGHTS if leg == "long" else scoring.HAVE_NOTS_WEIGHTS)
    min_score = int(inputs.get("minScore", 5))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1 (an
    # operator who pasted the v2 FRACTION 0.18) into a PERCENT so it never silently sizes
    # ~100x small (the runtime sizes (marginPct/100)*withdrawable).
    margin_pct_base = float(inputs.get("marginPctBase", 18 if leg == "long" else 15))
    if margin_pct_base <= 1.0:
        print(f"[lion.scan] WARN marginPctBase={margin_pct_base} looks like a v2 FRACTION; "
              f"converting to PERCENT ({margin_pct_base * 100})", file=sys.stderr)
        margin_pct_base *= 100.0
    margin_pct_cap = float(inputs.get("marginPctCap", 25))   # fleet <=25% per-position rule

    max_lev = int(inputs.get("maxLeverage", 5 if leg == "long" else 4))
    max_slots = int(inputs.get("maxSlots", 5 if leg == "long" else 4))
    rank_pool = int(inputs.get("rankPoolSize", 30 if leg == "long" else 16))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    min_for_gate = int(inputs.get("minUniverseForMedianGate", _MIN_UNIVERSE_FOR_MEDIAN_GATE))
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
            print(f"[lion.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        print(f"[lion.scan] leg={leg} WAITING — no account value / corrupt read", file=sys.stderr)
        _persist({"ts": now, "leg": leg, "emitted": 0, "gate": "no_value"})
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        print(f"[lion.scan] leg={leg} slots full ({len(held)}/{max_slots}) — DSL manages exit",
              file=sys.stderr)
        _persist({"ts": now, "leg": leg, "emitted": 0, "gate": "slots_full", "held": held})
        return []

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct, min_for_gate)

    # ── Cross-sectional relative strength over the thematic universe (score tiebreaker;
    #    absolute trend is the gate inside scoring.score_thematic). Verbatim v2. ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = _meta_for(meta_map, name)
        own = meta.get("ret24h") if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 1:                                          # v2-quirk: thematic universe too thin
        print(f"[lion.scan] leg={leg} WAITING — thematic universe too thin "
              f"(scanned {len(universe)})", file=sys.stderr)
        _persist({"ts": now, "leg": leg, "emitted": 0, "gate": "thin_universe",
                  "scanned": len(universe)})
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=(leg == "long"))     # leaders first (long) / laggards first (short)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue                                         # signal-dedup (race window)
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        if len(c1) < 8 or len(c4) < 6:
            continue
        thesis = scoring.score_thematic(name, c1, c4, own, excess, leg, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        print(f"[lion.scan] leg={leg} WAITING — no name cleared min_score={min_score} "
              f"(scanned {len(universe)}, pool {len(pool)}, mean_rs={mean_rs:.2f})",
              file=sys.stderr)
        _persist({"ts": now, "leg": leg, "emitted": 0, "gate": "no_candidate",
                  "scanned": len(universe), "pool": len(pool),
                  "mean_rs": round(mean_rs, 2), "min_score": min_score})
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Emit best-scoring first, sizing each by its conviction weight, capping to what the
    # wallet can actually FUND. v2-quirk: free margin decremented per commit so a mixed
    # basket of different-weight names never emits an un-fundable order (which would
    # re-emit an insufficient-funds create every tick). 1.1 = fee/slippage headroom.
    out = []
    fm = free_margin
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], sizing_weights)
        margin_pct = round(min(margin_pct_base * weight, margin_pct_cap), 4)
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)   # per-name venue clamp
        if margin_pct <= 0 or leverage <= 0:
            continue
        # affordability cap in USD terms: per-name margin = (margin_pct/100)*account_value
        per_name_margin = (margin_pct / 100.0) * account_value
        if per_name_margin * 1.1 > fm:
            continue
        out.append({
            "asset": th["coin"],
            "direction": direction,
            "marginPct": margin_pct,                            # PERCENT intent — runtime sizes the $
            "leverage": leverage,                               # already venue-clamped
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": direction,
                "reasons": th["reasons"][:6],
                "trend4h": th.get("trend4h"),
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
                "weight": round(weight, 2),
                "heldAssets": held,
            },
        })
        recent[th["coin"].upper()] = now
        open_slots -= 1
        fm -= per_name_margin * 1.1

    _persist({"ts": now, "leg": leg, "emitted": len(out),
              "gate": "pass" if out else "unaffordable",
              "scanned": len(universe), "pool": len(pool),
              "candidates": len(candidates), "mean_rs": round(mean_rs, 2),
              "held": held})
    verb = "EMIT" if out else "WAITING"
    print(f"[lion.scan] {verb} leg={leg} scanned={len(universe)} pool={len(pool)} "
          f"candidates={len(candidates)} emitted={len(out)} mean_rs={mean_rs:.2f} "
          f"elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
