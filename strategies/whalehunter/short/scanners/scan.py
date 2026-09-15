"""WHALEHUNTER — supervised scanner (Runtime 3.0), on Phalanx's smart-money engine.

Direction-parametrized and shared verbatim by both sleeves: the `long` instance passes
direction=LONG and opens WITH the proven cohort's freshly-forming longs; the `short` instance
passes direction=SHORT. Each sleeve runs on its OWN wallet and its OWN ctx.state, so the cohort
cache, the headcount baseline and the hit-rate ledger are per-sleeve. scanners/scoring.py is
phalanx's, byte-identical (tests/test_engine_verbatim.py pins it); this file is the orchestration
around it plus Whalehunter's own sizing. NEVER closes — the DSL owns every exit.

Each tick:
  1) COHORTS — the SMART cohort (lifetime realized >= smartMinRealizedUsd) and the CROWD
     (crowdMin..crowdMax) from the ALL_TIME realized ranking, cached daily in ctx.state and
     built a few pages per tick (_build_cohorts, unchanged).
  2) HEADCOUNT — one vote per (wallet, asset, direction) from discovery_get_trader_state
     (batched) -> {ASSET: {long_n, short_n}}. The FIRST read only seeds the baseline.
  3) LEDGER — a signal emitted >= 1h ago is marked right or wrong against price; an asset
     class (crypto / xyz) under a 40% hit rate has its threshold raised by 4, over 55%
     lowered by 2 (floor 52).
  4) GATE — the dominant side of a name qualifies iff one_sidedness >= tiltThreshold (65%,
     0 below 10 positioned wallets) AND its net headcount grew by >= deltaMin (+2) since
     the last tick (fresh_picks — pure); this sleeve keeps its own direction only.
  5) EMIT — not held, not signalled within recentSignalTtlSeconds, sized by Whalehunter's
     conviction score: 4 for a gated name, +1 at >= 85% one-sided, +1 when the crowd's
     headcount leans the other way (scoring.is_divergent — the crowd is read only when a
     name cleared the gate) -> marginPct x1 / x1.25 / x1.5.

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon.
"""

import sys
import time

import scoring

CACHE_VERSION = 2     # bump if the cohort-BUILDING logic changes (busts a stale cache) — 2: chunked build
_DEFAULT_TTL = 3600   # 60m signal-dedup: don't re-fire a coin while a signal is in flight
_DEFAULT_TILT_THRESHOLD = 65.0   # min one_sidedness to qualify (scoring: 0 below 10 wallets)
_DEFAULT_DELTA_MIN = 2.0          # min net-headcount growth since last tick (one wallet is noise)
_STRONG_TILT = 85.0   # the old "|bias| >= 0.7" size step, in headcount: 85% of the positioned wallets on one side


