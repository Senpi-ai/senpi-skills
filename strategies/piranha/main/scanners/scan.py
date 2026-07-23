"""PIRANHA — supervised scanner (Runtime 3.0 port of the v2 Piranha forced-flow hunter).

Multi-asset, whitelist-gated (BTC/ETH/SOL/HYPE). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - for every non-held, non-recently-signaled candidate: reads market data
    (5m/1h candles + asset_context.openInterest + oi_velocity + L2 order_book),
    resolves OI velocity (prefers the oi_velocity object; self-computes from the
    ctx.state OI cache when null), reads smart-money lean, and scores the pure
    forced-flow thesis via `scoring.build_thesis`,
  - emits the SINGLE highest-scoring candidate at/above `minScore`
    (v2 main() emitted only `best`), sized by a flat margin PERCENT.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (piranha-producer.py v1.0.1):
  - OI-velocity self-compute fallback: v2 persisted the last-seen openInterest per
    asset to state/oi-state.json (read_oi_state/record_oi) and compared on the next
    tick. This port stores the OI cache in ctx.state instead (the runtime's
    transactional store; no file I/O from a read-only scan). SEMANTICS preserved:
    the cache is refreshed for EVERY scanned asset every tick (even when the thesis
    returns None early on insufficient history), so a freshly-started Piranha needs
    one tick per asset to warm the cache before the "computed" fallback can fire.
    FLAGGED: ctx.state is bounded by state_history_max_count and rolls back on a
    failed tick — same effective behaviour as the v2 file cache (a missed tick just
    delays the self-compute), but the OI cache no longer survives a full restart
    that wipes state history. The oi_velocity object (primary source) is unaffected.
  - v2 sized marginUsd = account_value * marginPct (marginPct=0.15, a FRACTION).
    This port emits `marginPct` as a PERCENT in (0,100]; the runtime sizes
    (marginPct/100)*withdrawable. The v2 fraction 0.15 -> 15 (PERCENT). A defensive
    "<=1.0 means a pasted fraction, x100" guard is applied (dire/koala pattern).
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL semantics).
  - DROPPED (read-only scan cannot mutate): none — the v2 producer had no
    order-lifecycle management (no cancel_order / has_resting_orders / stale-order
    purge), so nothing in that family was dropped. The producer NEVER closed
    positions; DSL owns exits, unchanged.
"""

import sys
import time

import scoring

def _sm_row_matches(row, token, target):
    """True if leaderboard row `row` is the market for `target`.

    `leaderboard_get_markets` returns BARE tickers (`NVDA`) plus a separate `dex`
    field, while our universe carries the qualified name (`xyz:NVDA`). A raw
    `token != target` compare therefore NEVER matches an xyz name, so every xyz
    instrument reads as "no smart-money data" and a hard SM gate blocks it
    permanently. Compare bare tickers, and require the dex to agree so a main-DEX
    name cannot cross-match its xyz twin (e.g. main `GOLD` vs `xyz:GOLD`)."""
    tok = str(token or "").upper()
    want = str(target or "").upper()
    if tok.split(":", 1)[-1] != want.split(":", 1)[-1]:
        return False
    row_xyz = (str((row or {}).get("dex", "")).strip().lower() == "xyz"
               or tok.startswith("XYZ:"))
    return row_xyz == want.startswith("XYZ:")



# v2 defaults (piranha-producer.py / piranha-config.json)
_DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_MIN_SCORE = 5
_MAX_LEVERAGE = 5                # v2 MAX_LEVERAGE
_DEFAULT_LEVERAGE = 4           # v2 DEFAULT_LEVERAGE
_DEFAULT_RECENT_TTL = 240       # v2 RECENT_SIGNAL_TTL_SEC (race-window dedup)


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Piranha's universe is crypto majors, so this only ever returns '' in practice."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[piranha.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[piranha.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _asset_data(ctx, coin):
    """Raw market_get_asset_data document for `coin` (5m/1h candles + OI + L2 book)
    or None. READ-GUARDED. Ported from v2 fetch_market_data."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["5m", "1h"],
            "include_funding": False,
            "include_order_book": True,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[piranha.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    return md


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt) or (None, 0.0). READ-GUARDED.

    Verbatim thresholds: long_ratio >= 50 -> LONG (tilt=long_ratio), else SHORT
    (tilt=100-long_ratio); total<=0 -> ('NEUTRAL', 50.0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is an optional +1 confirm; never crash the tick
        print(f"[piranha.scan] leaderboard_get_markets read failed (smart-money -> none): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, coin):
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
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_state(ctx):
    """Latest persisted state record: {signaled:{}, oi:{}, result:{}} or empties."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    oi = last.get("oi", {})
    return (dict(sig) if isinstance(sig, dict) else {},
            dict(oi) if isinstance(oi, dict) else {})


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def _prev_oi(oi_cache, coin):
    """Last-seen openInterest for `coin` from the ctx.state OI cache, or None."""
    entry = oi_cache.get(coin.upper())
    if isinstance(entry, dict):
        return scoring._f(entry.get("oi", 0)) or None
    return None


def scan(inputs, ctx):
    now = time.time()
    universe = [a.upper() for a in inputs.get("universe", _DEFAULT_UNIVERSE)]
    min_score = int(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", 15))   # PERCENT in (0,100]
    # defensive: a pasted FRACTION (e.g. 0.15) means percent — convert x100 (dire/koala guard)
    if margin_pct <= 1.0:
        margin_pct *= 100
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled, oi_cache = _load_state(ctx)
    signaled = _prune_signaled(signaled, ttl, now)

    # ── score every eligible candidate; held + recently-signaled filtered BEFORE
    #    the per-asset MCP fetch, as in v2 main(). The OI cache is refreshed for
    #    EVERY asset that returns market data (even on a None thesis), mirroring
    #    v2's record_oi being called on every build_thesis pass. ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin:
            continue
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin)
        if not md:
            continue
        # refresh OI cache for the self-compute fallback (verbatim: record on every pass)
        cur_oi = scoring.current_oi(md)
        if cur_oi > 0:
            oi_cache[cu] = {"oi": cur_oi, "ts": now}
        sm = _get_sm_direction(ctx, cu)
        th = scoring.build_thesis(coin, md, _prev_oi(oi_cache, coin), sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score})"}
        print(f"[piranha.scan] WAITING — no forced-flow / liquidation-unwind signature "
              f"(min score {min_score}); scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[piranha.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:6]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "oiChangePct": best["oi_change_pct"],
                "oiSource": best["oi_source"],
                "move1hPct": best["move_1h_pct"],
                "move5mPct": best["move_5m_pct"],
                "bidDepth": best["bid_depth"],
                "askDepth": best["ask_depth"],
                "volumeTrendPct": best["volume_trend_pct"],
                "smDirection": best["sm_direction"] or "NONE",
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + OI cache + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.last(). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "oi": oi_cache, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[piranha.scan] WARNING: state append failed; next tick may re-emit a "
                  f"suppressed signal or warm OI cache again: {exc!r}", file=sys.stderr)
    return out
