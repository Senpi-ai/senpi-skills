"""BARNACLE — supervised scanner (autonomous "stealth accumulation / decoupler").

Index inclusions, M&A targets, and catalyst run-ups all leave the SAME fingerprint
in the tape WITHOUT knowing the catalyst: a name DECOUPLING from the broad market
on persistent, rising volume with shallow (bought) dips — relentless accumulation,
not a one-bar spike. Barnacle detects that footprint from market data ALONE — no
operator calendar, no event feed — and rides it. The DSL owns the exit (no event
date needed). LONG up-accumulation, SHORT down-distribution.

Per tick (read-only, single-pass):
  1. Account read (clearinghouse, dual-DEX equity via max(), read-sanity guard).
  2. Universe = the LIVE xyz board (market_list_instruments(dex="xyz")), filtered
     to liquid names (dayNtlVlm >= minVolUsd, not delisted) and excluding the
     benchmark itself.
  3. Benchmark: read the broad-xyz market (benchmarkAsset, default xyz:XYZ100)
     4h candles ONCE -> benchmark return over the excess lookback.
  4. For each non-held, non-recently-signaled candidate: pull 1h+4h candles and
     score the footprint via the pure scoring.build_thesis (excess vs benchmark,
     volume persistence, shallow-dip grind, 4h/1h trend, smart-money nudge).
  5. Emit the TOP 1-2 by score at/above minScore. Held + recent-signal dedup via
     ctx.state (TTL).

EVERY ctx.senpi_mcp.call_tool is READ-GUARDED — one bad/illiquid/fake name (or a
flaky benchmark/account/leaderboard read) skips without rolling back the whole
universe tick (per the scan contract, ANY uncaught exception rolls the entire tick
back to []). No daemon, no push_signal, no create_position — the runtime sizes the
dollars, owns cooldowns/slots/risk gates, and trails the DSL exit.

Sizing: emits a top-level `marginPct` INTENT (PERCENT in (0,100]) plus a per-name
venue-clamped `leverage`; the runtime sizes (marginPct/100) * withdrawable.
"""

import sys
import time

import scoring

