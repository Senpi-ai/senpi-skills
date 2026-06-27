"""COUGAR — supervised scanner (shared verbatim by both books).

Direction-parametrized: the `long` instance passes leg=LONG (long the relative-strength
LEADERS of the equity cross-section); the `short` instance passes leg=SHORT (short the
LAGGARDS). A faithful Runtime 3.0 port of the v2 cougar-producer.py — the cross-sectional
relative-strength rank + dispersion scoring is preserved exactly. Read-only, single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin)
  2. build the live universe: the validated xyz equity whitelist intersected with the
     live instrument board + a relative liquidity floor (NO hardcoded $)
  3. rank the universe by 24h relative strength (own 24h return - the universe mean),
     take the top (long) / bottom (short) rankPoolSize
  4. score each pooled name with the v2 dispersion gates, dedup held + recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT, the runtime sizes) + a per-name
     venue-clamped leverage; the runtime owns slots, dedup, execution, DSL.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (asia-ai NASDAQ-bug class). The universe was
validated against the live HL xyz board at authoring time; the per-asset guard is the
runtime backstop for a name that delists or goes thin between authoring and a live tick."""

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
        print(f"[cougar.scan] {name} read failed: {exc!r}", file=sys.stderr)
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
    # v2-quirk read-sanity guard (funding/$0 glitch): margin/notional IN USE but an EMPTY
    # positions list is a corrupt read — sizing or held-dedup off that re-enters held names.
    if used > 1.0 and not positions:
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── live instrument board: venue leverage + 24h vol + 24h return ─────────────

def _get_universe_meta(ctx):
    """name -> {max_leverage, dayNtlVlm, ret24h}. Skips delisted. Verbatim v2
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
    None and the universe loop skips it (asia-ai per-asset read-guard)."""
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


def _build_universe(whitelist, meta_map, vol_floor_pct):
    """The validated xyz equity whitelist intersected with the live board + a relative
    liquidity floor (>= vol_floor_pct of the whitelist's median 24h vol; NO hardcoded $).
    Names not live / too thin are dropped. Verbatim v2 build_universe()."""
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
    vols = sorted(v for _, v in cand)
    median = vols[len(vols) // 2]
    floor = vol_floor_pct * median
    return [n for n, v in cand if v >= floor]


def scan(inputs, ctx):
    run_start = time.time()
    leg = (inputs.get("leg", "long") or "long").strip().lower()
    direction = "LONG" if leg == "long" else "SHORT"
    whitelist = inputs.get("equities", [])
    min_score = int(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 20))          # PERCENT of withdrawable (0,100]
    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 4))
    rank_pool = int(inputs.get("rankPoolSize", 8))
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
            print(f"[cougar.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        return []                                            # no value / corrupt read — skip tick
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        return []                                            # book full — runtime also caps via slots

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct)

    # ── Cross-sectional relative-strength rank over the equity universe (v2-quirk) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = meta.get("ret24h") if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 4:                                          # v2-quirk: too thin to rank a cross-section
        _persist()
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
        thesis = scoring.score_dispersion(name, c1, c4, excess, own, leg, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist()
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: never emit more than the wallet can actually FUND. An open slot with no
    # free margin re-emits an un-fillable order every tick (insufficient-funds spam).
    # free margin is sized as (marginPct/100)*accountValue per name + 1.1 fee headroom.
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

    _persist()
    print(f"[cougar.scan] leg={leg} scanned={len(universe)} pool={len(pool)} "
          f"candidates={len(candidates)} emitted={len(out)} mean_rs={mean_rs:.2f} "
          f"elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
