"""OCTOPUS — supervised scanner (shared verbatim by both books).

Direction-parametrized: the `long` instance passes leg=LONG (long the relative-strength
LEADERS of the live crypto cross-section); the `short` instance passes leg=SHORT (short the
LAGGARDS). A faithful Runtime 3.0 port of the v2 octopus-producer.py — the cross-sectional
relative-strength rank + dispersion scoring is preserved exactly. Read-only, single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin)
  2. build the live universe: ALL main-DEX crypto perps (XYZ excluded), capped to the top
     universeMaxNames by 24h volume, then a relative-to-market liquidity floor (NO hardcoded
     $) — names with 24h vol < volFloorPctOfMedian x the cohort median are dropped
  3. rank the universe by 24h relative strength (own 24h return - the universe mean), take
     the top (long) / bottom (short) rankPoolSize names
  4. pull 1h+4h candles for ONLY the pooled names, score with the v2 dispersion gates, dedup
     held + recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT, the runtime sizes) + a per-name
     venue-clamped leverage; the runtime owns slots, dedup, execution, DSL.

FIDELITY NOTES vs octopus-producer.py v1.0.0:
  - v2 ran one long-lived producer_daemon driven by OCTOPUS_LEG; here the leg is an `inputs`
    field (long/runtime.yaml -> leg=long, short/runtime.yaml -> leg=short). One shared
    scan.py + scoring.py, two runtime instances.
  - v2 stored marginPct as a FRACTION (0.20) and computed margin_usd = account_value *
    marginPct, then emitted the absolute USD. Runtime 3.0 sizes from a PERCENT intent, so
    this port carries marginPct=20 (PERCENT) in runtime.yaml and emits a top-level
    `marginPct`; the runtime sizes (marginPct/100)*withdrawable. A defensive guard converts
    a value <= 1.0 (an operator who pasted the v2 fraction 0.20) to a PERCENT (*100). FLAGGED.
  - v2's per-tick `affordable` cap (never emit more entries than free margin can FUND) is
    preserved verbatim — an open slot with no free margin would otherwise re-emit an
    un-fillable order every tick (insufficient-funds spam).
  - v2 push_signal + record_signal -> ctx.state dedup map (same 180s TTL semantics, 4x-TTL
    prune via the recent-window filter).
  - The v2 LLM entry gate was an explicit pass-through ("honor the signal unless malformed").
    The Runtime 3.0 action is decision_mode: rule — the scan IS the decision — so the
    pass-through LLM step is correctly dropped. The scan already applied the RS rank, 4h/1h
    trend confirmation, the venue leverage clamp, and held-asset/race dedup.
  - DROPPED (read-only scan cannot mutate): nothing — the v2 producer had no order-lifecycle
    management (no cancel_order / has_resting_orders / stale-order purge). Pure emit path.
  - v2's unused first-seen / recent-signals JSON files collapse into ctx.state.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (asia-ai NASDAQ-bug class). Per the scan
contract, ANY uncaught exception rolls the whole tick back to [] — one bad read would
silently kill all emits."""

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
        print(f"[octopus.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


# ── account / positions ──────────────────────────────────────────────────────

def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). The 'main' and 'xyz'
    clearinghouse sections are TWO VIEWS of ONE cross-margined wallet — accountValue is
    taken ONCE via max() across sections, NEVER summed (v2-quirk: summing double-counts
    and makes every size 2x). Free margin = equity - committed margin."""
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
    # v2-quirk read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but an
    # EMPTY positions list is a corrupt read — sizing or held-dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    if used > 1.0 and not positions:
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + 24h vol + 24h return ─────────────

def _get_universe_meta(ctx):
    """name -> {max_leverage, vol, ret24h}. Skips delisted. Verbatim v2 get_universe_meta()
    + ret_24h() + day_vol() folded together (markPx/prevDayPx/dayNtlVlm from the board)."""
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
    """1h + 4h candles for ONE asset. Guarded — a bad name returns ([], []) and the universe
    loop skips it (asia-ai per-asset read-guard)."""
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


def _build_universe(meta_map, max_names, vol_floor_pct):
    """Resolve the live liquid main-DEX crypto cross-section to rank this tick. Verbatim v2
    build_universe(): a name qualifies if it is a main-DEX perp (NO xyz: prefix), survives
    the top-`max_names`-by-24h-volume cap, and is in the liquid cohort (24h vol >=
    vol_floor_pct of the top-N median — relative-to-market, NO hardcoded $ floor). XYZ
    equities are excluded (Octopus ranks crypto dispersion; XYZ has no clean peer group)."""
    seen, pool = set(), []
    for name, meta in meta_map.items():
        if not isinstance(name, str) or name.lower().startswith("xyz:"):
            continue
        key = name.upper()
        if key != name:
            continue                                         # skip the .upper() alias dup-key
        if key in seen:
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
    # Relative-to-market liquidity gate (NO hardcoded $): keep only names whose 24h volume is
    # >= vol_floor_pct of the top-N cohort's median. The top-N cap already restricts to the
    # most-liquid majors; this drops the anomalously thin tail. Budget-independent.
    vols = sorted(v for _, v in pool)
    median = vols[len(vols) // 2]
    floor = vol_floor_pct * median
    return [n for n, v in pool if v >= floor]


def scan(inputs, ctx):
    run_start = time.time()
    leg = (inputs.get("leg", "long") or "long").strip().lower()
    direction = "LONG" if leg == "long" else "SHORT"
    min_score = int(inputs.get("minScore", 5))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1.0 (an
    # operator who pasted the v2 FRACTION 0.20) into a PERCENT so it never silently sizes
    # ~100x small (resolve-margin sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", 20))
    if margin_pct <= 1.0:
        print(f"[octopus.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 4))
    max_names = int(inputs.get("universeMaxNames", 40))
    rank_pool = int(inputs.get("rankPoolSize", 12))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist(result=None):
        if ctx.state is None:
            return
        rec = {"recent": recent}
        if result is not None:
            rec["signaled"] = result.get("emitted", 0)
            rec["result"] = result
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[octopus.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        print(f"[octopus.scan] leg={leg} WAITING — no account value / corrupt read",
              file=sys.stderr)
        _persist({"emitted": 0, "gate": "no_account_value"})
        return []                                            # no value / corrupt read — skip tick
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        print(f"[octopus.scan] leg={leg} WAITING — slots full ({len(held)}/{max_slots})",
              file=sys.stderr)
        _persist({"emitted": 0, "gate": "slots_full", "held": sorted(held_set)})
        return []                                            # book full — runtime also caps via slots

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(meta_map, max_names, vol_floor_pct)

    # ── Cross-sectional relative-strength rank (one pass, no candle fetch) (v2-quirk) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = meta.get("ret24h") if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 5:                                          # v2-quirk: too thin to rank a cross-section
        print(f"[octopus.scan] leg={leg} WAITING — cross-section too thin to rank "
              f"({len(rs)} ranked, scanned {len(universe)})", file=sys.stderr)
        _persist({"emitted": 0, "gate": "thin_cross_section", "scanned": len(universe)})
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=(leg == "long"))     # leaders first (long) / laggards first (short)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue                                         # signal-dedup race window
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        if len(c1) < 8 or len(c4) < 6:
            continue
        thesis = scoring.score_dispersion(name, c1, c4, excess, own, leg, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        print(f"[octopus.scan] leg={leg} WAITING — no name cleared min score {min_score} "
              f"(scanned {len(universe)}, pool {len(pool)}, mean_rs {mean_rs:.2f})",
              file=sys.stderr)
        _persist({"emitted": 0, "gate": "no_candidate", "scanned": len(universe),
                  "pool": len(pool), "mean_rs": round(mean_rs, 2)})
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: never emit more than the wallet can actually FUND. An open slot with no free
    # margin re-emits an un-fillable order every tick (insufficient-funds spam). free margin
    # is sized as (marginPct/100)*accountValue per name + 1.1 fee/slippage headroom.
    per_name_margin = (margin_pct / 100.0) * account_value
    affordable = int(free_margin / (per_name_margin * 1.1)) if per_name_margin > 0 else 0
    to_emit = candidates[:max(0, min(open_slots, affordable))]

    out = []
    for th in to_emit:
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)  # v2-quirk: per-name venue clamp
        if leverage <= 0:
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
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
            },
        })
        recent[th["coin"].upper()] = now

    _persist({"emitted": len(out), "gate": "emit", "scanned": len(universe),
              "pool": len(pool), "candidates": len(candidates),
              "mean_rs": round(mean_rs, 2),
              "coins": [o["asset"] for o in out]})
    print(f"[octopus.scan] leg={leg} {'EMIT' if out else 'WAITING'} scanned={len(universe)} "
          f"pool={len(pool)} candidates={len(candidates)} emitted={len(out)} "
          f"mean_rs={mean_rs:.2f} elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
