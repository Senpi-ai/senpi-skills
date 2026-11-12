"""RELATIVITY XYZ -- supervised scanner (index-regime cross-sectional dispersion, xyz board).

Ranks the live xyz: perp universe (equities, commodities, indices) by 24h relative strength
every tick. The index's 4h trend gates the regime: risk-off -> shorts only, risk-on -> longs
only, neutral -> both. When the index's 4h candle flips from negative to positive, long
candidates get a shift boost so the book rotates the same tick the tape turns.

Per tick:
  1. REGIME   -- index 4h/1h trend -> risk-off / neutral / risk-on, plus shift detection.
  2. UNIVERSE -- every listed xyz: perp above a volume floor, top N by 24h volume.
  3. RANK     -- 24h excess return vs the universe mean for each name.
  4. SCORE    -- scoring.score_candidate on each regime-allowed leg.
  5. EMIT     -- the best maxNewEntriesPerTick, bounded by free slots.

Read-only, single-pass, synchronous. Promoted from a live fork (15-19 Oct 2026).

TWO DEPARTURES from that fork, both plumbing rather than signal:

  * CLOSED BARS ONLY for the regime. The fork read candles[-1], which is the bar still
    forming -- market_get_asset_data's last row always is. A gate that flips the whole
    book's direction cannot be driven by a price that is still moving: risk-on/risk-off
    and the shift boost both flickered inside each 4h window. The in-progress bar is now
    dropped and the regime reads the last CLOSED 4h bar.
  * A REAL TTL on recent signals. `recentSignalTtlSeconds` was read and never used; names
    accumulated in a set that was then truncated with `list(set)[-50:]`, and set ordering
    is arbitrary -- so which names stayed suppressed changed from tick to tick for no
    reason. Entries now carry their emit time and expire on the configured TTL.

Everything that scores a candidate is byte-for-byte the fork's. See scoring.py.
"""

import sys
import time

import scoring

# -- Defaults (every one overridable from runtime.yaml inputs) --
DEFAULT_RS_THRESH = 2.5
DEFAULT_MIN_SCORE = 5
DEFAULT_UNIVERSE_MAX = 40
DEFAULT_VOL_FLOOR_PCT = 0.2
DEFAULT_MARGIN_PCT = 25
DEFAULT_MAX_LEVERAGE = 10
DEFAULT_MAX_NEW_PER_TICK = 2
DEFAULT_RECENT_TTL = 180
DEFAULT_MIN_VOLUME_USD_M = 5.0
DEFAULT_RISK_OFF = -1.5
DEFAULT_RISK_ON = 2.0
DEFAULT_REGIME_ASSET = "xyz:SP500"
DEFAULT_HEADROOM = 0.9
DEFAULT_MIN_NOTIONAL = 12.0


def _read(ctx, name, args, label=""):
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 -- degrade, never crash the tick
        print("[relativity-xyz.scan] %s read failed: %r" % (label or name, exc), file=sys.stderr)
        return None


def _unwrap(resp):
    if not isinstance(resp, dict):
        return resp
    return resp.get("data", resp)


def _as_list(d, keys=("data", "results", "instruments")):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and isinstance(v.get("data"), list):
                return v["data"]
    return []


def _close(c):
    if isinstance(c, dict):
        return float(c.get("close", c.get("c", 0)) or 0)
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4])
    return 0.0


def pct_change(old, new):
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def closed_bars(candles):
    """Drop the bar still forming. market_get_asset_data's last row is always in progress
    (its `T` end-time is in the future), and a regime gate read off a moving price flips
    back and forth inside one window."""
    if not isinstance(candles, list) or len(candles) < 2:
        return []
    return candles[:-1]


# -- REGIME ----------------------------------------------------------------------------

