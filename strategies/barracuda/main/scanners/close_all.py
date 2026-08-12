"""BARRACUDA — take-profit circuit breaker.

A tiny second scanner that watches the whole book and, when total unrealized PnL
reaches `profit_pct_close_all` percent of account value, emits a CLOSE for every
open position so the runtime's CLOSE_POSITION action flattens the book and banks the
run. Between those moments the DSL manages each position individually (let winners
run); this only fires on the aggregate profit target.

Read-only + single-pass. On any failure it returns [] — never crashes the tick.
The threshold is an input (no hardcodes); there is no per-call timeout wrapper — the
runtime bounds the tick via the scanner's `timeout_seconds`.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args=args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[barracuda.close_all] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def scan(inputs, ctx):
    profit_target = _f(inputs.get("profit_pct_close_all"), 5.0)

    data = _read(ctx, "strategy_get_clearinghouse_state",
                 {"strategy_wallet": ctx.wallet}, "clearinghouse")
    if not isinstance(data, dict):
        return []

    # total account value across both sub-DEX views (main + xyz) of the one wallet
    account_value = 0.0
    for sec in ("main", "xyz"):
        summary = (data.get(sec, {}) or {}).get("marginSummary", {}) or {}
        account_value += _f(summary.get("accountValue"))
    if account_value <= 0:
        return []

    positions = []
    total_upnl = 0.0
    for sec in ("main", "xyz"):
        for ap in (data.get(sec, {}) or {}).get("assetPositions", []) or []:
            if not isinstance(ap, dict):
                continue
            p = ap.get("position", {}) or {}
            coin = str(p.get("coin", "")).strip()
            szi = _f(p.get("szi"))
            margin_used = _f(p.get("marginUsed"))
            if not coin or abs(szi) < 0.0001 or margin_used <= 0:
                continue
            u_pnl = _f(p.get("unrealizedPnl"))
            total_upnl += u_pnl
            positions.append({
                "coin": coin,
                "roe": (u_pnl / margin_used) * 100.0 if margin_used > 0 else 0.0,
                "u_pnl": u_pnl,
                "direction": "LONG" if szi > 0 else "SHORT",
            })

    if not positions:
        return []

    total_upnl_pct = (total_upnl / account_value) * 100.0
    if total_upnl_pct < profit_target:
        return []

    reason = (f"close_all: book at +{round(total_upnl_pct, 2)}% total unrealized "
              f"(target +{profit_target}%) — banking the run")
    print(f"[barracuda.close_all] {reason}", file=sys.stderr)

    return [{
        "asset": p["coin"],
        "direction": p["direction"],
        "data": {
            "roe": round(p["roe"], 2),
            "u_pnl": round(p["u_pnl"], 4),
            "total_upnl": round(total_upnl, 2),
            "total_upnl_pct": round(total_upnl_pct, 2),
            "profit_target": profit_target,
            "reason": reason,
        },
    } for p in positions]
