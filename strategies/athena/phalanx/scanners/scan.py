"""PHALANX — supervised scanner (proven-cohort rotation, Runtime 3.0).

THESIS — follow the PROVEN COHORT's flow, not the 4h leaderboard's state.
A state-based follower reads leaderboard_get_markets (the 4h gain board), a
*consequence* of price moves, not a predictor — by the time 58% of the
4h board is long ETH, ETH already moved. Phalanx instead reads the
proven cohort (>=$1M lifetime realized PnL, via discovery_get_top_traders
ALL_TIME) and tracks their headcount positioning + tick-over-tick delta.

Per tick:
  1. Read account state + held positions (dual-DEX, read-sanity guard).
  2. Refresh proven cohort daily (discovery_get_top_traders, cached in state).
  3. Snapshot cohort positions (discovery_get_trader_state, batched 50).
  4. Aggregate per-asset headcount: {ASSET: {long_n, short_n}}.
  5. Read 4h leaderboard (leaderboard_get_markets) for divergence detection.
  6. Evaluate past signal accuracy per asset class (price-based proxy).
  7. For each asset passing gates (one_sidedness >= threshold + delta >= +2
     + overcrowding breakout check), compute conviction with divergence
     booster and price confirmation multiplier.
  8. Emit up to maxLong longs + maxShort shorts, conviction-ranked, deduped.

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon,
no push_signal, no create_position. EVERY MCP call is read-guarded.
"""

import sys
import time

import scoring

# ── defaults (overridable via runtime.yaml inputs) ───────────────────────────
_DEFAULT_TILT_THRESHOLD = 65.0   # min one_sidedness to qualify
_DEFAULT_DELTA_MIN = 2.0          # min directional delta since last tick
_DEFAULT_OVERCROWD = 85.0         # one_sidedness above which breakout is required
_DEFAULT_MARGIN_PCT = 15.0        # baseline % of withdrawable
_DEFAULT_LEVERAGE = 3             # clamped to [1,4]
_MIN_LEVERAGE = 1
_MAX_LEVERAGE = 4
_DEFAULT_MAX_LONG = 2
_DEFAULT_MAX_SHORT = 2
_DEFAULT_RECENT_TTL = 240         # signal-dedup TTL (s)
_DEFAULT_COHORT_CAP = 100         # max proven traders to track
_DEFAULT_SMART_MIN_REALIZED = 1_000_000  # >=$1M lifetime realized
_DEFAULT_STATE_BATCH = 50         # discovery_get_trader_state batch size
_DEFAULT_LEADERBOARD_LIMIT = 100
_COHORT_REFRESH_S = 86400         # 24h cohort cache refresh

def _read(ctx, name, args, label):
    """Guarded MCP read — a transient error degrades, never crashes the tick."""
    try:
        raw = ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[phalanx.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw

def _dex_for(asset):
    """XYZ (HIP-3) assets need dex='xyz' for market reads."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""

# ── ACCOUNT + HELD ASSETS ─────────────────────────────────────────────────────

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts).
    """
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = d.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
            })

    # read-sanity guard: margin in use but empty positions -> skip tick
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = d.get(_sec, {}) if isinstance(d, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[phalanx.scan] read-sanity guard: margin in use but empty positions — skipping",
              file=sys.stderr)
        return 0.0, []

    return account_value, positions

# ── PROVEN COHORT (daily cache) ───────────────────────────────────────────────

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

def _refresh_cohort(ctx, inputs):
    """Fetch proven cohort: discovery_get_top_traders ALL_TIME, >=$1M realized."""
    smin = scoring._f(inputs.get("smartMinRealizedUsd"), _DEFAULT_SMART_MIN_REALIZED)
    cap = int(scoring._f(inputs.get("cohortCap"), _DEFAULT_COHORT_CAP))
    psize = int(scoring._f(inputs.get("pageSize"), 1000))
    pages = int(scoring._f(inputs.get("maxPages"), 6))

    smart, seen = [], set()
    for page in range(pages):
        d = _read(ctx, "discovery_get_top_traders",
                  {"time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
                   "open_position_filter": False, "limit": psize, "offset": page * psize},
                  f"discovery_get_top_traders(p{page})")
        rows = _traders_of(d)
        if not rows:
            break
        for t in rows:
            if not isinstance(t, dict):
                continue
            a = _trader_address(t)
            if not a or a in seen:
                continue
            if _realized(t) >= smin and len(smart) < cap:
                smart.append(a)
                seen.add(a)
        if len(rows) < psize or len(smart) >= cap:
            break
    return smart

