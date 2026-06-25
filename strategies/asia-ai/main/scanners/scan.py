"""ASIA-AI — supervised scanner (shared verbatim by both instances).

Direction-parametrized: the `main` instance passes direction=LONG / wantTrend=UP
(long the Asian AI semis); the `hedge` instance passes direction=SHORT /
wantTrend=DOWN (short the broad/US-AI complex when it rolls over, to strip market
beta). Read-only, pure, single-pass — emits a `marginPct` intent; the runtime sizes."""

import sys
import time

import scoring

_DEFAULT_TTL = 1800   # 30m: don't re-fire the same name while a signal is in flight


def scan(inputs, ctx):
    universe = inputs.get("universe", [])
    direction = (inputs.get("direction", "LONG") or "LONG").upper()
    want = (inputs.get("wantTrend", "UP") or "UP").upper()
    min_score = int(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 0.12))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    out = []
    for asset in universe:
        au = asset.upper()
        last = recent.get(au)
        if last is not None and (now - last) < ttl:        # signal-dedup
            continue
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["4h", "1d"],
            "dex": "xyz" if asset.lower().startswith("xyz:") else "",
        })
        if not md:
            continue
        c = (md.get("data", md) or {}).get("candles", {}) if isinstance(md, dict) else {}
        th = scoring.confirm_trend(c.get("4h", []), c.get("1d", []), want, inputs)
        if not th or th["score"] < min_score:
            continue
        out.append({
            "asset": asset,
            "direction": direction,
            "marginPct": margin_pct,          # SIZING INTENT — the runtime sizes the dollars
            "data": {"score": th["score"], "direction": direction, "reasons": th["reasons"]},
        })
        recent[au] = now

    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent})
        except Exception as exc:  # noqa: BLE001
            print(f"[asia-ai.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