def _read(ctx, name, args, label):
    """Guarded MCP read — a transient error degrades, never crashes the tick."""
    try:
        raw = ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[whalehunter.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


# ── PROVEN COHORT — record readers verbatim from phalanx/main/scanners/scan.py ──

def _traders_of(d):
    """Unwrap discovery_get_top_traders response."""
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("traders", "data", "results"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def _trader_address(t):
    """Extract a lower-cased address from a trader record."""
    if not isinstance(t, dict):
        return ""
    for k in ("traderAddress", "trader_address", "address", "wallet"):
        v = t.get(k)
        if v:
            return str(v).lower()
    return ""


def _realized(t):
    """Realized PnL from a top-trader record (tries every spelling)."""
    for k in ("realizedProfitAndLoss", "realized_profit_and_loss",
              "profit_and_loss_realized", "realizedPnl", "realized_pnl"):
        v = t.get(k) if isinstance(t, dict) else None
        if v is not None:
            return scoring._f(v)
    return 0.0


def _build_cohorts(ctx, cached, inputs, now):
    """Smart cohort (lifetime realized >= smartMinRealizedUsd) + crowd cohort
    (crowdMin..crowdMax) from the ALL_TIME realized-PnL ranking, PAGED by offset to
    reach the deep crowd band, each capped. Cached daily in ctx.state.

    The build is CHUNKED across ticks: at most `cohortPagesPerTick` pages are read per
    tick and the progress (pages done, members so far) is carried in the cache under
    `build`, so a refresh that cannot fit one tick's timeout completes over several ticks
    instead of timing out on every tick forever — and a tick killed mid-build loses only
    that tick's pages. While a build is in progress the LAST COMPLETE cohort is served."""
    refresh_h = float(inputs.get("cohortRefreshHours", 24))
    pages_per_tick = max(1, int(inputs.get("cohortPagesPerTick", 2)))
    fresh = bool(cached.get("smart")) and cached.get("cache_version") == CACHE_VERSION \
        and (now - cached.get("refreshed_at", 0)) / 3600 < refresh_h
    build = cached.get("build") if isinstance(cached.get("build"), dict) else None
    if build and (now - float(build.get("started_at", now))) / 3600 >= refresh_h:
        build = None                                        # a build older than a refresh: start over
    if fresh and not build:
        return cached                                       # fresh cache — no fetch
    smin = float(inputs.get("smartMinRealizedUsd", 1_000_000))
    cmin = float(inputs.get("crowdMinRealizedUsd", 10_000))
    cmax = float(inputs.get("crowdMaxRealizedUsd", 100_000))
    cap = int(inputs.get("cohortSampleCap", 250))
    page_size = int(inputs.get("cohortFetchLimit", 1000))
    max_pages = int(inputs.get("cohortMaxPages", 6))
    if build is None:
        build = {"started_at": now, "next_page": 0, "smart": [], "crowd": [], "seen": []}
    smart, crowd, seen = list(build["smart"]), list(build["crowd"]), set(build["seen"])
    page = int(build["next_page"])
    done, fetched = page >= max_pages, 0
    while page < max_pages and fetched < pages_per_tick:
        resp = _read(ctx, "discovery_get_top_traders", {
            "time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
            "open_position_filter": False, "limit": page_size, "offset": page * page_size},
            f"discovery_get_top_traders(p{page})")
        fetched += 1
        if resp is None:
            break                                           # transient: keep progress, resume next tick
        raw = _traders_of(resp)
        if not raw:
            done = True
            break
        page_top = None
        for t in raw:
            if not isinstance(t, dict):
                continue
            addr = _trader_address(t)
            if not addr or addr in seen:
                continue
            rp = _realized(t)
            page_top = rp if page_top is None else max(page_top, rp)
            if rp >= smin:
                if len(smart) < cap:
                    smart.append(addr)
                    seen.add(addr)
            elif cmin <= rp <= cmax:
                if len(crowd) < cap:
                    crowd.append(addr)
                    seen.add(addr)
        page += 1
        if len(smart) >= cap and len(crowd) >= cap:
            done = True
            break
        if page_top is not None and page_top < cmin:        # whole page below the crowd floor
            done = True
            break
    if page >= max_pages:
        done = True
    if done:
        if not smart and not crowd:                         # failed refresh — keep the old cache
            return {k: v for k, v in cached.items() if k != "build"}
        return {"refreshed_at": now, "cache_version": CACHE_VERSION, "smart": smart, "crowd": crowd}
    out = {k: v for k, v in cached.items() if k != "build"}
    out["build"] = {"started_at": build["started_at"], "next_page": page,
                    "smart": smart, "crowd": crowd, "seen": sorted(seen)}
    if fetched:
        print(f"[whalehunter.scan] cohort build in progress: page {page}/{max_pages}, "
              f"smart={len(smart)} crowd={len(crowd)} — serving the last complete cohort", file=sys.stderr)
    return out


# ── COHORT POSITIONING (headcount per asset) — verbatim from phalanx/main/scanners/scan.py ──

def _state_wallet(st):
    return str((st or {}).get("traderAddress") or (st or {}).get("trader_address")
               or (st or {}).get("address") or (st or {}).get("wallet") or "").lower()


def _positions_of(st):
    if not isinstance(st, dict):
        return []
    pos = st.get("openPositions") or st.get("open_positions") or st.get("positions") or []
    return pos if isinstance(pos, list) else []


def _direction_of(pos):
    """LONG / SHORT from szi sign."""
    szi = scoring._f((pos or {}).get("szi"))
    if szi > 0:
        return "LONG"
    if szi < 0:
        return "SHORT"
    return None


def _cohort_headcount(ctx, cohort, inputs):
    """Aggregate per-asset headcount from the cohort's open positions.

    Returns {BARE_UPPER: {"long_n": int, "short_n": int, "raw_coin": str}}
    where raw_coin carries the venue prefix (xyz:NVDA) for the first sighting.
    """
    batch = int(scoring._f(inputs.get("stateBatch"), 50))
    states, any_ok = [], False
    for i in range(0, len(cohort), batch):
        d = _read(ctx, "discovery_get_trader_state",
                  {"trader_addresses": cohort[i:i + batch]},
                  f"discovery_get_trader_state(b{i // batch})")
        if d is None:
            continue
        any_ok = True
        # d might be a list of trader states or a dict wrapping one
        if isinstance(d, list):
            states.extend(d)
        elif isinstance(d, dict):
            for k in ("traders", "data", "results"):
                if isinstance(d.get(k), list):
                    states.extend(d[k])
                    break
            else:
                states.append(d)  # single trader state

    if not any_ok:
        print("[whalehunter.scan] all trader_state batches failed — no headcount this tick",
              file=sys.stderr)
        return {}

    counts = {}
    seen = set()  # (wallet, asset, direction) dedup across batches
    for st in states:
        if not isinstance(st, dict):
            continue
        wallet = _state_wallet(st)
        for pos in _positions_of(st):
            if not isinstance(pos, dict):
                continue
            coin = (pos.get("coin") or pos.get("asset") or "")
            if not coin:
                continue
            direction = _direction_of(pos)
            if direction is None:
                continue
            asset_key = scoring.bare_upper(coin)
            wkey = wallet or f"_anon_{id(pos)}"
            dkey = (wkey, asset_key, direction)
            if dkey in seen:
                continue
            seen.add(dkey)
            rec = counts.setdefault(asset_key, {
                "long_n": 0, "short_n": 0, "raw_coin": coin,
            })
            if direction == "LONG":
                rec["long_n"] += 1
            else:
                rec["short_n"] += 1
            # prefer a prefixed raw_coin for venue correctness
            if str(coin).lower().startswith("xyz:") and not str(rec["raw_coin"]).lower().startswith("xyz:"):
                rec["raw_coin"] = coin

    return counts


# ── PRICE READ — the ledger's entry / evaluation price, verbatim from phalanx ──

def _dex_for(asset):
    """XYZ (HIP-3) assets need dex='xyz' for market reads."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _asset_data(ctx, coin):
    """Read 1h + 4h candles for an asset. Returns (candles_1h, candles_4h) or (None, None)."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["1h", "4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": _dex_for(coin),
    }, f"market_get_asset_data({coin})")
    if not isinstance(md, dict):
        return None, None
    candles = md.get("candles", {}) or {}
    c1h = candles.get("1h", []) or []
    c4h = candles.get("4h", []) or []
    return c1h, c4h


