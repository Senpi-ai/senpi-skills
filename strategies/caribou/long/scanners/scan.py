"""CARIBOU — supervised scanner (shared verbatim by both sleeves).

Direction-parametrized cross-asset TREND FUND (managed futures / CTA): the `long` instance
passes leg=long (longs confirmed UPTRENDS), the `short` instance passes leg=short (shorts
confirmed DOWNTRENDS). A faithful Runtime 3.0 port of the v2 caribou-producer.py v1.0.0 — the
class bucketing, momentum ranking, time-series trend scoring, vol-parity sizing and per-class
diversification cap are preserved EXACTLY. Read-only, single-pass.

Per tick:
  1. read the wallet clearinghouse (account value via max(main,xyz) — never sum; held names;
     current per-class deployed margin for the class cap)
  2. build the universe from the WHOLE live instrument board (crypto + xyz stocks/indices/
     metals/energy), bucket by asset class, apply a per-class top-N + relative-median liquidity
     gate, then rank each class by 24h momentum IN THE SLEEVE'S DIRECTION and keep the top
     `rankPerClass` movers — all from instrument-board CONTEXT, NO candle fetch here
  3. confirm + score the trend on each finalist (candle fetch happens here), keep score>=minScore
  4. sort by score, apply the per-class margin cap (40% of equity per class) so the book stays
     diversified across classes — the entire CTA edge
  5. emit a top-level marginPct INTENT (PERCENT, vol-parity sized) + a per-name venue-clamped
     leverage; the runtime owns slots, dedup, affordability, execution, and the DSL exit.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (asia-ai NASDAQ-bug class). Per the scan contract
ANY uncaught exception rolls the whole tick back to [] — one bad read would silently kill all
emits — so every read degrades (skip asset / empty universe / neutral) instead of propagating.

FIDELITY NOTES vs caribou-producer.py v1.0.0:
  - v2 sized in absolute marginUsd via vol_parity_margin() = account_value * pct (a FRACTION
    clamped to [0.03, 0.15]). This port emits the SAME vol-parity pct as a PERCENT (marginPct,
    pct*100) and the runtime sizes (marginPct/100)*withdrawable. Formula + clamps verbatim.
  - PER-CLASS MARGIN CAP preserved (the core CTA diversification edge): current per-class
    deployed margin is read from open positions and expressed as % of equity; a candidate whose
    class would exceed classMarginCapPct (40%) is skipped. Because the runtime (not the scan)
    sizes the actual dollars, this cap is computed on the EMITTED marginPct intents + the
    observed deployed margin %, which is the faithful percent-space equivalent of the v2 USD cap.
  - DROPPED (runtime owns these in 3.0; FLAGGED): the v2 affordability gate (margin*1.1 >
    free_margin) and the open-slots accounting that capped emits to (maxSlots - held) — both are
    the runtime's job now (slots + reconciliation). The scan still suppresses HELD names and
    recently-signalled names (belt-and-suspenders dedup in ctx.state) and emits at most
    `maxSlots` candidates so it never floods.
  - v2 normalised the wire score to min(score/8, 1.0); the 3.0 scaffold owns the wire envelope,
    so the raw integer score rides on data{}.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL semantics).
  - v2 read-sanity guard (margin in use + empty positions -> skip tick) preserved verbatim.
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
        print(f"[caribou.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


# ── account / positions (verbatim v2 get_positions; dual-DEX max(), read-sanity guard) ──

def _get_positions(ctx):
    """Returns (account_value, [position dicts]). The 'main' and 'xyz' clearinghouse sections
    are TWO VIEWS of ONE cross-margined wallet — accountValue is taken ONCE via max() across
    sections, NEVER summed (v2-quirk: summing double-counts the shared balance -> 2x sizing).
    READ-GUARDED inside _read. Includes the v2 read-sanity guard."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = _unwrap(ch)
    if not isinstance(data, dict):
        return 0.0, []
    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": scoring._f(pos.get("marginUsed", 0)),
            })
    # v2 read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but an EMPTY
    # positions list is a corrupt read — sizing or held-dedup off that re-enters held names.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[caribou.scan] read-sanity guard: margin in use but empty positions — skip tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── live instrument board (verbatim v2 get_universe_meta + ret_24h + day_vol folded) ──

def _get_universe_meta(ctx):
    """name -> {max_leverage, ret24h, vol}. Includes xyz instruments (Caribou trades every
    class). Skips delisted. Verbatim v2 get_universe_meta()/ret_24h()/day_vol()."""
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
        canonical.append(name)
    return out, canonical