def _get_cohort(ctx, inputs, prev_state):
    """Return (addresses, refreshed_at). Refreshes daily, else carries forward."""
    now = time.time()
    prev_cohort = (prev_state or {}).get("cohort", {})
    refreshed_at = scoring._f(prev_cohort.get("refreshed_at", 0))
    addresses = prev_cohort.get("addresses", [])

    if not addresses or (now - refreshed_at) >= _COHORT_REFRESH_S:
        fresh = _refresh_cohort(ctx, inputs)
        if fresh:
            return fresh, now
        # refresh failed — keep the old cohort (better than empty)
        if addresses:
            print("[phalanx.scan] cohort refresh failed — using cached cohort", file=sys.stderr)
            return addresses, refreshed_at
        print("[phalanx.scan] no cohort available (refresh failed, no cache)", file=sys.stderr)
        return [], 0.0

    return addresses, refreshed_at

# ── COHORT POSITIONING (headcount per asset) ──────────────────────────────────

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
    batch = int(scoring._f(inputs.get("stateBatch"), _DEFAULT_STATE_BATCH))
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
        print("[phalanx.scan] all trader_state batches failed — no headcount this tick",
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

# ── 4H LEADERBOARD (crowd read for divergence) ────────────────────────────────

def _crowd_lean(ctx, limit):
    """Per-asset long_ratio from the 4h gain leaderboard.

    Returns {BARE_UPPER: long_ratio} where long_ratio is the 4h board's
    long% (NOT smart money — this is the crowd/momentum read).
    """
    raw = _read(ctx, "leaderboard_get_markets",
                {"limit": limit}, "leaderboard_get_markets")
    if not isinstance(raw, list):
        if isinstance(raw, dict):
            for k in ("markets", "data", "results"):
                if isinstance(raw.get(k), list):
                    raw = raw[k]
                    break
            else:
                return {}
        else:
            return {}

    by_asset = {}
    for m in raw:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", ""))))
        if not token:
            continue
        dex = str(m.get("dex", "")).lower()
        direction = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        key = (token.upper(), dex)
        row = by_asset.setdefault(key, {"long_pct": 0.0, "short_pct": 0.0})
        if direction == "long":
            row["long_pct"] += pct
        elif direction == "short":
            row["short_pct"] += pct

    out = {}
    for (tok, dex), row in by_asset.items():
        lr, _ = scoring.net_tilt(row["long_pct"], row["short_pct"])
        out[tok] = lr
    return out

# ── PRICE READS (trend + breakout + current price) ────────────────────────────

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

# ── SIGNAL EMIT ───────────────────────────────────────────────────────────────

def _emit_side(candidates, direction, max_count, held_set, signaled, ttl, now):
    """Filter + emit signals for one direction. Returns (emits, updated_signaled)."""
    out = []
    for c in candidates:
        if len(out) >= max_count:
            break
        asset = c["asset"]
        sig_key = f"{asset}_{direction}"
        if scoring.bare_upper(asset) in held_set:
            continue
        if sig_key in signaled and (now - signaled[sig_key]) < ttl:
            continue
        out.append({
            "asset": asset,
            "direction": direction,
            "marginPct": c["marginPct"],
            "leverage": c["leverage"],
            "data": {
                "score": c["conviction"],
                "direction": direction,
                "longRatio": c.get("long_ratio"),
                "netTilt": c.get("net_tilt"),
                "conviction": c["conviction"],
                "delta": c.get("delta"),
                "divergence": c.get("divergence", False),
                "oneSidedness": c.get("one_sidedness"),
                "reasons": c.get("reasons", []),
            },
        })
        signaled[sig_key] = now
    return out, signaled

# ── MAIN SCAN ─────────────────────────────────────────────────────────────────

def scan(inputs, ctx):
    now = time.time()

    # ── tunables ──
    tilt_threshold = scoring._f(inputs.get("tiltThreshold"), _DEFAULT_TILT_THRESHOLD)
    delta_min = scoring._f(inputs.get("deltaMin"), _DEFAULT_DELTA_MIN)
    overcrowd = scoring._f(inputs.get("overcrowdThreshold"), _DEFAULT_OVERCROWD)
    base_margin = scoring._f(inputs.get("marginPctBase"), _DEFAULT_MARGIN_PCT)
    margin_max = scoring._f(inputs.get("marginPctMax"), 0)
    leverage = int(scoring._f(inputs.get("leverageDefault"), _DEFAULT_LEVERAGE))
    leverage = max(_MIN_LEVERAGE, min(_MAX_LEVERAGE, leverage))
    max_long = int(scoring._f(inputs.get("maxLong"), _DEFAULT_MAX_LONG))
    max_short = int(scoring._f(inputs.get("maxShort"), _DEFAULT_MAX_SHORT))
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), _DEFAULT_RECENT_TTL)
    lb_limit = int(scoring._f(inputs.get("leaderboardLimit"), _DEFAULT_LEADERBOARD_LIMIT))

    # ── account + held ──
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[phalanx.scan] cannot read account value; skip tick", file=sys.stderr)
        _persist_state(ctx, prev_state=None, signaled={}, result={"ts": now, "emitted": False, "gate": "no_account"})
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {scoring.bare_upper(h) for h in held_assets}

    # ── load previous state ──
    prev = (ctx.state.last() or {}) if ctx.state else {}
    prev_tilts = prev.get("prev_tilts", {})
    accuracy_state = prev.get("accuracy", scoring.new_accuracy_state())
    if not isinstance(accuracy_state, dict):
        accuracy_state = scoring.new_accuracy_state()
    signaled = prev.get("signaled", {})
    if not isinstance(signaled, dict):
        signaled = {}

    # prune expired dedup entries
    signaled = {k: v for k, v in signaled.items() if (now - v) < ttl * 3}

    # ── proven cohort (daily cache) ──
    cohort, cohort_refreshed = _get_cohort(ctx, inputs, prev)
    if not cohort:
        print("[phalanx.scan] no proven cohort; skip tick", file=sys.stderr)
        _persist_state(ctx, prev, cohort_refreshed, cohort, prev_tilts,
                       accuracy_state, signaled, {"ts": now, "emitted": False, "gate": "no_cohort"})
        return []

    # ── cohort positioning (headcount per asset) ──
    headcount = _cohort_headcount(ctx, cohort, inputs)
    if not headcount:
        print("[phalanx.scan] empty headcount; skip tick", file=sys.stderr)
        _persist_state(ctx, prev, cohort_refreshed, cohort, prev_tilts,
                       accuracy_state, signaled, {"ts": now, "emitted": False, "gate": "no_headcount"})
        return []

    # ── 4h leaderboard (crowd read for divergence) ──
    crowd_lean = _crowd_lean(ctx, lb_limit)

    # ── evaluate past signal accuracy ──
    def _price_lookup(asset):
        c1h, _ = _asset_data(ctx, asset)
        return _current_price(c1h)

    for cls in ("crypto", "xyz"):
        accuracy_state = scoring.update_accuracy(accuracy_state, cls, now, _price_lookup)

    # ── build candidates ──
    long_candidates, short_candidates = [], []
    new_tilts = {}

    for asset_key, rec in headcount.items():
        long_n = rec["long_n"]
        short_n = rec["short_n"]
        raw_coin = rec.get("raw_coin", asset_key)

        # store for next tick's delta
        new_tilts[asset_key] = {"long_n": long_n, "short_n": short_n}

        long_ratio, net_tilt = scoring.net_tilt(long_n, short_n)
        sided = scoring.one_sidedness(long_n, short_n)

        # direction: LONG if cohort is net long, SHORT otherwise
        direction = "LONG" if long_n >= short_n else "SHORT"

        # per-class threshold adjustment
        cls = scoring.asset_class_for(raw_coin)
        eff_threshold = scoring.adjusted_threshold(tilt_threshold, cls, accuracy_state)

        # gate 1: one_sidedness >= threshold
        if sided < eff_threshold:
            continue

        # gate 2: delta >= delta_min (conviction growing)
        prev_rec = prev_tilts.get(asset_key, {})
        delta = scoring.directional_delta(
            prev_rec.get("long_n", 0), prev_rec.get("short_n", 0),
            long_n, short_n, direction,
        )
        if delta < delta_min:
            continue

        # gate 3: overcrowding breakout check
        reasons = []
        if sided >= overcrowd:
            c1h, c4h = _asset_data(ctx, raw_coin)
            if not scoring.breakout_check(c1h, direction):
                continue  # crowded but no breakout — move is exhausted
            reasons.append(f"overcrowded ({sided:.0f}%) with breakout confirmation")
        else:
            # only read candles if we haven't already
            c1h, c4h = _asset_data(ctx, raw_coin)

        # trend structure (price confirmation)
        trend_label, trend_strength = scoring.trend_structure(c4h or [])
        price_mult = scoring.price_conviction_mult(trend_label, direction)

        # divergence booster
        crowd_lr = crowd_lean.get(asset_key, 50.0)
        divergent = scoring.is_divergent(long_ratio, crowd_lr)
        divergence_mult = 1.5 if divergent else 1.0

        if divergent:
            reasons.append(f"divergence: cohort {direction} vs 4h board {'LONG' if crowd_lr > 50 else 'SHORT'}")

        if trend_label != "NEUTRAL":
            reasons.append(f"4h trend {trend_label.lower()} ({trend_strength:.0%})")

        reasons.append(f"cohort {long_n}L/{short_n}S ({sided:.0f}% one-sided), delta +{delta:.0f}")

        # conviction = one_sidedness * price_mult * divergence_mult
        conviction = sided * price_mult * divergence_mult

        # margin sizing
        margin_pct = scoring.margin_pct_for(conviction, base_margin, margin_max if margin_max > 0 else None)

        candidate = {
            "asset": raw_coin,
            "direction": direction,
            "conviction": round(conviction, 2),
            "one_sidedness": round(sided, 1),
            "long_ratio": round(long_ratio, 1),
            "net_tilt": round(net_tilt, 1),
            "delta": round(delta, 1),
            "divergence": divergent,
            "marginPct": round(margin_pct, 2),
            "leverage": leverage,
            "reasons": reasons,
        }

        if direction == "LONG":
            long_candidates.append(candidate)
        else:
            short_candidates.append(candidate)

    # ── rank + emit ──
    long_candidates = scoring.rank_signals(long_candidates)
    short_candidates = scoring.rank_signals(short_candidates)

    long_emits, signaled = _emit_side(
        long_candidates, "LONG", max_long, held_set, signaled, ttl, now)
    short_emits, signaled = _emit_side(
        short_candidates, "SHORT", max_short, held_set, signaled, ttl, now)

    out = long_emits + short_emits

    # ── record emitted signals for accuracy tracking ──
    for e in out:
        cls = scoring.asset_class_for(e["asset"])
        c1h, _ = _asset_data(ctx, e["asset"])
        entry_px = _current_price(c1h)
        if entry_px > 0:
            scoring.add_pending_signal(accuracy_state, cls, e["asset"], e["direction"], entry_px, now)

    # ── log + persist state ──
    if out:
        print(f"[phalanx.scan] EMIT {len(long_emits)}L + {len(short_emits)}S "
              f"| cohort={len(cohort)} assets={len(headcount)} "
              f"| {[ (e['asset'], e['direction'], e['data'].get('conviction')) for e in out ]}",
              file=sys.stderr)
        result = {"ts": now, "emitted": True, "board": len(headcount),
                  "emittedAssets": [(e["asset"], e["direction"]) for e in out],
                  "held": held_assets}
    else:
        print(f"[phalanx.scan] WAITING — no emit | cohort={len(cohort)} assets={len(headcount)} "
              f"long_cands={len(long_candidates)} short_cands={len(short_candidates)} "
              f"held={held_assets} (threshold {tilt_threshold:.0f}, delta_min {delta_min:.0f})",
              file=sys.stderr)
        result = {"ts": now, "emitted": False, "gate": "no_signal", "board": len(headcount),
                  "held": held_assets}

    _persist_state(ctx, prev, cohort_refreshed, cohort, new_tilts,
                   accuracy_state, signaled, result)

    return out

def _persist_state(ctx, prev, cohort_refreshed, cohort, new_tilts,
                   accuracy_state, signaled, result):
    """Persist the full state record for next tick."""
    if ctx.state is None:
        return
    cohort_refreshed = cohort_refreshed if cohort_refreshed else (
        scoring._f((prev or {}).get("cohort", {}).get("refreshed_at", 0)))
    cohort_list = cohort if cohort else (
        (prev or {}).get("cohort", {}).get("addresses", []))
    record = {
        "ts": result.get("ts", time.time()),
        "cohort": {"addresses": cohort_list, "refreshed_at": cohort_refreshed},
        "prev_tilts": new_tilts if new_tilts else (prev or {}).get("prev_tilts", {}),
        "accuracy": accuracy_state,
        "signaled": signaled,
        "result": result,
    }
    try:
        ctx.state.append(record)
    except Exception as exc:  # noqa: BLE001
        print(f"[phalanx.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
