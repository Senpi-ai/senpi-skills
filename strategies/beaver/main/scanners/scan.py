"""BEAVER — supervised scanner (Runtime 3.0 port of the v2 Beaver BTC trend follower).

Single-asset (BTC). Reads BTC candles (1h/4h) and smart-money positioning
(leaderboard_get_markets); scores via the pure `scoring.build_thesis`; and emits
ONE flat-5x signal when the composite clears `minScore` AND the SM direction gate
agrees with the 4h trend. Read-only + single-pass — emits a `marginPct` intent
plus a fixed `leverage` (5x; v2 had no conviction tiers); the runtime sizes the
dollars, owns the cooldowns/risk gates, and trails the DSL exit. No daemon, no
push_signal.

Onboarding tier: SIMPLER than kodiak — no funding/OI, no BTC-macro driver, no
RSI/time-of-day, no 5m/15m bars. The whole thesis is "is BTC trending on the 4h
AND does smart money agree?"."""

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


_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (4h, anti re-fire)
_FIXED_LEVERAGE = 5           # v2-quirk: Beaver has NO conviction tiers — flat 5x
                              # (producer MAX_LEVERAGE=DEFAULT_LEVERAGE=5).


def _asset_data(ctx, asset, intervals):
    """Read 1h/4h BTC candles. Read-guarded — a transient failure returns None and
    the tick HOLDs (degrade, never crash)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "include_funding": False,
            "include_order_book": False,
            "dex": "",
        })
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[beaver.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _sm_for_asset(ctx, asset):
    """Port of v2 fetch_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns (direction, tilt_pct).
      direction in {"LONG","SHORT","NEUTRAL"} or None when the asset isn't found.
    Read-guarded — a transient failure returns (None, 0.0) so the SM gate blocks
    (no entry without a confirmed SM lean)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — SM gate is hard; a read error -> no entry, never a crash
        print(f"[beaver.scan] leaderboard_get_markets read failed (SM gate -> no entry): {exc!r}", file=sys.stderr)
        return None, 0.0
    if not raw:
        return None, 0.0

    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

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
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    if long_ratio >= 50:                      # v2-quirk: ties (50/50) resolve LONG
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def scan(inputs, ctx):
    asset = (inputs.get("asset", "BTC") or "BTC").upper()
    min_score = float(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 25))   # PERCENT of withdrawable (0,100], not a fraction
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    last = recent.get(asset)
    if last is not None and (now - last) < ttl:
        return []

    data = _asset_data(ctx, asset, ["1h", "4h"])
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    c1h = candles.get("1h", []) or []
    c4h = candles.get("4h", []) or []

    sm_dir, sm_tilt = _sm_for_asset(ctx, asset)

    th = scoring.build_thesis(c1h, c4h, sm_dir, sm_tilt, inputs)

    # ── per-tick result record → scan-results history (ctx.state, bounded by
    #    state_history_max_count). Read back with ctx.state.recent(n); persists to state.json. ──
    out = []
    if not th:
        t4, s4 = scoring.trend_structure(c4h)
        t1, _ = scoring.trend_structure(c1h)
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked", "score": None,
                  "direction": None, "trend4h": t4, "ts4h": round(s4, 3), "trend1h": t1,
                  "smDir": sm_dir, "smTilt": round(scoring._f(sm_tilt), 1)}
        print(f"[beaver.scan] {asset} HOLD (gate): 4h={t4} {s4:.0%} | 1h={t1} | "
              f"SM={sm_dir} {scoring._f(sm_tilt):.0f}% (need 4h trend + SM agreement + tilt)",
              file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_4h_strength"],
                  "trend1h": th["trend_1h"], "smDir": th["sm_direction"], "smTilt": th["sm_tilt_pct"],
                  "reasons": th["reasons"]}
        print(f"[beaver.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_4h_strength']:.0%} SM={th['sm_tilt_pct']:.0f}% | {th['reasons']}",
              file=sys.stderr)
    else:
        leverage = _FIXED_LEVERAGE             # flat 5x — no conviction tiers in v2 Beaver
        recent[asset] = now
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "leverage": leverage, "trend4h": th["trend_4h"],
                  "ts4h": th["trend_4h_strength"], "trend1h": th["trend_1h"], "smDir": th["sm_direction"],
                  "smTilt": th["sm_tilt_pct"], "reasons": th["reasons"]}
        print(f"[beaver.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x | {th['reasons']}",
              file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # flat 5x; runtime applies it
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "reasons": th["reasons"],
                "trend4h": th["trend_4h"], "trend4hStrength": th["trend_4h_strength"],
                "trend1h": th["trend_1h"], "smDirection": th["sm_direction"],
                "smTiltPct": th["sm_tilt_pct"], "volumeTrendPct": th["volume_trend_pct"],
            },
        }]

    # ── persist dedup map + this tick's result EVERY tick; self-trims at
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[beaver.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