def _current_price(candles_1h):
    """Latest close from 1h candles, or 0.0."""
    if not candles_1h:
        return 0.0
    last = candles_1h[-1]
    if isinstance(last, dict):
        return scoring._f(last.get("c", last.get("close", 0)))
    if isinstance(last, (list, tuple)) and len(last) >= 5:
        return scoring._f(last[4])
    return 0.0


# ── THIS WALLET ──

def _held(ctx):
    """Bare-uppercase set of coins with an open position on this wallet, or None on
    read failure. Ported verbatim from raven (clearinghouse assetPositions unwrap)."""
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return None
    out = set()
    # dual-DEX: strategy_get_clearinghouse_state returns {"main": ..., "xyz": ...} —
    # two views of ONE cross-margined wallet, each with its own assetPositions.
    # Reading assetPositions off the TOP level silently yields NOTHING held, so a
    # scanner re-opens names it already holds (pyramiding / failed duplicate opens).
    _rows = []
    for _sec in ("main", "xyz"):
        _s = d.get(_sec)
        if isinstance(_s, dict):
            _rows.extend(_s.get("assetPositions", _s.get("asset_positions", [])) or [])
    if not _rows:  # legacy/flat shape
        _rows = d.get("assetPositions", d.get("asset_positions", [])) or []
    for e in _rows:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        if coin and scoring._f(pos.get("szi")) != 0:
            out.add(coin.split(":", 1)[-1].upper())
    return out


