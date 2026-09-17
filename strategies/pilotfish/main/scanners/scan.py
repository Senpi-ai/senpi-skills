"""PILOTFISH — Smart-Money Accumulation. The single supervised scanner.

Every other copy strategy reads a STANDING position — who is long what, right now, which
is already priced. PILOTFISH reads the DERIVATIVE: it follows the PROVEN COHORT (>= $1M
lifetime realized PnL, discovery_get_top_traders ALL_TIME) and opens WITH it only when its
headcount on a name is one-sided AND still growing — Phalanx's smart-money engine.
scanners/scoring.py is phalanx's, byte-identical (tests/test_engine_verbatim.py pins it);
this file is the orchestration around it plus Pilotfish's own sizing. NEVER closes — the
DSL owns every exit.

Each tick:
  1) SLOW CLOCK — refresh the cohort every cohortRefreshHours (or first tick): paginated
     discovery_get_top_traders (ALL_TIME, realized), wallets with realized PnL >=
     minRealizedUsd, capped at cohortCap. If derivation returns empty, KEEP the prior
     cohort (never wipe).
  2) HEADCOUNT — one vote per (wallet, asset, direction) from discovery_get_trader_state
     (batched) -> {ASSET: {long_n, short_n}}. The FIRST read only seeds the baseline.
  3) LEDGER — a signal emitted >= 1h ago is marked right or wrong against price; an asset
     class (crypto / xyz) under a 40% hit rate has its threshold raised by 4, over 55%
     lowered by 2 (floor 52).
  4) GATE — the dominant side of a name qualifies iff one_sidedness >= tiltThreshold (65%,
     0 below 10 positioned wallets) AND its net headcount grew by >= deltaMin (+2) since
     the last tick (fresh_picks — pure). The minority side is unpickable by construction.
  5) EMIT — up to the free slots, not held, not signalled within recentSignalTtlSeconds,
     sized by how many wallets are on the dominant side (base / good / apex).

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring

_DEFAULT_TILT_THRESHOLD = 65.0   # min one_sidedness to qualify (scoring: 0 below 10 wallets)
_DEFAULT_DELTA_MIN = 2.0          # min net-headcount growth since last tick (one wallet is noise)


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[pilotfish.scan] {label} read failed: {exc!r}", file=sys.stderr)
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


def _refresh_cohort(ctx, inputs):
    """The proven cohort: top traders by ALL_TIME realized PnL — the row's
    `realizedProfitAndLoss`, the one spelling the tool returns. Refreshed on a slow clock."""
    addrs, offset = [], 0
    page = int(scoring._f(inputs.get("pageSize"), 500))
    cap = int(scoring._f(inputs.get("cohortCap"), 80))
    min_real = scoring._f(inputs.get("minRealizedUsd"), 1_000_000)
    for _ in range(int(scoring._f(inputs.get("maxPages"), 4))):
        data = _read(ctx, "discovery_get_top_traders",
                     {"time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
                      "limit": page, "offset": offset},
                     "discovery_get_top_traders")
        rows = _traders_of(data)
        if not rows:
            break
        for t in rows:
            if not isinstance(t, dict):
                continue
            realized = scoring._f(t.get("realizedProfitAndLoss"), 0.0)
            addr = _trader_address(t)
            if addr and realized >= min_real:
                addrs.append(addr)
        if len(addrs) >= cap:
            break
        offset += page
    seen, out = set(), []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:cap]


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
    batch = int(scoring._f(inputs.get("stateBatch"), 40))
    states, failed = [], []
    for i in range(0, len(cohort), batch):
        d = _read(ctx, "discovery_get_trader_state",
                  {"trader_addresses": cohort[i:i + batch]},
                  f"discovery_get_trader_state(b{i // batch})")
        if d is None:
            failed.append(i // batch)
            continue
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

    if failed:
        # A partial read is not a reading: the failed batch's wallets would count as the cohort leaving
        # their positions, and saved as the baseline they would count as growth on the next full read.
        print(f"[pilotfish.scan] trader_state batch(es) {failed} failed — no headcount this tick",
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
    data = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet},
                 "clearinghouse")
    if data is None:
        return None
    held = set()
    views = list(data.values()) + [data] if isinstance(data, dict) else []
    for view in views:
        if not isinstance(view, dict):
            continue
        for ap in (view.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            coin = pos.get("coin") or pos.get("asset")
            if coin and scoring._f(pos.get("szi"), 0.0) != 0.0:
                held.add(scoring.bare_upper(coin))
    return held


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


# ── SIZING — conviction band from the dominant-side headcount -> leverage / marginPct ──

def band_for(count, inputs):
    """Conviction band from how many cohort wallets agree."""
    if count >= scoring._f(inputs.get("apexConsensus"), 6):
        return "apex"
    if count >= scoring._f(inputs.get("goodConsensus"), 4):
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100] — never a fraction."""
    lev = scoring._f((inputs.get("leverageTiers") or {}).get(band), 3)
    mgn = scoring._f((inputs.get("marginPctTiers") or {}).get(band), 8)
    lev = min(lev, scoring._f(inputs.get("maxLeverage"), 4))
    if venue_max:
        lev = min(lev, scoring._f(venue_max, lev))
    mgn = min(mgn, scoring._f(inputs.get("maxMarginPct"), 20))
    return int(max(1, lev)), round(max(0.1, mgn), 4)


