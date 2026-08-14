"""BARRACUDA — the supervised entry scanner.

A dynamic, dual-direction breakout/pump screener. Every tick it rebuilds a live
universe of liquid crypto AND xyz: equity perps (no fixed whitelist), scores each
candidate independently for LONG (breakout) and SHORT (breakdown) via the pure
`scoring.py` engine, keeps the single strongest direction per coin, and emits the
top few as OPEN signals. The runtime sizes, executes, dedups, and runs the DSL exit.

Each tick:
  1) REGIME — BTC 4h/1h trend gates direction (crash blocks longs, euphoria blocks
     shorts) and the global funding regime flags a crowded side to fade.
  2) UNIVERSE — leaderboard_get_top gives smart-money counts; market_get_prices
     gives the full liquid symbol set. Coins already held (anti-flip) or signalled
     within the recent-signal TTL are dropped; the rest are ranked by smart-money
     coverage and capped at max_scan_coins.
  3) SCORE — one market_get_asset_data (15m/1h/4h) per candidate; candle momentum +
     acceleration + RSI + funding squeeze + OI velocity + size bonus + a 15m
     fast-track, with per-asset-class RSI/overextension bands and overextension
     guards. `scoring.combine_score()` folds the weighted total per direction.
  4) EMIT — top max_new_entries_per_tick signals, each with a `marginPct` intent
     (compounds: the runtime sizes marginPct% of *withdrawable*) and `leverage`.

Read-only + single-pass + synchronous. No daemon, no sleep, no writes. On any
failure a read returns None / a candidate is skipped — the tick never crashes.
`marginPct` is a PERCENT in (0,100]. Sizing/execution/exits are the runtime's job —
this scanner never reads the clearinghouse to size (only to skip names already held).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import concurrent.futures
import sys
import time

import scoring
from scoring import _f, is_xyz, pct_change

# ─── Engine defaults (overridable via runtime.yaml `inputs:`) ─────────
DEFAULT_MIN_SCORE = 22          # the engine's entry bar (runtime sets a calibrated input)

# ─── Directional gates (orchestration, not pure scoring) ─────────────
MIN_1H_MOMENTUM = 0.5           # % — 1h must move at least this in the trade direction
MAX_1H_MOVE_PCT = 12.0          # skip coins already ±12% in 1h (hard overextension skip)
MAX_1H_LONG_ENTRY = 1.75        # don't LONG crypto after +1.75% 1h (buying the top)
MAX_1H_SHORT_ENTRY = 1.75       # don't SHORT crypto after -1.75% 1h (shorting the bottom)
MAX_1H_LONG_ENTRY_XYZ = 1.5     # xyz equities trend harder — slightly tighter late-entry guard
MAX_1H_SHORT_ENTRY_XYZ = 1.5
FAST_15M_GATE = 0.3             # don't SHORT a coin bouncing >0.3% / LONG one dropping >0.3% in 15m
OI_FLAT_SMART_OVERRIDE = 3      # FLAT OI blocks a direction unless >= this many smart traders hold it

# ─── Market/funding regime thresholds ────────────────────────────────
REGIME_4H_DROP_PCT = -1.5       # BTC 4h < this → crash regime (longs gated)
REGIME_4H_PUMP_PCT = 2.0        # BTC 4h > this → euphoria regime (shorts gated)
FUNDING_LONG_CROWDED_RATIO = 0.6
FUNDING_SHORT_CROWDED_RATIO = 0.6


def _read(ctx, tool, args, label):
    """One read-only MCP call, guarded. Returns the unwrapped payload, or None on
    any failure (logged to stderr). The runtime owns the per-tick timeout via the
    scanner's `timeout_seconds` — there is no hand-rolled per-call timeout wrapper."""
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args=args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[barracuda.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _as_list(d, keys=("data", "results", "prices", "leaderboard")):
    """Tolerant list extraction from a bare list or a {key: [...]} / {key: {data: [...]}} dict."""
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


def _market_regime(ctx):
    """BTC 4h/1h trend → regime. Fail-open to 'normal' so a read failure never
    silently blocks every direction."""
    normal = {"regime": "normal", "btc_4h_change": 0.0, "btc_1h_change": 0.0}
    d = _read(ctx, "market_get_asset_data",
              {"asset": "BTC", "candle_intervals": ["4h", "1h"], "dex": ""}, "regime BTC")
    if not isinstance(d, dict):
        return normal
    candles = d.get("candles", {}) or {}
    c4 = candles.get("4h", []) or []
    c1 = candles.get("1h", []) or []
    if len(c4) < 2:
        return normal
    ch_4h = pct_change(c4[-2].get("c"), c4[-1].get("c"))
    ch_1h = pct_change(c1[-2].get("c"), c1[-1].get("c")) if len(c1) >= 2 else 0.0
    if ch_4h < REGIME_4H_DROP_PCT:
        regime = "crash_bottoming" if ch_1h > 0.1 else "crash"
    elif ch_4h > REGIME_4H_PUMP_PCT:
        regime = "euphoria_topping" if ch_1h < -0.1 else "euphoria"
    else:
        regime = "normal"
    return {"regime": regime, "btc_4h_change": round(ch_4h, 2), "btc_1h_change": round(ch_1h, 2)}


def _funding_regime(ctx):
    """Global funding crowding → 'neutral' | 'long_crowded' | 'short_crowded'."""
    d = _read(ctx, "market_get_funding_regime", {}, "funding_regime")
    if not isinstance(d, dict):
        return "neutral"
    bias = str(d.get("regime", "NEUTRAL")).lower()
    long_c = _f(d.get("long_funding_assets", 0))
    short_c = _f(d.get("short_funding_assets", 0))
    total = long_c + short_c
    if total > 0:
        if long_c / total >= FUNDING_LONG_CROWDED_RATIO:
            bias = "long_crowded"
        elif short_c / total >= FUNDING_SHORT_CROWDED_RATIO:
            bias = "short_crowded"
    return bias


def _open_positions(ctx):
    """Bare-upper set of coins with an open position on this wallet (dual-DEX).
    Used ONLY to skip names already held (anti-flip) — never to size."""
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "clearinghouse")
    held = set()
    if not isinstance(d, dict):
        return held
    rows = []
    for sec in ("main", "xyz"):
        s = d.get(sec)
        if isinstance(s, dict):
            rows.extend(s.get("assetPositions", s.get("asset_positions", [])) or [])
    if not rows:  # legacy/flat shape
        rows = d.get("assetPositions", d.get("asset_positions", [])) or []
    for e in rows:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        if coin and _f(pos.get("szi")) != 0:
            held.add(coin.split(":", 1)[-1].upper())
    return held


