"""HYDRA DIP — supervised scanner (Runtime 3.0 port of v2 hydra-producer.py single_main,
LEG=dip). Single-asset, LONG-only. Reads the thesis coin's 1h/4h/1d candles + funding,
scores via the pure `scoring.score_dip`, and emits ONE signal when a pullback inside a
confirmed 1d+4h uptrend clears minScore. Read-only + single-pass — emits a `marginPct`
intent plus a per-signal `leverage`; the runtime sizes the dollars, owns the cooldowns/
risk gates, and trails the DSL exit. No daemon.

PER-COIN PARAMETERIZATION: the thesis coin is inputs.coin (mirrors v2 HYDRA_COIN env).
"""

import sys
import time

import scoring

_DEFAULT_TTL = 240


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None and dex != "":
        return dex
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _read(ctx, name, args):
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[hydra-dip.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _account_value(ctx):
    """max(main, xyz) account value — two views of ONE cross-margined wallet (v2
    get_positions: never sum). Returns (account_value, held_assets, deployed_margin)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    account_value, held, deployed = 0.0, [], 0.0
    use = 0.0
    has_pos = False
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s.get("marginSummary"), dict) else {}
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        use = max(use, float(ms.get("totalMarginUsed", 0) or 0),
                  abs(float(ms.get("totalNtlPos", 0) or 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            has_pos = True
            held.append(pos.get("coin", ""))
            deployed += float(pos.get("marginUsed", 0) or 0)
    if use > 1.0 and not has_pos:
        return 0.0, [], 0.0
    return account_value, [h for h in held if h], deployed


def scan(inputs, ctx):
    coin = str(inputs.get("coin", "ETH"))
    dex = _dex_for(coin, inputs)
    min_score = float(inputs.get("minScore", 4))
    margin_pct = float(inputs.get("marginPct", 18))     # PERCENT of withdrawable (0,100]
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    now = time.time()

    cu = coin.upper()
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    def _persist(extra=None):
        if ctx.state is None:
            return
        rec = {"recent": recent}
        if extra:
            rec.update(extra)
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[hydra-dip.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    last = recent.get(cu)
    if last is not None and (now - last) < ttl:
        _persist()
        return []

    account_value, held, deployed = _account_value(ctx)
    if account_value <= 0:
        _persist({"result": {"ts": now, "coin": coin, "emitted": False, "note": "no account value"}})
        return []
    if cu in {h.upper() for h in held}:
        _persist({"result": {"ts": now, "coin": coin, "emitted": False, "note": "position open — holding"}})
        return []

    md = _read(ctx, "market_get_asset_data", {
        "asset": coin, "candle_intervals": ["1h", "4h", "1d"],
        "dex": dex, "include_funding": True, "include_order_book": False,
    })
    if not md:
        _persist({"result": {"ts": now, "coin": coin, "emitted": False, "note": "no market data"}})
        return []
    d = md.get("data", md) if isinstance(md, dict) else {}
    candles = d.get("candles", {}) or {}

    th = scoring.score_dip(candles.get("1h", []), candles.get("4h", []), candles.get("1d", []), inputs)
    if not th or th["score"] < min_score:
        result = {"ts": now, "coin": coin, "emitted": False, "gate": "blocked",
                  "score": (th["score"] if th else None), "note": "no pullback in an uptrend"}
        print(f"[hydra-dip.scan] {coin} HOLD: {result}", file=sys.stderr)
        _persist({"result": result})
        return []

    margin_usd = round(account_value * (margin_pct / 100.0), 2)
    free_margin = max(0.0, account_value - deployed)
    min_notional = max(account_value * min_notional_pct, venue_min_notional)
    notional = margin_usd * th["leverage"]
    if not (margin_usd > 0 and notional >= min_notional and margin_usd * 1.1 <= free_margin):
        result = {"ts": now, "coin": coin, "emitted": False, "gate": "sizing",
                  "score": th["score"], "note": "below notional floor or insufficient free margin"}
        print(f"[hydra-dip.scan] {coin} HOLD (sizing): {result}", file=sys.stderr)
        _persist({"result": result})
        return []

    recent[cu] = now
    result = {"ts": now, "coin": coin, "emitted": True, "gate": "pass", "score": th["score"],
              "direction": th["direction"], "leverage": th["leverage"], "trend4h": th["trend4h"],
              "reasons": th["reasons"]}
    print(f"[hydra-dip.scan] {coin} EMIT: score={th['score']} {th['direction']} {th['leverage']}x | {th['reasons']}",
          file=sys.stderr)
    out = [{
        "asset": coin,
        "direction": th["direction"],       # always LONG (dip-buyer)
        "marginPct": margin_pct,            # SIZING INTENT — runtime sizes the dollars
        "leverage": th["leverage"],
        "data": {
            "score": th["score"], "leverage": th["leverage"], "direction": th["direction"],
            "trend4h": th["trend4h"], "trend1h": th["trend1h"], "rsi": round(th.get("rsi", 0), 1),
            "reasons": th["reasons"],
        },
    }]
    _persist({"result": result})
    return out