# defaults (also declared in runtime.yaml inputs)
_DEFAULT_BENCHMARK = "xyz:XYZ100"   # broad-xyz market index (validated live in HL xyz meta)
_DEFAULT_FALLBACK_BENCHMARK = "xyz:SP500"  # if the primary benchmark read fails/empty
_DEFAULT_MIN_VOL_USD = 5_000_000    # 24h notional-volume liquidity floor
_DEFAULT_MIN_SCORE = 5
_DEFAULT_MARGIN_PCT = 15            # PERCENT of withdrawable (0,100]
_DEFAULT_LEVERAGE = 4              # clamped to [1,5] + venue max
_DEFAULT_MAX_EMIT = 2              # emit the top 1-2 by score
_DEFAULT_TTL = 240                # 4min race-window dedup
_DEFAULT_EXCESS_BARS = 24          # ~24 * 4h = 4 days for the excess/benchmark window


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must
    NOT roll back the whole tick. Returns None on failure so the caller's degrade
    path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[barnacle.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


# ── account / positions (dual-DEX, read-sanity guard — like bison) ─────────────

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.
    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Includes the read-sanity guard (margin in use
    but empty positions -> corrupt read -> skip tick)."""
    if not getattr(ctx, "wallet", None):
        return 0.0, []
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = _unwrap(ch)
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "direction": "LONG" if szi > 0 else "SHORT"})

    # read-sanity guard (funding/$0 glitch 2026-06): a corrupt clearinghouse read
    # can report margin/notional IN USE while returning an EMPTY positions list;
    # sizing or the held-dedup off that re-enters held names (pyramiding) and
    # mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[barnacle.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── live xyz instrument board: the universe ────────────────────────────────────

def _get_universe(ctx, inputs, benchmark_asset):
    """The live liquid xyz board to scan this tick. READ-GUARDED.
    A name qualifies if it is on the xyz DEX, NOT delisted, has 24h notional
    volume >= minVolUsd, and is NOT the benchmark itself. Returns a list of
    {coin, vol, venue_max}."""
    min_vol = float(inputs.get("minVolUsd", _DEFAULT_MIN_VOL_USD))
    dex = inputs.get("dex", "xyz")
    resp = _read(ctx, "market_list_instruments", {"dex": dex})
    if not resp:
        return []
    data = _unwrap(resp)
    insts = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(insts, list):
        return []

    bench_u = str(benchmark_asset).upper()
    out, seen = [], set()
    for inst in insts:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        nu = str(name).upper()
        if nu == bench_u or nu in seen:
            continue
        ctxd = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        vol = scoring._f(ctxd.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        if vol < min_vol:
            continue
        seen.add(nu)
        out.append({
            "coin": name,
            "vol": vol,
            "venue_max": inst.get("max_leverage", inst.get("maxLeverage")),
        })
    out.sort(key=lambda x: -x["vol"])
    return out


def _benchmark_return(ctx, inputs, asset, excess_bars):
    """Benchmark return over `excess_bars` of 4h candles, or None. READ-GUARDED."""
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": _dex_for(asset, inputs),
    })
    if not resp:
        return None
    if isinstance(resp, dict) and resp.get("success") is False:
        return None
    d = _unwrap(resp)
    if not isinstance(d, dict):
        return None
    candles = (d.get("candles", {}) or {}).get("4h", []) or []
    return scoring.window_return(candles, excess_bars)


def _fetch_candles(ctx, asset, inputs):
    """1h + 4h candles for ONE asset, or ([], []). READ-GUARDED."""
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": _dex_for(asset, inputs),
    })
    if not resp:
        return [], []
    if isinstance(resp, dict) and resp.get("success") is False:
        return [], []
    d = _unwrap(resp)
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _get_sm_direction(ctx, coin):
    """Net smart-money lean for `coin` from leaderboard_get_markets. Returns
    (direction, tilt_pct) or (None, 0.0). READ-GUARDED -> a read error degrades to
    (None, 0.0), which the scorer treats as a NEUTRAL nudge (no gate). Token match
    is case-insensitive. long_ratio >= 55 -> LONG; <= 45 -> SHORT; else NEUTRAL."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw or (isinstance(raw, dict) and raw.get("success") is False):
        return None, 0.0
    markets = _unwrap(raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

    # xyz instruments carry the 'xyz:' prefix; the leaderboard tokens may be bare
    # (e.g. NVDA). Match against both the full name and the bare suffix.
    target = coin.upper()
    bare = target.split(":", 1)[1] if ":" in target else target
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token not in (target, bare):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100.0
    if long_ratio >= 55:
        return "LONG", long_ratio
    if long_ratio <= 45:
        return "SHORT", 100 - long_ratio
    return "NEUTRAL", 50.0


# ── ctx.state: recent-signal dedup ─────────────────────────────────────────────

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    benchmark = str(inputs.get("benchmarkAsset", _DEFAULT_BENCHMARK))
    fallback_benchmark = str(inputs.get("fallbackBenchmarkAsset", _DEFAULT_FALLBACK_BENCHMARK))
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    excess_bars = int(inputs.get("excessBars", _DEFAULT_EXCESS_BARS))
    lev_cfg = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    max_emit = int(inputs.get("maxEmit", _DEFAULT_MAX_EMIT))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    universe_max_names = int(inputs.get("universeMaxNames", 50))

    # marginPct: PERCENT in (0,100]. Defensive fraction guard (dire/koala pattern):
    # a value <= 1.0 is a pasted FRACTION (e.g. 0.15) -> *100 -> 15.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[barnacle.scan] marginPct={margin_pct} looks like a fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    def _persist(result=None, signaled=None):
        if ctx.state is None:
            return
        rec = {}
        if signaled is not None:
            rec["signaled"] = signaled
        if result is not None:
            rec["result"] = result
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[barnacle.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    # 1) account
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[barnacle.scan] WAITING — no account value / corrupt read", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_account_value"})
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # 2) universe (live xyz board, liquid, benchmark excluded)
    universe = _get_universe(ctx, inputs, benchmark)
    if not universe:
        print("[barnacle.scan] market_list_instruments(xyz) empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe"}, signaled)
        return []
    universe = universe[:universe_max_names]

    # 3) benchmark return ONCE (broad-xyz decoupling reference), with fallback
    benchmark_ret = _benchmark_return(ctx, inputs, benchmark, excess_bars)
    used_benchmark = benchmark
    if benchmark_ret is None and fallback_benchmark and fallback_benchmark != benchmark:
        print(f"[barnacle.scan] benchmark {benchmark} read failed/short — "
              f"falling back to {fallback_benchmark}", file=sys.stderr)
        benchmark_ret = _benchmark_return(ctx, inputs, fallback_benchmark, excess_bars)
        used_benchmark = fallback_benchmark
    if benchmark_ret is None:
        print("[barnacle.scan] WAITING — no benchmark return (broad-xyz read failed)", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_benchmark"}, signaled)
        return []

    # 4) score every eligible candidate (held + recently-signaled filtered BEFORE
    #    the per-asset MCP fetch)
    candidates = []
    scanned = 0
    for u in universe:
        coin = u["coin"]
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        c1, c4 = _fetch_candles(ctx, coin, inputs)
        if len(c4) < excess_bars + 1 or len(c1) < 6:
            continue
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(coin, c1, c4, benchmark_ret, sm, inputs)
        if th and th["score"] >= min_score:
            th["_venue_max"] = u.get("venue_max")
            candidates.append(th)

    if not candidates:
        print(f"[barnacle.scan] WAITING — no decoupler footprint >= minScore {min_score:.0f} "
              f"(scanned {scanned}/{len(universe)}, bench {used_benchmark} {benchmark_ret:+.1f}%) "
              f"held={held}", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidate", "scanned": scanned,
                  "benchmark": used_benchmark, "benchmark_ret": round(benchmark_ret, 3),
                  "held": held}, signaled)
        return []

    # 5) emit the top 1-2 by score
    candidates.sort(key=lambda x: x["score"], reverse=True)
    to_emit = candidates[:max(1, max_emit)]

    out, emitted_coins = [], []
    for th in to_emit:
        leverage = scoring.clamp_leverage(lev_cfg, th.get("_venue_max"), lo=1, hi=5)
        if leverage <= 0:
            continue
        signaled[th["coin"].upper()] = now
        emitted_coins.append(th["coin"])
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,           # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,              # venue-clamped to [1,5] + instrument max
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": th["direction"],
                "reasons": th["reasons"][:8],
                "excess": th["excess"],
                "nameRet": th["name_ret"],
                "benchmarkRet": th["benchmark_ret"],
                "benchmark": used_benchmark,
                "trend4h": th["trend_4h"],
                "trend4hStrength": th["trend_4h_strength"],
                "trend1h": th["trend_1h"],
                "volTrend4hPct": th["vol_trend"],
                "deepestDipPct": th["deepest_dip"],
                "smDirection": th["sm_direction"] or "NEUTRAL",
                "smPct": th["sm_pct"],
                "heldAssets": held,
            },
        })

    result = {"ts": now, "emitted": len(out), "gate": "emit", "scanned": scanned,
              "benchmark": used_benchmark, "benchmark_ret": round(benchmark_ret, 3),
              "candidates": len(candidates), "coins": emitted_coins,
              "marginPct": round(margin_pct, 4), "held": held}
    print(f"[barnacle.scan] EMIT {emitted_coins} (scanned {scanned}, "
          f"bench {used_benchmark} {benchmark_ret:+.1f}%, marginPct {margin_pct:.1f}%) | "
          f"top {to_emit[0]['coin']} {to_emit[0]['direction']} score={to_emit[0]['score']} "
          f"{to_emit[0]['reasons'][:5]}", file=sys.stderr)
    _persist(result, signaled)
    return out