def _universe(ctx, lb_limit):
    """(smart_count, all_coins). smart_count[coin] = how many top traders hold it;
    all_coins = the full liquid symbol set (leaderboard holdings ∪ market_get_prices).
    Tolerant of both the list-of-rows and symbol→price map shapes of the price feed."""
    smart_count = {}
    lb = _read(ctx, "leaderboard_get_top", {"limit": lb_limit}, "leaderboard_get_top")
    for t in _as_list(lb):
        if not isinstance(t, dict):
            continue
        for coin in (t.get("top_markets") or []):
            if isinstance(coin, str) and coin:
                smart_count[coin] = smart_count.get(coin, 0) + 1

    all_coins = set(smart_count)
    prices = _read(ctx, "market_get_prices", {}, "market_get_prices")
    price_syms = []
    if isinstance(prices, dict):
        pm = prices.get("prices", prices)
        if isinstance(pm, dict):
            price_syms = [s for s in pm.keys() if isinstance(s, str)]
        elif isinstance(pm, list):
            price_syms = [(p.get("symbol") or p.get("coin") or p.get("asset"))
                          for p in pm if isinstance(p, dict)]
    elif isinstance(prices, list):
        price_syms = [(p.get("symbol") or p.get("coin") or p.get("asset"))
                      for p in prices if isinstance(p, dict)]
    for sym in price_syms:
        if isinstance(sym, str) and sym:
            all_coins.add(sym)
            smart_count.setdefault(sym, 0)
    return smart_count, all_coins


