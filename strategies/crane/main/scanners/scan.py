"""CRANE — Managed Pairs / Stat-Arb. The single supervised scanner.

⚠️ NOT DEPLOYABLE AS-IS — BLOCKED ON A RUNTIME CAPABILITY (see crane/DESIGN.md).
A managed pair must close BOTH legs together (on reversion, on a blowout stop, or
the instant one leg goes missing). The Runtime 3.0 scan contract emits OPEN-only
signals and blocks close mutations; CLOSE_POSITION has no scanner-signal path. So
crane cannot flatten a pair, and a DSL-only pair would leave a naked directional
leg the moment one side's stop fires. Until coordinated multi-leg close exists,
this scanner is DISARMED: `scan` returns [] unless `inputs.armed` is true, and it
logs every close it WOULD issue so the gap is observable. The alpha engine
(scoring.py) is complete and unit-tested, ready to wire when the capability lands.

Each tick: update each configured pair's rolling log-spread in ctx.state, z-score
it, and run the pair state machine. Read-only, single-pass, no daemon.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[crane.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _prices(ctx, assets):
    """{ASSET_UPPER: price} for the requested assets (tolerant)."""
    d = _read(ctx, "market_get_prices", {"assets": assets}, "market_get_prices")
    out = {}
    rows = d if isinstance(d, list) else (d.get("prices", d) if isinstance(d, dict) else [])
    if isinstance(rows, dict):
        for k, v in rows.items():
            p = scoring._num(v if not isinstance(v, dict) else v.get("price", v.get("markPx")))
            if p is not None:
                out[str(k).split(":", 1)[-1].upper()] = p
    elif isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict):
                name = r.get("asset") or r.get("coin") or r.get("name")
                p = scoring._num(r.get("price", r.get("markPx", r.get("mid"))))
                if name and p is not None:
                    out[str(name).split(":", 1)[-1].upper()] = p
    return out


def _held_map(ctx):
    """{ASSET_UPPER: 'LONG'|'SHORT'} for open positions, or None on read failure."""
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return None
    out = {}
    for e in d.get("assetPositions", d.get("asset_positions", [])) or []:
        pos = e.get("position", e) if isinstance(e, dict) else {}
        coin = str(pos.get("coin", "")).strip()
        szi = scoring._f(pos.get("szi"))
        if coin and szi != 0:
            out[coin.split(":", 1)[-1].upper()] = "LONG" if szi > 0 else "SHORT"
    return out


def scan(inputs, ctx):
    now = time.time()
    armed = bool(inputs.get("armed", False))
    window = int(scoring._f(inputs.get("zWindow"), 60))
    pairs = [{"a": str(p[0]).upper(), "b": str(p[1]).upper()}
             for p in (inputs.get("pairs") or []) if isinstance(p, (list, tuple)) and len(p) == 2]
    if not pairs:
        print("[crane.scan] no pairs configured — nothing to do", file=sys.stderr)
        return []

    st = (ctx.state.last() or {}) if ctx.state else {}
    spreads = dict(st.get("spreads", {}) or {})     # {pairKey: [history]}

    legs = sorted({p["a"] for p in pairs} | {p["b"] for p in pairs})
    prices = _prices(ctx, legs)
    held = _held_map(ctx)
    if held is None:
        print("[crane.scan] clearinghouse unreadable — act next tick", file=sys.stderr)
        return []

    out, close_needed, suppressed_open = [], [], False
    for p in pairs:
        key = f"{p['a']}/{p['b']}"
        spread = scoring.log_spread(prices.get(p["a"]), prices.get(p["b"]))
        spreads[key] = scoring.push_spread(spreads.get(key, []), spread, window)
        z = scoring.zscore(spreads[key], window)
        a_held, b_held = p["a"] in held, p["b"] in held
        action, reason = scoring.decide_pair_action(z, a_held, b_held, inputs)
        print(f"[crane.scan] {key}: z={'NA' if z is None else round(z,2)} held=({a_held},{b_held}) "
              f"→ {action} ({reason})", file=sys.stderr)

        if action in (scoring.CLOSE_BOTH, scoring.CLOSE_NAKED):
            # Cannot execute a close from a scanner in the current runtime — record the gap.
            close_needed.append((key, action, reason))
            print(f"[crane.scan] ⚠️ {action} REQUIRED for {key} but the runtime exposes no "
                  f"scanner-driven close — see crane/DESIGN.md", file=sys.stderr)
        elif action == scoring.OPEN_BOTH and not a_held and not b_held:
            if not armed:
                suppressed_open = True
                continue
            lev, mgn = scoring.leg_sizing(z, inputs)
            for leg in scoring.entry_legs(z, p):
                out.append({
                    "asset": leg["asset"], "direction": leg["direction"],
                    "marginPct": mgn, "leverage": lev,
                    "data": {"pair": key, "z": round(z, 3), "leverage": lev,
                             "direction": leg["direction"], "reasons": [reason]},
                })
            print(f"[crane.scan] OPEN pair {key}: {lev}x {mgn}%/leg | {reason}", file=sys.stderr)

    if suppressed_open:
        print("[crane.scan] DISARMED (inputs.armed=false) — opens suppressed; arm only once "
              "coordinated multi-leg close exists (crane/DESIGN.md)", file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"spreads": spreads,
                              "result": {"ts": now, "opened": len(out),
                                         "close_needed": [c[0] for c in close_needed]}})
        except Exception as exc:  # noqa: BLE001
            print(f"[crane.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
