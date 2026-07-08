#!/usr/bin/env python3
"""Are my positions protected? — the DSL-coverage doctor (PROTECTED / NAKED / STOP-NOT-ON-VENUE).

  python3 protect.py <id>        # every wallet of the deployed package
  python3 protect.py <id> --json

Answers the one question a DSL state file CANNOT: is each on-chain position actually covered by a live
stop? It reconciles THREE sources and treats the **chain** as the source of truth:

  1. open positions  — MCP strategy_get_clearinghouse_state (main + xyz)   ← what is ACTUALLY open
  2. DSL-tracked     — openclaw senpi dsl positions -a <wallet> --json      ← what the engine protects
  3. resting stops   — MCP strategy_get_open_orders                         ← what is on the venue

THE TRAP THIS EXISTS TO KILL: a DSL state file going `active: false` (archived) means the engine STOPPED
TRACKING — which happens on EVERY breach — NOT that the position closed. So "archived" is equally
consistent with "closed cleanly" and "breach fired, the close order never filled, position still open and
now untracked = NAKED." You can only tell them apart by the on-chain position. NEVER conclude "closed" or
"protected" from a DSL file's `active` flag. This tool reads the clearinghouse and refuses to call a
position protected unless the chain agrees.

Verdict per OPEN on-chain position:
  ✅ PROTECTED          in the DSL-tracked set AND a resting stop is on the venue
  ⚠ STOP-NOT-ON-VENUE  tracked, but no reduce-only/stop order is actually resting (stop never posted)
  ❌ NAKED             open on-chain but NOT tracked (the archived-but-still-open case) — re-protect NOW
This tool only READS. It never closes or arms anything — it tells you what to fix.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
from mcp_client import MCPClient, MCPError  # noqa: E402

_ICON = {"PROTECTED": "✅", "STOP-NOT-ON-VENUE": "⚠", "NAKED": "❌",
         "CLOSED-OR-STALE": "·", "UNKNOWN": "·"}


# ---------------------------------------------------------------- pure reconciliation (unit-tested)

def reconcile(open_assets, tracked_assets, stop_assets):
    """The core verdict logic — pure, so it is testable without a host. Inputs are three sets/dicts of
    asset symbols; the CHAIN (`open_assets`) drives every verdict. Returns list[(asset, verdict)]."""
    verdicts = []
    for asset in sorted(open_assets):
        tracked = asset in tracked_assets
        has_stop = asset in stop_assets
        if tracked and has_stop:
            verdicts.append((asset, "PROTECTED"))
        elif tracked and not has_stop:
            verdicts.append((asset, "STOP-NOT-ON-VENUE"))
        else:
            verdicts.append((asset, "NAKED"))  # open on-chain but the engine isn't tracking it
    for asset in sorted(tracked_assets):
        if asset not in open_assets:
            verdicts.append((asset, "CLOSED-OR-STALE"))  # engine tracks a position the chain says is gone
    return verdicts


# ---------------------------------------------------------------- source reads (chain = truth)

def _open_assets(mcp, wallet):
    """Assets with a non-zero position on-chain (main + xyz). Returns {asset: szi} or None if unreadable
    (None ⇒ we must NOT claim anything is protected — surface UNKNOWN)."""
    try:
        ch = mcp.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet, timeout=20)
    except MCPError:
        return None
    out = {}
    for dex in ("main", "xyz"):
        sub = _cli.dig(ch or {}, dex, default={}) or {}
        positions = _cli.dig(sub, "assetPositions", "positions", default=[]) or []
        for p in positions:
            pos = _cli.dig(p, "position", default=p) if isinstance(p, dict) else {}
            asset = _cli.dig(pos, "coin", "asset", "symbol")
            szi = _cli.dig(pos, "szi", "size", "sz")
            try:
                if asset and float(szi) != 0.0:
                    out[str(asset)] = float(szi)
            except (TypeError, ValueError):
                continue
    return out


def _tracked_assets(wallet):
    """Assets the DSL engine is ACTIVELY tracking (from the live RPC, not a state file)."""
    js = _cli.cli_json(["openclaw", "senpi", "dsl", "positions", "-a", wallet, "--json"], timeout=20)
    return _assets_from(js, extra=("floorPrice", "phase", "currentROE"))


def _is_stop_order(o):
    """True iff order `o` is a protective DOWNSIDE stop — NOT a take-profit and not a plain reduce-only
    scale-out. A take-profit is ALSO reduceOnly (and isPositionTpsl), so `reduceOnly` alone must not count
    as a stop: a TP-only position would then read PROTECTED with no real stop behind it. Exclude explicit
    take-profits FIRST, then accept reduce-only / trigger / stop-typed orders."""
    if not isinstance(o, dict):
        return False
    otype = str(_cli.dig(o, "orderType", "type") or "").lower()
    tpsl = str(_cli.dig(o, "tpsl", "tpSl", "triggerType") or "").lower()
    if "take" in otype or "profit" in otype or tpsl == "tp":   # a take-profit is not downside protection
        return False
    return bool(_cli.dig(o, "reduceOnly", "isPositionTpsl", "isTrigger")) or "stop" in otype or \
        otype in ("trigger", "stop", "stop_market", "stop_limit") or tpsl == "sl"


def _stop_assets(mcp, wallet):
    """Assets with a resting protective STOP order on the venue (take-profits excluded — see _is_stop_order)."""
    try:
        oo = mcp.mcp_call("strategy_get_open_orders", strategy_wallet=wallet, timeout=20)
    except MCPError:
        return set()
    out = set()
    for o in _flatten_orders(oo):
        asset = _cli.dig(o, "coin", "asset", "symbol") if isinstance(o, dict) else None
        if asset and _is_stop_order(o):
            out.add(str(asset))
    return out


def _assets_from(js, extra=()):  # tolerant: pull asset symbols out of a list/dict payload
    out = set()

    def walk(o):
        if isinstance(o, dict):
            a = _cli.dig(o, "asset", "coin", "symbol")
            if a and (not extra or any(k in o for k in extra)):
                out.add(str(a))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(js)
    return out


def _flatten_orders(oo):
    if isinstance(oo, list):
        return oo
    if isinstance(oo, dict):
        for k in ("orders", "openOrders", "data", "main", "xyz"):
            v = oo.get(k)
            if isinstance(v, list):
                return v
        # {main:{orders:[]}, xyz:{orders:[]}}
        acc = []
        for v in oo.values():
            if isinstance(v, dict):
                acc += _flatten_orders(v)
            elif isinstance(v, list):
                acc += v
        return acc
    return []


# ---------------------------------------------------------------- driver

def check_wallet(mcp, wallet, host_ok):
    open_map = _open_assets(mcp, wallet)
    if open_map is None:
        return {"wallet": wallet, "verdicts": [], "error": "clearinghouse unreadable — cannot verify (do NOT assume protected)"}
    tracked = _tracked_assets(wallet) if host_ok else set()
    stops = _stop_assets(mcp, wallet)
    verds = reconcile(set(open_map), tracked, stops)
    # host-blind caveat: without openclaw we can't read the tracked set, so an open position can't be
    # confirmed PROTECTED — downgrade any PROTECTED/NAKED to UNKNOWN and say why.
    if not host_ok:
        verds = [(a, "UNKNOWN" if v in ("PROTECTED", "NAKED", "STOP-NOT-ON-VENUE") else v) for a, v in verds]
    return {"wallet": wallet, "open": open_map, "tracked": sorted(tracked), "stops": sorted(stops),
            "verdicts": [{"asset": a, "verdict": v} for a, v in verds],
            "host_ok": host_ok}


def main(argv):
    ap = argparse.ArgumentParser(description="Verify every open position of a deployed strategy is DSL-protected.")
    ap.add_argument("package", help="Strategy id (e.g. divergence-play).")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    mcp = MCPClient()
    host_ok = _cli.run_cli(["openclaw", "--version"], timeout=15)[0] == 0
    strategies = [s for s in _cli.strategies_for(mcp, skill_name=a.package) if _cli.strategy_open(s)]
    wallets = sorted({str(_cli.strategy_wallet(s)) for s in strategies if _cli.strategy_wallet(s)})

    rows = [check_wallet(mcp, w, host_ok) for w in wallets]

    if a.json:
        print(json.dumps({"strategy": a.package, "openclaw_available": host_ok, "wallets": rows}, indent=2))
        return 0

    if not wallets:
        print(f"No open strategies found for {a.package!r}.")
        return 0
    print(f"\n{a.package} — DSL protection" + ("" if host_ok else "  (no openclaw here — tracked set unknown; run on the runtime host)"))
    naked = 0
    for r in rows:
        print(f"\n  wallet {r['wallet'][:12]}…")
        if r.get("error"):
            print(f"    · {r['error']}")
            continue
        if not r["verdicts"]:
            print("    (no open positions)")
            continue
        for v in r["verdicts"]:
            icon = _ICON.get(v["verdict"], "·")
            print(f"    {icon} {v['asset']:<12} {v['verdict']}")
            if v["verdict"] == "NAKED":
                naked += 1
    if naked:
        print(f"\n  ❌ {naked} NAKED position(s) — open on-chain, not DSL-tracked, no stop. Re-protect NOW: "
              f"`ratchet_stop_add` a stop, or `close.py {a.package}` the leg. Do not leave it running.")
    elif host_ok and any(r.get("verdicts") for r in rows):
        print("\n  ✅ every open position is tracked with a resting stop." if all(
            v["verdict"] == "PROTECTED" for r in rows for v in r["verdicts"])
            else "\n  ⚠ see STOP-NOT-ON-VENUE above — tracked but the stop isn't resting on the venue.")
    return 2 if naked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