def _fetch_candles(ctx, asset):
    """4h + 1d candles for ONE asset, dex-routed for xyz. Guarded — a bad name returns
    ([], []) and the universe loop skips it (asia-ai per-asset read-guard)."""
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["4h", "1d"],
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    })
    if not resp:
        return [], [], None
    d = _unwrap(resp)
    if isinstance(d, dict) and d.get("success") is False:
        return [], [], None
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    asset_ctx = (d.get("asset_context", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("4h", []) or [], candles.get("1d", []) or [], asset_ctx


def _own_24h_from_ctx(asset_ctx):
    """24h return from the per-asset context (verbatim v2 ret_24h on md ctx)."""
    if not isinstance(asset_ctx, dict):
        return None
    try:
        mark = float(asset_ctx.get("markPx", 0) or 0)
        prev = float(asset_ctx.get("prevDayPx", 0) or 0)
    except (TypeError, ValueError):
        return None
    if prev <= 0 or mark <= 0:
        return None
    return (mark - prev) / prev * 100.0


# ── class pools — verbatim v2 build_class_pools (rank from CONTEXT, no candle fetch) ──

def _build_class_pools(inputs, meta_map, canonical, leg):
    """Bucket the universe by asset class; within each class apply a top-N-by-vol liquidity cap
    + a relative-to-median gate; then rank by 24h momentum in the sleeve's direction and keep
    the top `rankPerClass` movers. Returns {class: [(name, meta), ...]}. Ranking is from
    context only — NO candle fetch here. Verbatim v2 build_class_pools()."""
    per_class_max = int(inputs.get("perClassMaxNames", scoring.DEFAULTS["perClassMaxNames"]))
    rank_per = int(inputs.get("rankPerClass", scoring.DEFAULTS["rankPerClass"]))
    vfloor = float(inputs.get("volFloorPctOfMedian", scoring.DEFAULTS["volFloorPctOfMedian"]))

    buckets = {}
    seen = set()
    for name in canonical:
        if not isinstance(name, str):
            continue
        key = name.upper()
        if key in seen:
            continue
        meta = meta_map.get(name) or meta_map.get(key)
        if not meta:
            continue
        v = meta.get("vol", 0.0)
        if v <= 0:
            continue
        seen.add(key)
        buckets.setdefault(scoring.classify(name, inputs), []).append((name, meta, v))

    pools = {}
    for cls, names in buckets.items():
        names.sort(key=lambda x: x[2], reverse=True)
        names = names[:per_class_max]
        if not names:
            continue
        vols = sorted(v for _, _, v in names)
        median = vols[len(vols) // 2]
        floor = vfloor * median
        liquid = [(n, m) for n, m, v in names if v >= floor]
        scored = []
        for n, m in liquid:
            r = m.get("ret24h")
            if r is None:
                continue
            scored.append((n, m, r))
        scored.sort(key=lambda x: x[2], reverse=(leg == "long"))
        # long: most positive movers; short: most negative movers
        finalists = [(n, m) for n, m, r in scored
                     if (r > 0 if leg == "long" else r < 0)][:rank_per]
        pools[cls] = finalists
    return pools


def scan(inputs, ctx):
    run_start = time.time()
    leg = (inputs.get("leg", "long") or "long").strip().lower()
    direction = "LONG" if leg == "long" else "SHORT"
    min_score = int(inputs.get("minScore", scoring.DEFAULTS["minScore"]))
    max_lev = int(inputs.get("maxLeverage", scoring.DEFAULTS["maxLeverage"]))
    max_slots = int(inputs.get("maxSlots", scoring.DEFAULTS["maxSlots"]))
    class_cap_pct = float(inputs.get("classMarginCapPct", scoring.DEFAULTS["classMarginCapPct"]))
    class_cap_pct = class_cap_pct / 100.0 if class_cap_pct > 1.0 else class_cap_pct  # frac guard
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("signaled") or {}).items() if (now - v) < ttl * 4}

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"signaled": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[caribou.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    account_value, positions = _get_positions(ctx)
    if account_value <= 0:
        _persist({"ts": now, "leg": leg, "emitted": False, "gate": "no_account_value"})
        print(f"[caribou.scan] leg={leg} WAITING — no account value / corrupt read",
              file=sys.stderr)
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    meta_map, canonical = _get_universe_meta(ctx)
    if not canonical:
        _persist({"ts": now, "leg": leg, "emitted": False, "gate": "no_universe"})
        print(f"[caribou.scan] leg={leg} WAITING — instrument board empty/failed",
              file=sys.stderr)
        return []

    pools = _build_class_pools(inputs, meta_map, canonical, leg)

    # ── Confirm + score the trend on each class's finalists (candle fetch happens here) ──
    candidates = []
    scanned = 0
    for cls, finalists in pools.items():
        for name, meta in finalists:
            if name.upper() in held_set:
                continue
            if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
                continue
            scanned += 1
            c4, cd, asset_ctx = _fetch_candles(ctx, name)
            if len(c4) < 6:
                continue
            own = _own_24h_from_ctx(asset_ctx)
            if own is None:
                own = meta.get("ret24h")     # fall back to board context
            th = scoring.score_trend(name, c4, cd, own, leg, inputs)
            if th and th["score"] >= min_score:
                th["_meta"] = meta
                th["assetClass"] = cls
                candidates.append(th)

    if not candidates:
        _persist({"ts": now, "leg": leg, "emitted": False, "gate": "no_candidate",
                  "scanned": scanned, "classes": {c: len(f) for c, f in pools.items()},
                  "min_score": min_score, "held": held})
        print(f"[caribou.scan] leg={leg} WAITING — no asset cleared min score {min_score} "
              f"(scanned {scanned}, classes {{{', '.join(f'{c}:{len(f)}' for c, f in pools.items())}}})",
              file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # ── Per-class margin cap (in PERCENT-of-equity space). Current per-class deployed margin %
    #    is read from open positions; a candidate whose class would exceed classMarginCapPct is
    #    skipped — the book stays diversified across classes (the CTA edge). Verbatim v2 intent. ──
    class_deployed_pct = {}
    for p in positions:
        cls = scoring.classify(p.get("coin", ""), inputs)
        pct = (scoring._f(p.get("margin", 0)) / account_value * 100.0) if account_value > 0 else 0.0
        class_deployed_pct[cls] = class_deployed_pct.get(cls, 0.0) + pct
    class_cap = class_cap_pct * 100.0   # PERCENT

    out = []
    emitted = []
    skipped_cap = []
    for th in candidates:
        if len(out) >= max_slots:
            break
        cls = th["assetClass"]
        margin_pct = scoring.vol_parity_margin_pct(th["vol_pct"], inputs)
        if margin_pct <= 0:
            continue
        # per-class diversification cap
        if class_deployed_pct.get(cls, 0.0) + margin_pct > class_cap:
            skipped_cap.append(f"{th['coin']}({cls})")
            continue
        desired_lev = scoring.conviction_leverage(th["score"], inputs)
        desired_lev = min(desired_lev, max_lev)               # strict sleeve cap
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(desired_lev, venue_max)
        if leverage <= 0:
            continue
        out.append({
            "asset": th["coin"],
            "direction": direction,
            "marginPct": margin_pct,                          # PERCENT (0,100] — runtime sizes the $
            "leverage": leverage,                             # venue-clamped, <= sleeve max
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": direction,
                "reasons": th["reasons"][:6],
                "assetClass": cls,
                "trend4h": th.get("trend4h"),
                "trend1d": th.get("trend1d"),
                "volPct": th.get("vol_pct"),
                "own24h": th.get("own24h"),
                "rsi": th.get("rsi"),
                "heldAssets": held,
            },
        })
        class_deployed_pct[cls] = class_deployed_pct.get(cls, 0.0) + margin_pct
        recent[th["coin"].upper()] = now
        emitted.append({"coin": th["coin"], "class": cls, "score": th["score"],
                        "leverage": leverage, "marginPct": margin_pct})

    result = {"ts": now, "leg": leg, "emitted": bool(out), "gate": "pass" if out else "all_capped",
              "scanned": scanned, "candidates": len(candidates), "signals": len(out),
              "classes": {c: len(f) for c, f in pools.items()},
              "class_deployed_pct": {k: round(v, 2) for k, v in class_deployed_pct.items()},
              "skipped_class_cap": skipped_cap, "held": held,
              "account_value": round(account_value, 2),
              "elapsed_sec": round(time.time() - run_start, 2), "details": emitted}
    _persist(result)
    print(f"[caribou.scan] leg={leg} {'EMIT' if out else 'WAITING'} scanned={scanned} "
          f"candidates={len(candidates)} signals={len(out)} "
          f"classes={{{', '.join(f'{c}:{len(f)}' for c, f in pools.items())}}} "
          f"skipped_cap={skipped_cap} elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
