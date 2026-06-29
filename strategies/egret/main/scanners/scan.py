"""EGRET — supervised scanner (Runtime 3.0 port of the v2 Egret Smart-Money Divergence Fader).

Multi-asset, whitelist-gated (BTC/ETH/SOL/HYPE). A CONTRARIAN FADER: when the
Smart-Money crowd is extremely concentrated one way (>= crowdingMinPct) but price
is NOT confirming over the recent window, the crowded side is exhausted and the
unwind is the edge — Egret fades it (crowded LONG + price stalled -> SHORT;
crowded SHORT + price stalled -> LONG). RSI exhaustion adds conviction. Per tick:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - iterates the whitelist universe (XYZ banned; held + recently-signaled filtered
    BEFORE the per-asset MCP fetch, as in v2 main()),
  - scores each candidate via the pure `scoring.build_thesis` (two hard gates:
    SM crowding + price divergence),
  - emits the SINGLE highest-scoring candidate at/above `minScore`
    (v2 main() emitted only `best`), sized by a FLAT margin percent.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the TIGHT DSL
exit (fader profile — bank the bounded snapback). No daemon, no push_signal, no
create_position.

FIDELITY NOTES vs the v2 producer (egret-producer.py v1.0.1):
  - v2 sized margin as a FLAT FRACTION: marginUsd = account_value * marginPct
    (config marginPct=0.15). This port emits `marginPct` as a PERCENT (15) and the
    runtime sizes (marginPct/100)*withdrawable. The defensive "<=1.0 means a pasted
    fraction, x100" guard converts a config that still stores 0.15. NOT tiered by
    score (unlike bison) — v2 used a single flat marginPct for every emit.
  - v2 emitted exactly one signal (best, highest score). Preserved: scan() emits
    <= 1 signal/tick.
  - v2 leverage = min(config.leverage=4, MAX_LEVERAGE=5) = 4. Clamp preserved.
  - v2 `fetch_sm_direction` returns the CROWDED SIDE + concentration (long_ratio if
    >= 50 else SHORT/100-long_ratio) — DIFFERENT from bison's net-lean 58/42
    thresholds. Ported verbatim from egret's own producer (_get_sm_direction).
  - v2 market fetch was 1h candles only (no funding / no order book). Preserved.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same 240s TTL semantics).
"""

import sys
import time

import scoring


# v2 defaults (egret-producer.py / egret-config.json)
_DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_MIN_SCORE = 5            # v2 DEFAULT_MIN_SCORE / config minScore
_DEFAULT_MARGIN_PCT = 15.0        # v2 config marginPct=0.15 (FRACTION) -> 15 PERCENT
_DEFAULT_LEVERAGE = 4            # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 5               # v2 MAX_LEVERAGE
_DEFAULT_RECENT_TTL = 240        # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Egret's universe is all main-DEX majors, so this only returns '' in practice."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[egret.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        account_value = max(account_value, scoring._num(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = scoring._num(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._num(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._num(_ms.get("totalMarginUsed", 0)), abs(scoring._num(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[egret.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, asset):
    """Port of v2 fetch_sm_direction. Returns (crowded_direction, concentration_pct)
    or (None, 0). READ-GUARDED.

    crowded_direction = the side the smart-money crowd is piled onto;
    concentration_pct = how concentrated (e.g. 78 = 78% of top traders on that side).
    Verbatim from egret-producer.py: long_ratio >= 50 -> ('LONG', long_ratio);
    else -> ('SHORT', 100 - long_ratio). NEUTRAL only when total == 0."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is the GATE-1 input; a read miss degrades to no-fade
        print(f"[egret.scan] leaderboard_get_markets read failed (no SM read -> skip asset): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw or (isinstance(raw, dict) and raw.get("success") is False):
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
        if token != asset:
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._num(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def _asset_1h_candles(ctx, asset):
    """1h candle list for `asset` (no funding / no order book — v2 fetch only 1h).
    READ-GUARDED. Returns [] on any failure."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(asset),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[egret.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return []
    if not md:
        return []
    if isinstance(md, dict) and md.get("success") is False:
        return []
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return []
    candles = d.get("candles", {}) or {}
    return candles.get("1h", []) if isinstance(candles, dict) else []


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    universe = [a.upper() for a in inputs.get("universe", _DEFAULT_UNIVERSE)]
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    # defensive: a config that still stores the v2 FRACTION (0.15) -> x100 (15%).
    if margin_pct <= 1.0:
        margin_pct = margin_pct * 100.0
    lev_default = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    leverage = min(lev_default, _MAX_LEVERAGE)   # v2 min(config.leverage, MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible whitelist candidate (XYZ banned; held + recently
    #    signaled filtered BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin or coin.lower().startswith("xyz:"):   # XYZ not in egret universe
            continue
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        sm = _get_sm_direction(ctx, cu)
        # GATE 1 short-circuit (avoid the candle fetch when the crowd isn't crowded),
        # matching v2 build_thesis order (SM gate before market fetch).
        sm_dir, sm_pct = sm
        crowd_min = float(inputs.get("crowdingMinPct", scoring.DEFAULT_CROWDING_MIN_PCT))
        if sm_dir not in ("LONG", "SHORT") or sm_pct < crowd_min:
            continue
        candles_1h = _asset_1h_candles(ctx, cu)
        if not candles_1h:
            continue
        th = scoring.build_thesis(cu, candles_1h, sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[egret.scan] WAITING — no exhausted-crowd divergence (min score {min_score:.0f}); "
              f"scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[egret.scan] EMIT {best['coin']} {best['direction']} (fade) score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],            # the FADE direction (opposite the crowd)
            "marginPct": margin_pct,                   # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,                      # clamped to <= 5
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "smCrowdDirection": best["sm_crowd_direction"],
                "smCrowdPct": best["sm_crowd_pct"],
                "priceMomentumPct": best["price_momentum_pct"],
                "rsi": best["rsi"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[egret.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
