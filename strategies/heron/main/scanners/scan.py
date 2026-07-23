"""HERON — supervised scanner (Runtime 3.0 port of the v2 Heron ETH trend follower).

Single-asset, onboarding tier. Reads ETH candles (1h + 4h) and smart-money
positioning (leaderboard_get_markets); scores via the pure `scoring.build_thesis`;
and emits ONE signal when the 4h trend + SM-direction gate align and the score
clears `minScore`. Read-only + single-pass — emits a `marginPct` intent plus a
FIXED `leverage` of 5 (heron does NOT tier by conviction); the runtime sizes the
dollars, owns the cooldowns/risk gates, and trails the DSL exit. No daemon, no
push_signal.

Deliberately simpler than kodiak/polar: only 1h+4h candles, no funding/OI/RSI/BTC,
no time-of-day, no leverage tiers."""

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


_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (anti re-fire)
_DEFAULT_LEVERAGE = 5         # v2 DEFAULT_LEVERAGE / MAX_LEVERAGE — heron is FIXED 5x


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _asset_data(ctx, asset, dex, intervals):
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "include_funding": False,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[heron.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _sm_for_asset(ctx, asset):
    """Port of v2 fetch_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns {direction, tilt} or None. tilt is the
    percent long/short concentration (e.g. 70 = '70% of top traders long').

    # v2-quirk: NO neutral band — long_ratio >= 50 -> LONG (tilt=long_ratio),
    # else SHORT (tilt=100-long_ratio). total<=0 -> NEUTRAL/50. Reproduced verbatim."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — SM is the directional gate; on read failure -> None (thesis blocks safely)
        print(f"[heron.scan] leaderboard_get_markets read failed (smart-money -> None): {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    want = asset.upper()
    long_pct = 0.0
    short_pct = 0.0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, want):
            continue
        found = True
        direction = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if direction == "LONG":
            long_pct = pct
        elif direction == "SHORT":
            short_pct = pct

    if not found:
        return None
    total = long_pct + short_pct
    if total <= 0:
        return {"direction": "NEUTRAL", "tilt": 50.0}
    long_ratio = (long_pct / total) * 100
    if long_ratio >= 50:
        return {"direction": "LONG", "tilt": long_ratio}
    return {"direction": "SHORT", "tilt": 100 - long_ratio}


def scan(inputs, ctx):
    asset = (inputs.get("asset", "ETH") or "ETH")
    dex = _dex_for(asset, inputs)
    min_score = float(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 25))     # PERCENT of withdrawable (0,100], not a fraction
    leverage = int(inputs.get("leverage", _DEFAULT_LEVERAGE))  # FIXED — heron does not tier
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []

    data = _asset_data(ctx, asset, dex, ["1h", "4h"])
    if not data:
        return []
    candles = data.get("candles", {}) or {}

    sm = _sm_for_asset(ctx, asset)

    th = scoring.build_thesis(candles.get("1h", []), candles.get("4h", []), sm, inputs)

    # ── per-tick result record → scan-results history (ctx.state, bounded by
    #    state_history_max_count). Read back with ctx.state.recent(n); persists to state.json. ──
    out = []
    if not th:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked",
                  "score": None, "direction": None,
                  "smDir": (sm or {}).get("direction"), "smTilt": (sm or {}).get("tilt")}
        print(f"[heron.scan] {asset} HOLD (gate): sm={(sm or {}).get('direction')} "
              f"tilt={(sm or {}).get('tilt')} — 4h trend + SM gate not aligned", file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_4h_strength"],
                  "trend1h": th["trend_1h"], "smTilt": th["sm_tilt_pct"], "reasons": th["reasons"]}
        print(f"[heron.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_4h_strength']:.0%} smTilt={th['sm_tilt_pct']:.0f}% | {th['reasons']}",
              file=sys.stderr)
    else:
        recent[au] = now
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "leverage": leverage, "trend4h": th["trend_4h"],
                  "ts4h": th["trend_4h_strength"], "trend1h": th["trend_1h"], "smTilt": th["sm_tilt_pct"],
                  "reasons": th["reasons"]}
        print(f"[heron.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x | {th['reasons']}",
              file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # FIXED 5x; runtime applies it
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "trend4h": th["trend_4h"], "trend4hStrength": th["trend_4h_strength"], "trend1h": th["trend_1h"],
                "smDirection": th["sm_direction"], "smTiltPct": th["sm_tilt_pct"],
                "volumeTrendPct": th["volume_trend_pct"],
                "reasons": th["reasons"],
            },
        }]

    # ── persist dedup map + this tick's result EVERY tick; self-trims at
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[heron.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