def index_regime(ctx, inputs):
    """Index 4h/1h trend -> regime, plus shift detection on CLOSED bars.

    Returns {regime, index_4h_pct, index_1h_pct, shift_detected}. An unreadable index is
    `neutral` with no shift -- both legs open, no boost -- never an invented direction.
    """
    risk_off = float(inputs.get("riskOffThreshold", DEFAULT_RISK_OFF))
    risk_on = float(inputs.get("riskOnThreshold", DEFAULT_RISK_ON))
    asset = str(inputs.get("regimeAsset", DEFAULT_REGIME_ASSET))
    neutral = {"regime": "neutral", "index_4h_pct": 0.0, "index_1h_pct": 0.0,
               "shift_detected": False}

    d = _unwrap(_read(ctx, "market_get_asset_data",
                      {"asset": asset, "candle_intervals": ["4h", "1h"],
                       "include_order_book": False, "include_funding": False,
                       "dex": "xyz"}, "regime %s" % asset))
    if not isinstance(d, dict):
        return neutral

    candles = d.get("candles", {}) or {}
    c4 = closed_bars(candles.get("4h", []) or [])
    c1 = closed_bars(candles.get("1h", []) or [])
    if len(c4) < 3:
        return neutral

    ch_4h = pct_change(_close(c4[-2]), _close(c4[-1]))
    ch_1h = pct_change(_close(c1[-2]), _close(c1[-1])) if len(c1) >= 2 else 0.0

    if ch_4h < risk_off:
        regime = "risk_off"
    elif ch_4h > risk_on:
        regime = "risk_on"
    else:
        regime = "neutral"

    prev_move = pct_change(_close(c4[-3]), _close(c4[-2]))
    shift = prev_move < 0 and ch_4h > 0

    return {"regime": regime, "index_4h_pct": round(ch_4h, 2),
            "index_1h_pct": round(ch_1h, 2), "shift_detected": shift}


def legs_for(regime):
    """risk-off shorts the laggards, risk-on longs the leaders, neutral does both."""
    if regime == "risk_off":
        return ["short"]
    if regime == "risk_on":
        return ["long"]
    return ["long", "short"]


# -- UNIVERSE --------------------------------------------------------------------------