# ── THE GATE — on the engine's functions. PURE (dicts in, picks out) ──

def fresh_picks(headcount, prev_tilts, accuracy_state, inputs):
    """Per name, the dominant side qualifies iff

        scoring.one_sidedness(long_n, short_n) >= threshold     (0 below 10 wallets positioned)
        scoring.directional_delta(prev, cur, direction) >= deltaMin

    where threshold is tiltThreshold moved per asset class by the hit-rate ledger
    (scoring.adjusted_threshold). So a 30L/26S split (54%) never trades however fast it grew,
    and a +1 delta — one wallet — never trades however one-sided the name is. Returns
    [{asset, bare, direction, count, conviction, delta}] ranked by one-sidedness
    (scoring.rank_signals). PURE — no I/O."""
    tilt = scoring._f(inputs.get("tiltThreshold"), _DEFAULT_TILT_THRESHOLD)
    delta_min = scoring._f(inputs.get("deltaMin"), _DEFAULT_DELTA_MIN)
    picks = []
    for bare, rec in (headcount or {}).items():
        rec = rec or {}
        long_n = int(scoring._f(rec.get("long_n")))
        short_n = int(scoring._f(rec.get("short_n")))
        raw_coin = rec.get("raw_coin", bare)
        sided = scoring.one_sidedness(long_n, short_n)
        if sided < scoring.adjusted_threshold(tilt, scoring.asset_class_for(raw_coin), accuracy_state):
            continue
        direction = "LONG" if long_n >= short_n else "SHORT"
        p = (prev_tilts or {}).get(bare) or {}
        delta = scoring.directional_delta(p.get("long_n", 0), p.get("short_n", 0),
                                          long_n, short_n, direction)
        if delta < delta_min:
            continue
        picks.append({"asset": raw_coin, "bare": bare, "direction": direction,
                      "count": max(long_n, short_n), "conviction": round(sided, 1),
                      "delta": round(delta, 1)})
    return scoring.rank_signals(picks)


# ── SIZING — Whalehunter's own conviction score -> marginPct ──

def crowd_diverges(smart_rec, crowd_rec):
    """True when the crowd's headcount on this name leans against the smart cohort's
    (scoring.is_divergent over each cohort's long share, scoring.net_tilt). No crowd position
    on the name -> no divergence claim (an absent crowd would otherwise read as SHORT)."""
    if not crowd_rec:
        return False
    smart_lr, _ = scoring.net_tilt(smart_rec.get("long_n", 0), smart_rec.get("short_n", 0))
    crowd_lr, _ = scoring.net_tilt(crowd_rec.get("long_n", 0), crowd_rec.get("short_n", 0))
    return scoring.is_divergent(smart_lr, crowd_lr)


def score_for(one_sidedness, divergent):
    """Whalehunter's conviction score on the engine's reads: 4 for any name that cleared the gate
    (the old 3 + the 'adding' point, which the delta gate now makes mandatory), +1 when the smart
    side holds >= 85% of the positioned wallets, +1 when the crowd leans the other way."""
    return 4 + (1 if one_sidedness >= _STRONG_TILT else 0) + (1 if divergent else 0)


def margin_pct_for(score, config):
    """Conviction-scaled marginPct INTENT as a PERCENT of withdrawable in (0,100]
    (the runtime sizes (marginPct/100)*withdrawable). Scales +25% per point above 4 (the
    score every gated name starts at), capped at maxMarginPct. Whalehunter's own sizing,
    moved here from the old scoring.py."""
    base = float(config.get("marginPct", 12))
    cap = float(config.get("maxMarginPct", 25))
    smax = float(config.get("maxConvictionScale", 2.0))
    scale = min(smax, 1.0 + 0.25 * max(0, score - 4))
    return round(min(base * scale, cap), 4)