def _persist(ctx, cohort, last_refresh, tilts, accuracy, recent, now, opened, held_n):
    if ctx.state is None:
        return
    try:
        ctx.state.append({
            "cohort": cohort, "last_refresh": last_refresh,
            "prev_tilts": tilts, "accuracy": accuracy, "recent": recent,
            "result": {"ts": now, "opened": opened, "held": held_n,
                       "cohort_size": len(cohort), "names": len(tilts or {})},
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[pilotfish.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)


def scan(inputs, ctx):
    now = time.time()
    refresh_s = scoring._f(inputs.get("cohortRefreshHours"), 12.0) * 3600.0
    max_slots = int(scoring._f(inputs.get("maxSlots"), 5))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 21600)

    st = (ctx.state.last() or {}) if ctx.state else {}
    cohort = list(st.get("cohort", []) or [])
    last_refresh = scoring._f(st.get("last_refresh"), 0.0)
    prev = dict(st.get("prev_tilts", {}) or {})
    accuracy = st.get("accuracy")
    if not isinstance(accuracy, dict):
        accuracy = scoring.new_accuracy_state()
    recent = dict(st.get("recent", {}) or {})

    # ── SLOW CLOCK: refresh the cohort only when due (or first tick / empty) ──
    if not cohort or (now - last_refresh) >= refresh_s:
        fresh_cohort = _refresh_cohort(ctx, inputs)
        if fresh_cohort:
            cohort, last_refresh = fresh_cohort, now
            print(f"[pilotfish.scan] cohort refreshed: {len(cohort)} proven wallets", file=sys.stderr)
        else:
            print(f"[pilotfish.scan] cohort derivation empty — keeping prior cohort "
                  f"({len(cohort)} wallets)", file=sys.stderr)

    if not cohort:
        print("[pilotfish.scan] no cohort — nothing to follow this tick", file=sys.stderr)
        _persist(ctx, cohort, last_refresh, prev, accuracy, recent, now, 0, 0)
        return []

    # ── headcount: one vote per (wallet, asset, direction) ──
    cur = _cohort_headcount(ctx, cohort, inputs)
    if not cur:
        print("[pilotfish.scan] no headcount this tick — holding the prior baseline", file=sys.stderr)
        _persist(ctx, cohort, last_refresh, prev, accuracy, recent, now, 0, None)
        return []
    tilts = {a: {"long_n": r["long_n"], "short_n": r["short_n"]} for a, r in cur.items()}

    # ── cold start: the first read is the baseline, not a signal (delta needs a last tick) ──
    if not prev:
        print(f"[pilotfish.scan] baseline seeded: {len(tilts)} names across {len(cohort)} wallets "
              f"— no opens on the first tick", file=sys.stderr)
        _persist(ctx, cohort, last_refresh, tilts, accuracy, recent, now, 0, None)
        return []

    # ── ledger: mark signals emitted >= 1h ago right or wrong against price ──
    def _price_lookup(asset):
        c1h, _ = _asset_data(ctx, asset)
        return _current_price(c1h)

    for cls in ("crypto", "xyz"):
        accuracy = scoring.update_accuracy(accuracy, cls, now, _price_lookup)

    fresh = fresh_picks(cur, prev, accuracy, inputs)

    held = _held(ctx)
    if held is None:
        # clearinghouse unreadable — do NOT advance the baseline (keep the growth actionable
        # next tick), emit nothing this tick.
        print("[pilotfish.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        _persist(ctx, cohort, last_refresh, prev, accuracy, recent, now, 0, None)
        return []
    free = max_slots - len(held)

    out = []
    for pick in fresh:
        if free <= 0:
            break
        bare, asset, direction = pick["bare"], pick["asset"], pick["direction"]
        count = int(pick["count"])
        if bare in held:
            continue
        if recent.get(bare) is not None and (now - scoring._f(recent[bare])) < ttl:
            continue
        band = band_for(count, inputs)
        lev, mgn = sizing_for(band, inputs)
        recent[bare] = now
        free -= 1
        reasons = [f"{count}_smart_wallets_{direction}",
                   f"{pick['conviction']:.0f}%_one_sided", f"delta_+{pick['delta']:.0f}"]
        out.append({
            "asset": asset, "direction": direction, "marginPct": mgn, "leverage": lev,
            "data": {"consensusCount": count, "band": band, "direction": direction,
                     "reasons": reasons, "leverage": lev,
                     "oneSidedness": pick["conviction"], "delta": pick["delta"]},
        })
        entry_px = _price_lookup(asset)          # the ledger marks this signal in 1h
        if entry_px > 0:
            scoring.add_pending_signal(accuracy, scoring.asset_class_for(asset),
                                       asset, direction, entry_px, now)
        print(f"[pilotfish.scan] OPEN {direction} {asset}: {count} smart wallets agree "
              f"({pick['conviction']:.0f}% one-sided, delta +{pick['delta']:.0f}) "
              f"band {band} {lev}x {mgn}%", file=sys.stderr)

    if not out:
        print(f"[pilotfish.scan] no opens: cohort={len(cohort)} held={len(held)}/{max_slots} "
              f"free={free} names={len(cur)} fresh={len(fresh)}", file=sys.stderr)

    _persist(ctx, cohort, last_refresh, tilts, accuracy, recent, now, len(out), len(held))
    return out