def build_universe(rows, inputs, regime_asset):
    """Pure: instrument rows -> the ranked xyz: universe. The caller owns the read.

    The regime proxy is never traded -- a book whose gate is also a position is reading
    its own exposure.
    """
    max_names = int(inputs.get("universeMaxNames", DEFAULT_UNIVERSE_MAX))
    vol_floor_pct = float(inputs.get("volFloorPctOfMedian", DEFAULT_VOL_FLOOR_PCT))
    min_vol_m = float(inputs.get("minVolumeUsdM", DEFAULT_MIN_VOLUME_USD_M))

    out = []
    for inst in rows or []:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name or not str(name).startswith("xyz:"):
            continue
        if inst.get("is_delisted", False):
            continue
        if name == regime_asset:
            continue
        c = inst.get("context", {}) or {}
        if not c:
            continue
        try:
            vol_m = float(c.get("dayNtlVlm", 0) or 0) / 1_000_000.0
        except (TypeError, ValueError):
            vol_m = 0.0
        if vol_m < min_vol_m:
            continue
        prev = float(c.get("prevDayPx", 0) or 0)
        mark = float(c.get("markPx", 0) or 0)
        if prev <= 0 or mark <= 0:
            continue
        out.append({"name": str(name), "prev": prev, "mark": mark, "vol_m": vol_m,
                    "max_leverage": int(inst.get("max_leverage", 10) or 10)})

    out.sort(key=lambda x: -x["vol_m"])
    out = out[:max_names]

    if len(out) >= 4:
        vols = sorted(c["vol_m"] for c in out)
        floor = vols[len(vols) // 2] * vol_floor_pct
        out = [c for c in out if c["vol_m"] >= floor]
    return out


def rank(universe):
    """24h return per name, and each name's excess over the universe mean."""
    if not universe:
        return 0.0
    for u in universe:
        u["ret_24h"] = pct_change(u["prev"], u["mark"])
    mean = sum(u["ret_24h"] for u in universe) / len(universe)
    for u in universe:
        u["excess"] = u["ret_24h"] - mean
    return mean


# -- ACCOUNT ---------------------------------------------------------------------------

def account(ctx):
    """(held, withdrawable) read fresh from the venue every tick, or None when the
    clearinghouse is unreadable -- then the tick stands down rather than double-opening.

    `withdrawable` is what the free-margin gate spends. The 'main' and 'xyz' sections are
    two views of ONE cross-margined wallet, so it is taken with max(), never summed.
    """
    data = _unwrap(_read(ctx, "strategy_get_clearinghouse_state",
                         {"strategy_wallet": ctx.wallet}, "clearinghouse"))
    if not isinstance(data, dict):
        return None
    held, withdrawable, acct, used = set(), 0.0, 0.0, 0.0
    views = [v for v in data.values() if isinstance(v, dict)] + [data]
    for view in views:
        ms = view.get("marginSummary") if isinstance(view.get("marginSummary"), dict) else {}
        try:
            acct = max(acct, float(ms.get("accountValue", 0) or 0))
            used = max(used, float(ms.get("totalMarginUsed", 0) or 0))
            withdrawable = max(withdrawable, float(view.get("withdrawable", 0) or 0))
        except (TypeError, ValueError):
            pass
        for ap in (view.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            coin = pos.get("coin") or pos.get("asset")
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if coin and szi != 0.0:
                held.add(str(coin))
    if used > 1.0 and not held:
        print("[relativity-xyz.scan] margin in use but no positions -- corrupt read, "
              "standing down", file=sys.stderr)
        return None
    free = withdrawable if withdrawable > 0 else max(0.0, acct - used)
    return held, free


# -- RECENT SIGNALS --------------------------------------------------------------------

def live_recent(state_entry, now, ttl):
    """{asset: emitted_at} with anything past its TTL dropped.

    The fork stored a set and truncated it with `list(set)[-50:]`; set ordering is
    arbitrary, so which names stayed suppressed changed every tick. Timestamps make the
    suppression mean what the input says it means.
    """
    raw = (state_entry or {}).get("recent_signals") or {}
    if isinstance(raw, list):                 # migrate the fork's shape: no times, so expire now
        return {}
    out = {}
    for asset, ts in raw.items():
        try:
            if now - float(ts) < ttl:
                out[str(asset)] = float(ts)
        except (TypeError, ValueError):
            continue
    return out


# -- MAIN ------------------------------------------------------------------------------

def scan(inputs, ctx):
    min_score = int(inputs.get("minScore", DEFAULT_MIN_SCORE))
    rs_thresh = float(inputs.get("rsThresholdPct", DEFAULT_RS_THRESH))
    margin_pct = float(inputs.get("marginPct", DEFAULT_MARGIN_PCT))
    max_new = int(inputs.get("maxNewEntriesPerTick", DEFAULT_MAX_NEW_PER_TICK))
    max_slots = int(inputs.get("maxSlots", 4))
    ttl = float(inputs.get("recentSignalTtlSeconds", DEFAULT_RECENT_TTL))
    regime_asset = str(inputs.get("regimeAsset", DEFAULT_REGIME_ASSET))
    now = time.time()

    # 1) the book first: an unreadable clearinghouse must not open anything
    acct = account(ctx)
    if acct is None:
        print("[relativity-xyz.scan] clearinghouse unreadable -- no opens this tick", file=sys.stderr)
        return []
    held, free = acct
    slots = max_slots - len(held)
    if slots <= 0:
        return []

    # 2) regime
    reg = index_regime(ctx, inputs)
    legs = legs_for(reg["regime"])

    # 3) universe
    rows = _as_list(_unwrap(_read(ctx, "market_list_instruments", {"dex": "xyz"},
                                  "instruments xyz")))
    if not rows:
        print("[relativity-xyz.scan] xyz instrument list unreadable -- nothing to rank", file=sys.stderr)
        return []
    universe = build_universe(rows, inputs, regime_asset)
    if not universe:
        return []
    mean_ret = rank(universe)

    recent = live_recent(ctx.state.last() if ctx.state else None, now, ttl)

    # 4) score
    cands = []
    for u in universe:
        coin = u["name"]
        if coin in held or coin in recent:
            continue
        best = None
        for leg in legs:
            if leg == "long" and u["excess"] < rs_thresh * 0.5:
                continue
            if leg == "short" and u["excess"] > -rs_thresh * 0.5:
                continue
            d = _unwrap(_read(ctx, "market_get_asset_data",
                              {"asset": coin, "candle_intervals": ["1h", "4h"],
                               "include_order_book": False, "include_funding": True,
                               "dex": "xyz"}, "candles %s" % coin))
            if not isinstance(d, dict):
                continue
            candles = d.get("candles", {}) or {}
            # `asset_context` is the real key -- verified against a live response, 19 Oct 2026.
            actx = d.get("asset_context", {}) or {}
            try:
                funding = float(actx.get("funding", 0) or 0)
            except (TypeError, ValueError):
                funding = 0.0
            r = scoring.score_candidate(
                coin=coin,
                candles_1h=closed_bars(candles.get("1h", []) or []),
                candles_4h=closed_bars(candles.get("4h", []) or []),
                excess=u["excess"], own24h=u["ret_24h"], leg=leg, inputs=inputs,
                shift_detected=reg["shift_detected"], asset_funding=funding)
            if r and r["score"] >= min_score:
                r["leverage"] = scoring.clamp_leverage(r["leverage"], u["max_leverage"])
                if best is None or r["score"] > best["score"]:
                    best = r
        if best:
            cands.append(best)

    # 5) emit -- best score first, bounded by the tick cap, by free slots, AND by the
    #    margin actually available. Every signal in a tick is priced off the ONE balance
    #    read above, so without this the second and later opens land position_open_failed
    #    (insufficient margin) tick after tick, silently.
    cands.sort(key=lambda c: -c["score"])
    budget = free * float(inputs.get("freeMarginHeadroom", DEFAULT_HEADROOM))
    clip_usd = free * margin_pct / 100.0
    min_notional = float(inputs.get("venueMinNotionalUsd", DEFAULT_MIN_NOTIONAL))
    out = []
    for c in cands[:max_new][:slots]:
        if clip_usd * c["leverage"] < min_notional:
            print("[relativity-xyz.scan] a %.0f%% clip of $%.0f free margin is under the "
                  "$%.0f venue minimum -- no opens this tick"
                  % (margin_pct, free, min_notional), file=sys.stderr)
            break
        if clip_usd > budget:
            print("[relativity-xyz.scan] free margin exhausted after %d signal(s) -- the "
                  "rest wait for the next tick" % len(out), file=sys.stderr)
            break
        budget -= clip_usd
        recent[c["coin"]] = now
        out.append({
            "asset": c["coin"],
            "direction": c["direction"],
            "marginPct": margin_pct,
            "leverage": c["leverage"],
            "data": {
                "score": c["score"], "direction": c["direction"],
                "leverage": c["leverage"], "marginPct": margin_pct,
                "reasons": c.get("reasons", []), "regime": reg["regime"],
                "shiftDetected": reg["shift_detected"], "excess": c.get("excess"),
                "own24h": c.get("own24h"), "rsi": c.get("rsi"),
                "trend4h": c.get("trend4h"), "trend1h": c.get("trend1h"),
                "funding": c.get("funding"),
            },
        })

    if ctx.state is not None:
        try:
            ctx.state.append({"recent_signals": recent, "regime": reg["regime"],
                              "shift_detected": reg["shift_detected"],
                              "universe_size": len(universe), "mean_24h": mean_ret,
                              "index_4h_pct": reg["index_4h_pct"], "timestamp": now})
        except Exception as exc:  # noqa: BLE001
            print("[relativity-xyz.scan] state append failed: %r" % exc, file=sys.stderr)

    return out