def scan(inputs, ctx):
    direction = (inputs.get("direction", "LONG") or "LONG").upper()
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    leverage = min(int(inputs.get("stdLeverage", 3)), int(inputs.get("maxLeverage", 5)))
    min_members = int(inputs.get("cohortMinMembers", 5))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    prev = dict(last.get("prev_tilts") or {})
    accuracy = last.get("accuracy")
    if not isinstance(accuracy, dict):
        accuracy = scoring.new_accuracy_state()
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    cohorts = _build_cohorts(ctx, last.get("cohorts", {}), inputs, now)
    smart, crowd = cohorts.get("smart", []), cohorts.get("crowd", [])

    def _persist(tilts, opened=0, held_n=None):
        if ctx.state is None:
            return
        try:
            ctx.state.append({
                "cohorts": cohorts, "prev_tilts": tilts, "accuracy": accuracy, "recent": recent,
                "result": {"ts": now, "opened": opened, "held": held_n,
                           "cohort_size": len(smart), "names": len(tilts or {})},
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[whalehunter.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if len(smart) < min_members:                            # cohort too small (or still building) — cache & bail
        _persist(prev)
        return []

    # ── headcount: one vote per (wallet, asset, direction) ──
    cur = _cohort_headcount(ctx, smart, inputs)
    if not cur:
        print("[whalehunter.scan] no headcount this tick — holding the prior baseline", file=sys.stderr)
        _persist(prev)
        return []
    tilts = {a: {"long_n": r["long_n"], "short_n": r["short_n"]} for a, r in cur.items()}

    # ── cold start: the first read is the baseline, not a signal (delta needs a last tick) ──
    if not prev:
        print(f"[whalehunter.scan] baseline seeded: {len(tilts)} names across {len(smart)} smart "
              f"wallets — no opens on the first tick", file=sys.stderr)
        _persist(tilts)
        return []

    # ── ledger: mark signals emitted >= 1h ago right or wrong against price ──
    def _price_lookup(asset):
        c1h, _ = _asset_data(ctx, asset)
        return _current_price(c1h)

    for cls in ("crypto", "xyz"):
        accuracy = scoring.update_accuracy(accuracy, cls, now, _price_lookup)

    fresh = [p for p in fresh_picks(cur, prev, accuracy, inputs) if p["direction"] == direction]

    held = _held(ctx)
    if held is None:
        # clearinghouse unreadable — do NOT advance the baseline (keep the growth actionable
        # next tick), emit nothing this tick.
        print("[whalehunter.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        _persist(prev)
        return []
    # the crowd only ever sizes a pick UP, so it is read only when a name cleared the gate
    crowd_hc = _cohort_headcount(ctx, crowd, inputs) if fresh and crowd else {}

    out = []
    for pick in fresh:
        bare, asset, count = pick["bare"], pick["asset"], int(pick["count"])
        if bare in held:
            continue
        if recent.get(bare) is not None and (now - recent[bare]) < ttl:   # signal-dedup
            continue
        divergent = crowd_diverges(cur[bare], crowd_hc.get(bare))
        score = score_for(pick["conviction"], divergent)
        mgn = margin_pct_for(score, inputs)
        recent[bare] = now
        reasons = [f"{count}_smart_wallets_{direction}", f"{pick['conviction']:.0f}%_one_sided",
                   f"delta_+{pick['delta']:.0f}"] + (["crowd_diverges"] if divergent else [])
        out.append({
            "asset": asset, "direction": direction, "marginPct": mgn, "leverage": leverage,
            "data": {"score": score, "direction": direction, "smartMembers": count,
                     "oneSidedness": pick["conviction"], "delta": pick["delta"],
                     "divergence": divergent, "reasons": reasons},
        })
        entry_px = _price_lookup(asset)          # the ledger marks this signal in 1h
        if entry_px > 0:
            scoring.add_pending_signal(accuracy, scoring.asset_class_for(asset),
                                       asset, direction, entry_px, now)
        print(f"[whalehunter.scan] OPEN {direction} {asset}: {count} smart wallets agree "
              f"({pick['conviction']:.0f}% one-sided, delta +{pick['delta']:.0f}"
              f"{', crowd diverges' if divergent else ''}) score {score} {leverage}x {mgn}%",
              file=sys.stderr)

    if not out:
        print(f"[whalehunter.scan] no opens: cohort={len(smart)} held={len(held)} "
              f"names={len(cur)} fresh={len(fresh)}", file=sys.stderr)

    _persist(tilts, len(out), len(held))
    return out