def _fetch_assets(ctx, coins, workers):
    """Fetch 15m/1h/4h candles + context for each coin, in a bounded thread pool.
    The pool is created and joined WITHIN this call (single-pass — not a daemon);
    the runtime's `timeout_seconds` bounds the whole tick. Each read is guarded, so
    a slow/failed coin is simply absent from the result."""
    out = {}

    def _one(coin):
        d = _read(ctx, "market_get_asset_data",
                  {"asset": coin, "candle_intervals": ["15m", "1h", "4h"],
                   "dex": "xyz" if is_xyz(coin) else ""},
                  f"asset_data {coin}")
        return coin, d

    workers = max(1, int(workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for coin, d in ex.map(_one, coins):
            if isinstance(d, dict):
                out[coin] = d
    return out


def scan(inputs, ctx):
    # ── inputs (all tunables live in runtime.yaml) ──
    min_score = _f(inputs.get("min_score"), DEFAULT_MIN_SCORE)
    min_score_fast = _f(inputs.get("min_score_fast"), max(1.0, min_score - 2.0))
    max_positions = int(_f(inputs.get("max_positions"), 4))
    margin_pct = _f(inputs.get("margin_pct"), 30.0)
    base_leverage = int(_f(inputs.get("default_leverage"), 5))
    xyz_max_leverage = int(_f(inputs.get("xyz_max_leverage"), 6))
    min_vol = _f(inputs.get("min_volume_usd_m"), 15.0)
    min_vol_xyz = _f(inputs.get("min_volume_usd_m_xyz"), 5.0)
    rsi_ob = _f(inputs.get("rsi_overbought"), 72.0)
    rsi_os = _f(inputs.get("rsi_oversold"), 28.0)
    rsi_ob_xyz = _f(inputs.get("rsi_overbought_xyz"), 80.0)
    rsi_os_xyz = _f(inputs.get("rsi_oversold_xyz"), 20.0)
    max_scan_coins = int(_f(inputs.get("max_scan_coins"), 200))
    workers = int(_f(inputs.get("parallel_workers"), 8))
    max_new = int(_f(inputs.get("max_new_entries_per_tick"), 2))
    recent_ttl = _f(inputs.get("recent_signal_ttl_seconds"), 180.0)
    lb_limit = int(_f(inputs.get("smart_money_leaderboard_limit"), 50))

    now = time.time()

    # ── recent-signal ledger (anti-churn): asset -> last-signalled ts ──
    st = (ctx.state.last() or {}) if ctx.state is not None else {}
    recent = {a: _f(ts) for a, ts in (st.get("recent", {}) or {}).items()
              if now - _f(ts) < recent_ttl}

    # ── regime + open positions ──
    regime = _market_regime(ctx)
    regime_val = regime["regime"]
    funding_bias = _funding_regime(ctx)
    held = _open_positions(ctx)

    # ── universe ──
    smart_count, all_coins = _universe(ctx, lb_limit)
    if not all_coins:
        print("[barracuda.scan] empty universe (leaderboard + prices both unreadable)",
              file=sys.stderr)
        _persist(ctx, recent, now, 0, 0)
        return []

    # ── pre-filter (cheap, no per-coin MCP): drop held + recently-signalled ──
    scan_coins = [c for c in all_coins
                  if c.split(":", 1)[-1].upper() not in held and c not in recent]
    # rank by smart-money coverage so the highest-signal names are always covered,
    # then cap the per-tick fetch work (a bound, not a whitelist — coverage rotates)
    scan_coins.sort(key=lambda c: smart_count.get(c, 0), reverse=True)
    scan_coins = scan_coins[:max_scan_coins]

    coin_data = _fetch_assets(ctx, scan_coins, workers)

    # ── score every candidate ──
    candidates = []
    for coin in scan_coins:
        md = coin_data.get(coin)
        if not isinstance(md, dict):
            continue
        try:
            cand = _score_coin(coin, md, smart_count.get(coin, 0), regime_val, funding_bias,
                               min_score, min_score_fast,
                               min_vol_xyz if is_xyz(coin) else min_vol,
                               (rsi_ob_xyz, rsi_os_xyz) if is_xyz(coin) else (rsi_ob, rsi_os))
        except Exception as exc:  # noqa: BLE001 — one bad coin never sinks the tick
            print(f"[barracuda.scan] score {coin} failed: {exc!r}", file=sys.stderr)
            continue
        if cand:
            candidates.append(cand)

    candidates.sort(key=lambda c: c["total_score"], reverse=True)

    if candidates:
        dbg = [{"a": c["asset"], "d": c["direction"], "s": c["total_score"]}
               for c in candidates[:5]]
        print(f"[barracuda.scan] top candidates: {dbg}", file=sys.stderr)
    else:
        print(f"[barracuda.scan] no candidates (scanned {len(scan_coins)} of "
              f"{len(all_coins)} coins, regime={regime_val}, funding={funding_bias})",
              file=sys.stderr)

    candidates = candidates[:max_positions][:max_new]

    # ── emit (marginPct = intent; the runtime sizes marginPct% of withdrawable) ──
    signals = []
    for c in candidates:
        lev = min(base_leverage, xyz_max_leverage) if is_xyz(c["asset"]) else base_leverage
        signals.append({
            "asset": c["asset"],
            "direction": c["direction"],
            "marginPct": round(margin_pct, 2),
            "leverage": lev,
            "data": {
                "total_score": round(c["total_score"], 1),
                "momentum_1h": round(c["ch_1h"], 2),
                "momentum_4h": round(c["ch_4h"], 2),
                "candle_score": round(c["cs"], 1),
                "smart_money_score": round(c["sm_score"], 1),
                "funding_score": round(c["fs"], 1),
                "volume_usd_m": round(c["volume_24h"], 2),
                "funding_annualized_pct": round(c["fund_pct"], 2),
                "rsi": c["rsi"] if c["rsi"] is not None else 0,
                "acceleration": round(c["accel_adj"], 1),
                "oi_score": round(c["oi_adj"], 1),
                "oi_trend": c["oi_trend"],
                "size_score": round(c["size_score"] * scoring.SIZE_WEIGHT, 1),
                "market_size": c["market_size"],
                "fast_track": bool(c["fast_track"]),
                "ch_15m": c["ch_15m"] if c["ch_15m"] is not None else 0,
                "conviction": c["conviction"],
            },
        })
        recent[c["asset"]] = now

    _persist(ctx, recent, now, len(scan_coins), len(signals))
    return signals


def _score_coin(coin, md, smart, regime_val, funding_bias,
                min_score, min_score_fast, min_vol, rsi_bands):
    """Score one coin in both directions; return the stronger qualifying candidate
    dict, or None. Pure gates + `scoring` math — no MCP, no state."""
    candles = md.get("candles", {}) or {}
    c1h = candles.get("1h", []) or []
    c4h = candles.get("4h", []) or []
    c15m = candles.get("15m", []) or []
    if len(c1h) < 10 or len(c4h) < 5:
        return None

    ac = md.get("asset_context", {}) or {}
    mark_price = _f(ac.get("markPx"))
    volume_24h = _f(ac.get("dayNtlVlm")) / 1e6   # $M
    if volume_24h < min_vol:
        return None

    # 1h / 4h change (fall back to the 1h series for 4h if the 4h close is flat)
    ch_1h = pct_change(c1h[-2].get("c"), c1h[-1].get("c")) if len(c1h) >= 2 else 0.0
    ch_4h = pct_change(c4h[-2].get("c"), c4h[-1].get("c")) if len(c4h) >= 2 else 0.0
    if ch_4h == 0 and len(c1h) >= 5:
        ch_4h = pct_change(c1h[-5].get("c"), c1h[-1].get("c"))

    if abs(ch_1h) > MAX_1H_MOVE_PCT:
        return None
    max_long = MAX_1H_LONG_ENTRY_XYZ if is_xyz(coin) else MAX_1H_LONG_ENTRY
    max_short = MAX_1H_SHORT_ENTRY_XYZ if is_xyz(coin) else MAX_1H_SHORT_ENTRY
    long_blocked = ch_1h > max_long
    short_blocked = ch_1h < -max_short

    candle_score = scoring.score_candle_multi(c1h, c4h, mark_price)
    if not candle_score:
        return None
    if candle_score["long_score"] < 1.0 and candle_score["short_score"] < 1.0:
        return None

    fast_mom = scoring.score_fast_momentum(c15m)
    is_fast_track = bool(fast_mom and fast_mom.get("is_fast_track"))
    in_consolidation = scoring.detect_consolidation(c1h, lookback=6)

    price_score = scoring.score_price_change(ch_1h, ch_4h)
    if not price_score:
        # a fast-track consolidation breakout can have a small 4h (mostly flat) —
        # bypass the 4h momentum requirement only for that case
        if not (is_fast_track and in_consolidation):
            return None
        price_score = {"long_score": 0.0, "short_score": 0.0, "ch_1h": ch_1h, "ch_4h": ch_4h}
        if ch_1h > 0:
            price_score["long_score"] = min(3.0, ch_1h * 2)
        elif ch_1h < 0:
            price_score["short_score"] = min(3.0, abs(ch_1h) * 2)

    rsi = scoring.compute_rsi(c1h)
    accel = scoring.score_momentum_acceleration(c1h)

    # funding from asset_context (per-8h rate → annualized %); direction = who COLLECTS
    funding_rate = _f(ac.get("funding"))
    funding_annualized = abs(funding_rate) * 3 * 365 * 100
    funding_data = None
    if funding_annualized > 0:
        funding_data = {"funding_annualized_pct": funding_annualized}
        if funding_rate > 0:
            funding_data["funding_direction"] = "SHORT"
        elif funding_rate < 0:
            funding_data["funding_direction"] = "LONG"
    funding_long = scoring.score_funding(funding_data, "LONG") if funding_data else None
    funding_short = scoring.score_funding(funding_data, "SHORT") if funding_data else None

    oi_data = md.get("oi_velocity")
    oi_present = isinstance(oi_data, dict)
    oi_trend = oi_data.get("oi_trend", "FLAT") if oi_present else "ABSENT"
    oi_score = scoring.score_oi_velocity(oi_data)
    size_score = scoring.score_size(volume_24h)

    effective_min = min_score_fast if (is_fast_track and in_consolidation) else min_score
    rsi_ob, rsi_os = rsi_bands
    ch_15m_val = fast_mom["ch_15m"] if fast_mom else 0.0

    directions = []
    for direction in ("LONG", "SHORT"):
        # regime direction gate
        if regime_val == "crash" and direction == "LONG":
            continue
        if regime_val == "euphoria" and direction == "SHORT":
            continue
        # overextension: don't enter after the move already happened
        if direction == "LONG" and long_blocked:
            continue
        if direction == "SHORT" and short_blocked:
            continue

        # follow-the-trend regime bonus
        regime_bonus = 0.0
        if regime_val == "crash" and direction == "SHORT":
            regime_bonus = 3.0
        elif regime_val == "euphoria" and direction == "LONG":
            regime_bonus = 3.0
        elif regime_val == "crash_bottoming":
            regime_bonus = 3.0 if direction == "LONG" else -2.0
        elif regime_val == "euphoria_topping":
            regime_bonus = 3.0 if direction == "SHORT" else -2.0
        # fade the crowded funding side (squeeze potential)
        if funding_bias == "long_crowded":
            regime_bonus += 4.0 if direction == "SHORT" else -2.0
        elif funding_bias == "short_crowded":
            regime_bonus += 4.0 if direction == "LONG" else -2.0

        # RSI gate (per asset class)
        if rsi is not None:
            if direction == "LONG" and rsi > rsi_ob:
                continue
            if direction == "SHORT" and rsi < rsi_os:
                continue
        # 15m momentum gate — don't short a bouncing coin / long a dropping one
        if direction == "SHORT" and ch_15m_val > FAST_15M_GATE:
            continue
        if direction == "LONG" and ch_15m_val < -FAST_15M_GATE:
            continue
        # 1h momentum gate — require a real 1h move in the trade direction
        if direction == "LONG" and ch_1h < MIN_1H_MOMENTUM:
            continue
        if direction == "SHORT" and ch_1h > -MIN_1H_MOMENTUM:
            continue
        # OI conviction gate — FLAT OI (when present) blocks unless smart money is strong.
        # ABSENT OI (the tool didn't return oi_velocity) is treated as neutral, never a
        # silent universe-wide block.
        if oi_present and oi_trend == "FLAT" and smart < OI_FLAT_SMART_OVERRIDE:
            continue

        cs = candle_score[f"{direction.lower()}_score"]
        ps = price_score[f"{direction.lower()}_score"]
        fs = (funding_long["score"] if funding_long else 0.0) if direction == "LONG" \
            else (funding_short["score"] if funding_short else 0.0)
        sm_score = min(5.0, smart)

        # accel / OI count only in the direction of the move; counter-trend is a warning
        aligned_accel = (direction == "LONG" and ch_1h > 0) or (direction == "SHORT" and ch_1h < 0)
        accel_adj = accel if aligned_accel else -abs(accel)
        aligned_oi = (direction == "LONG" and ch_4h > 0) or (direction == "SHORT" and ch_4h < 0)
        oi_adj = oi_score if aligned_oi else -abs(oi_score)
        fast_adj = fast_mom[f"{direction.lower()}_score"] if fast_mom else 0.0

        # choppy coin (both directions strong) — penalise
        other = candle_score[f"{'short' if direction == 'LONG' else 'long'}_score"]
        conflict = scoring.CONFLICT_PENALTY if (other > 3.0 and cs > 3.0) else 0.0

        total = scoring.combine_score(cs, ps, fs, sm_score, accel_adj, oi_adj,
                                      fast_adj, size_score, regime_bonus, conflict)

        # crash regime lowers the bar for trend-following shorts
        threshold = effective_min - 2 if (direction == "SHORT" and regime_val == "crash") \
            else effective_min
        if total < threshold:
            continue

        fund_pct = 0.0
        if direction == "LONG" and funding_long:
            fund_pct = funding_long.get("annualized_pct", 0.0)
        elif direction == "SHORT" and funding_short:
            fund_pct = funding_short.get("annualized_pct", 0.0)

        directions.append({
            "asset": coin, "direction": direction, "total_score": round(total, 1),
            "cs": cs, "ps": ps, "fs": fs, "sm_score": sm_score,
            "ch_1h": ch_1h, "ch_4h": ch_4h, "fund_pct": fund_pct, "volume_24h": volume_24h,
            "rsi": rsi, "accel_adj": accel_adj, "oi_adj": oi_adj, "oi_trend": oi_trend,
            "size_score": size_score, "market_size": scoring.market_size_label(volume_24h),
            "fast_track": is_fast_track, "ch_15m": (fast_mom["ch_15m"] if fast_mom else None),
            "conviction": scoring.conviction_tier(total),
        })

    if not directions:
        return None
    return max(directions, key=lambda d: d["total_score"])


def _persist(ctx, recent, now, scanned, emitted):
    if ctx.state is None:
        return
    try:
        ctx.state.append({"recent": recent, "scanned_at": now,
                          "scanned": scanned, "emitted": emitted})
    except Exception as exc:  # noqa: BLE001
        print(f"[barracuda.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
