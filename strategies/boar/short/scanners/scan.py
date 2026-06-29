"""BOAR — supervised scanner (shared verbatim by both books).

Direction-parametrized via inputs.leg: the `long` instance passes leg=long (the HARD-MONEY
book — LONG scarce real assets: GOLD, BTC, SILVER, PLATINUM, PALLADIUM); the `short`
instance passes leg=short (the PAPER book — SHORT the broad market + rate-sensitive growth:
SP500, RIVN, DKNG, HIMS). A faithful Runtime 3.0 port of the v2 boar-producer.py main() —
the thematic universe build + absolute-trend scoring + per-name conviction sizing are
preserved exactly. Read-only, single-pass.

Per tick:
  1. read the wallet clearinghouse (account value + held names + free margin)
  2. build the live THEMATIC universe: the curated whitelist (config.universe) intersected
     with the live instrument board + a relative-to-market liquidity floor — but the
     median-vol gate is SKIPPED for small curated lists (< minUniverseForMedianGate names),
     since on a tiny basket it drops an intentionally-thinner name and can collapse the
     universe to 1 (v2 Mongoose-bricking note). The curation IS the liquidity decision.
  3. rank the universe by 24h relative strength (own 24h return - the universe mean) — used
     only as a SCORE TIEBREAKER; ABSOLUTE trend is the hard gate inside scoring
  4. score each pooled name (absolute-trend gate, excess bonus), dedup held + recently-signalled
  5. emit a top-level marginPct INTENT (PERCENT = base_margin_pct * conviction weight; the
     runtime sizes the dollars) + a per-name venue-clamped leverage; the runtime owns slots,
     dedup, execution, DSL.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped so one bad/illiquid/fake name skips
without rolling back the whole universe tick (asia-ai NASDAQ-bug class). Per the scan
contract ANY uncaught exception rolls the whole tick back to [], so one bad read would
silently kill all emits — hence each read degrades to None/[] instead of propagating.

FIDELITY NOTES vs boar-producer.py v1.0.0
- DROPPED v2 order-lifecycle behaviour: none present in boar's producer (boar never called
  cancel_order / has_resting_orders / stale-order purge — it only pushed signals), so
  nothing to drop. The runtime's reconciliation owns order lifecycle.
- v2 marginPct is a FRACTION (0.18 long / 0.15 short); this port carries it as a PERCENT
  input (18 / 15). A defensive guard ×100's any value <= 1.0 (a pasted fraction).
- Per-name conviction weight is BAKED INTO the emitted marginPct (see scoring.sizing_weight)
  so the runtime reproduces v2's account_value * marginPct(fraction) * weight exactly.
- The free-margin affordability cap is per-name (weight-aware), mirroring v2's
  decrement-as-you-commit loop so a mixed-size basket never emits an un-fundable order.
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
        print(f"[boar.scan] {name} read failed: {exc!r}", file=sys.stderr)
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
    makes every size 2x). Boar holds both xyz equities/metals and main-DEX BTC on the same
    wallet, so both sections routinely carry positions. Free margin = equity - committed."""
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
    d = _unwrap(resp)
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _build_universe(whitelist, meta_map, vol_floor_pct, min_for_gate):
    """The curated thematic whitelist intersected with the live board + a relative liquidity
    floor (>= vol_floor_pct of the whitelist's median 24h vol; NO hardcoded $). v2-quirk:
    SMALL curated lists (< min_for_gate live names) SKIP the median gate — on a tiny basket
    it drops an intentionally-thinner name and can collapse the universe to 1, bricking the
    book (v2 Mongoose short, 2026-06-22). Below the threshold, keep every live (vol>0) name —
    the curation IS the liquidity decision. Verbatim v2 build_universe()."""
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
    if len(cand) < int(min_for_gate):                       # v2-quirk: skip gate on small lists
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
    weights = inputs.get("sizingWeights", {"_default": 1.0})
    min_score = int(inputs.get("minScore", 5))
    base_margin_pct = float(inputs.get("marginPct", 18))     # PERCENT of withdrawable (0,100]
    # defensive: a pasted v2 FRACTION (<= 1.0, e.g. 0.18) -> percent (dire/koala guard)
    if base_margin_pct <= 1.0:
        base_margin_pct *= 100.0
    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 4))
    rank_pool = int(inputs.get("rankPoolSize", 12))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", 0.2))
    min_for_gate = int(inputs.get("minUniverseForMedianGate", 5))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "ts": now})
        except Exception as exc:  # noqa: BLE001
            print(f"[boar.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        print(f"[boar.scan] leg={leg} WAITING — no account value / corrupt read",
              file=sys.stderr)
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        print(f"[boar.scan] leg={leg} WAITING — slots full ({len(held)}/{max_slots})",
              file=sys.stderr)
        return []

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map, vol_floor_pct, min_for_gate)

    # ── Cross-sectional relative strength over the thematic universe (used only as a score
    #    TIEBREAKER; absolute trend is the gate inside scoring.score_thematic) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = meta.get("ret24h") if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 1:                                          # v2-quirk: thematic universe too thin
        _persist()
        print(f"[boar.scan] leg={leg} WAITING — thematic universe too thin "
              f"(scanned={len(universe)})", file=sys.stderr)
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
        _persist()
        print(f"[boar.scan] leg={leg} WAITING — no name cleared min score {min_score} "
              f"(scanned={len(universe)} pool={len(pool)} mean_rs={mean_rs:.2f})",
              file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: emit best-scoring first, sizing each by its conviction weight, and cap to what
    # the wallet can actually FUND. free margin decrements as we commit so a mixed basket of
    # different-sized names never emits an un-fundable order (insufficient-funds re-emit spam).
    out = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], weights)
        margin_pct = base_margin_pct * weight                # conviction baked into the PERCENT
        margin_usd = (margin_pct / 100.0) * account_value
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)  # per-name venue clamp
        if margin_usd <= 0 or leverage <= 0:
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
                "weight": weight,
                "reasons": th["reasons"][:6],
                "trend4h": th.get("trend4h"),
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
            },
        })
        open_slots -= 1
        free_margin -= margin_usd * 1.1
        recent[th["coin"].upper()] = now

    _persist()
    print(f"[boar.scan] leg={leg} scanned={len(universe)} pool={len(pool)} "
          f"candidates={len(candidates)} emitted={len(out)} mean_rs={mean_rs:.2f} "
          f"elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
