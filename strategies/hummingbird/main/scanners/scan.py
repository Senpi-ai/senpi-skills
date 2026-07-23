"""HUMMINGBIRD — supervised scanner (Runtime 3.0 port of the v2 Hummingbird
HYPE trend follower, SKILL.md v1.0.0).

Single-asset (HYPE). Reads HYPE candles (1h/4h) and smart-money positioning
(leaderboard_get_markets); scores via the pure `scoring.build_thesis`; and emits
ONE signal when the composite clears `minScore`. Read-only + single-pass — emits
a `marginPct` intent plus a flat per-signal `leverage` (5x; v2 was NOT tiered);
the runtime sizes the dollars, owns the cooldowns/risk gates, and trails the DSL
exit. No daemon, no push_signal.

Direction gate (all required, ported verbatim from v2):
  1. 4h trend != NEUTRAL
  2. Smart-Money direction in {LONG, SHORT} AND tilt >= smTiltMinPct (60)
  3. SM direction agrees with the 4h trend
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


_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (anti re-fire)
_DEFAULT_LEVERAGE = 5         # v2: DEFAULT_LEVERAGE == MAX_LEVERAGE == 5 (NOT tiered)
_MAX_LEVERAGE = 5             # v2 hardcoded cap — operator capital risk (SKILL.md RULE 5)


def _asset_data(ctx, asset, intervals):
    """READ-GUARD: a read error must not roll back the whole tick (degrade -> None)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "include_funding": False,
            "include_order_book": False,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[hummingbird.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def _sm_for_asset(ctx, asset):
    """Port of v2 fetch_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns {direction, tilt} or None.

    READ-GUARD: smart-money is on the hard gate, but a READ failure must degrade
    to None (-> thesis returns None -> HOLD) rather than crash the tick."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001
        print(f"[hummingbird.scan] leaderboard_get_markets read failed (smart-money -> None): {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    # v2-quirk: tolerate {success}, {data:{markets|leaderboard}}, or a bare list.
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    markets = data
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    want = asset.upper()
    long_pct = short_pct = 0.0
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
    sm_dir, sm_tilt = scoring.sm_split(long_pct, short_pct)
    return {"direction": sm_dir, "tilt": sm_tilt}


def scan(inputs, ctx):
    asset = (inputs.get("asset", "HYPE") or "HYPE").upper()
    min_score = float(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 25))   # PERCENT of withdrawable (0,100], not a fraction
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    # signal-dedup (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []

    data = _asset_data(ctx, asset, ["1h", "4h"])
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    c1h = candles.get("1h", []) or []
    c4h = candles.get("4h", []) or []

    sm = _sm_for_asset(ctx, asset)

    th = scoring.build_thesis(asset, c1h, c4h, sm, inputs)

    # ── per-tick result record → scan-results history (ctx.state, bounded by
    #    state_history_max_count). Read back with ctx.state.recent(n). ──
    out = []
    if not th:
        t4, s4 = scoring.trend_structure(c4h)
        t1, _ = scoring.trend_structure(c1h)
        sm_dir = (sm or {}).get("direction")
        sm_tilt = scoring._f((sm or {}).get("tilt", 0.0))
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "blocked", "score": None,
                  "direction": None, "trend4h": t4, "ts4h": round(s4, 3), "trend1h": t1,
                  "smDirection": sm_dir, "smTiltPct": round(sm_tilt, 2)}
        print(f"[hummingbird.scan] {asset} HOLD (gate): 4h={t4} {s4:.0%} | 1h={t1} | "
              f"sm={sm_dir} {sm_tilt:.0f}%", file=sys.stderr)
    elif th["score"] < min_score:
        result = {"ts": now, "asset": asset, "emitted": False, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "trend4h": th["trend_4h"], "ts4h": th["trend_4h_strength"],
                  "trend1h": th["trend_1h"], "smDirection": th["sm_direction"],
                  "smTiltPct": th["sm_tilt_pct"], "reasons": th["reasons"]}
        print(f"[hummingbird.scan] {asset} HOLD: score={th['score']}/{min_score:.0f} {th['direction']} | "
              f"4h={th['trend_4h']} {th['trend_4h_strength']:.0%} sm={th['sm_tilt_pct']:.0f}% | {th['reasons']}",
              file=sys.stderr)
    else:
        recent[au] = now
        result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
                  "direction": th["direction"], "leverage": leverage, "trend4h": th["trend_4h"],
                  "ts4h": th["trend_4h_strength"], "trend1h": th["trend_1h"],
                  "smDirection": th["sm_direction"], "smTiltPct": th["sm_tilt_pct"], "reasons": th["reasons"]}
        print(f"[hummingbird.scan] {asset} EMIT: score={th['score']} {th['direction']} {leverage}x | {th['reasons']}",
              file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # flat 5x (v2 was NOT conviction-tiered); runtime applies it
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "reasons": th["reasons"],
                "trend4h": th["trend_4h"], "trend4hStrength": th["trend_4h_strength"], "trend1h": th["trend_1h"],
                "smDirection": th["sm_direction"], "smTiltPct": th["sm_tilt_pct"],
                "volumeTrendPct": th["volume_trend_pct"],
            },
        }]

    # ── persist dedup map + this tick's result EVERY tick; self-trims at
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[hummingbird.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
