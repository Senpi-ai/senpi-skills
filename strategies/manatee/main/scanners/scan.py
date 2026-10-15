"""MANATEE — Large-cap Mean-Reversion Inventory. The single supervised scanner.

Once per closed DAILY bar, for an identity basket of large caps: a name whose daily close is at
least devPct below its one-week mean is a LONG, one at least devPct above it is a SHORT. No bias:
the sign of the stretch is the direction. A held name gets one more clip (an add) when the next
daily close is a further addStepPct against the position AND the position is under water, up to
maxAddsPerAsset; the runtime merges the add into the open position and the DSL re-anchors on the
new average entry (it archives the old state as position_increased and tracks the merged position
from scratch), which is the modelled vault's "harvest over average cost". NEVER closes: the DSL
owns every exit, every hour of the day. Read-only, single-pass. marginPct is a PERCENT in (0,100]
of withdrawable.

THE DAILY CLOCK: the scanner ticks every few minutes but acts on a name at most once per closed
daily bar (state `acted[name] = that bar's open time`), so the first tick after 00:00 UTC does the
day's work and the rest of the day is quiet. The in-progress bar (the MCP's last row) is dropped.

FREE-MARGIN GATE: every signal in one tick is sized off the same balance read, so the scanner reads
free margin (withdrawable) once, prices each clip as the runtime will ((marginPct/100) x withdrawable
x leverage), skips the tick when a clip is under the venue minimum notional, and stops emitting when
the clips it has already emitted would exhaust free margin.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[manatee.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _bare(name):
    return str(name).split(":", 1)[-1].upper()


def _account(ctx):
    """(held, free_margin): held = bare-upper name -> (side, average entry) for every open position;
    free_margin = withdrawable USD. The 'main' and 'xyz' sections are two views of one cross-margined
    wallet: account value and withdrawable are taken ONCE via max(), never summed. None if the state
    is unreadable or contradicts itself (margin in use but no positions): the tick stands down."""
    data = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet}, "clearinghouse")
    if not isinstance(data, dict):
        return None
    held, account_value, used, withdrawable = {}, 0.0, 0.0, 0.0
    for view in [v for v in data.values() if isinstance(v, dict)] + [data]:
        ms = view.get("marginSummary") if isinstance(view.get("marginSummary"), dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue")))
        used = max(used, scoring._f(ms.get("totalMarginUsed")))
        withdrawable = max(withdrawable, scoring._f(view.get("withdrawable")))
        for ap in (view.get("assetPositions") or []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            coin = pos.get("coin") or pos.get("asset")
            szi = scoring._f(pos.get("szi"), 0.0)
            if coin and szi != 0.0:
                held[_bare(coin)] = ("LONG" if szi > 0 else "SHORT", scoring._f(pos.get("entryPx"), 0.0))
    if used > 1.0 and not held:
        print("[manatee.scan] margin in use but no positions — corrupt read, standing down", file=sys.stderr)
        return None
    free = withdrawable if withdrawable > 0 else max(0.0, account_value - used)
    return held, free


def _listed(ctx):
    """Bare-upper names currently listed and not delisted, or None if unreadable."""
    data = _read(ctx, "market_list_instruments", {}, "market_list_instruments(main)")
    insts = data.get("instruments") if isinstance(data, dict) else data
    if not isinstance(insts, list):
        return None
    out = set()
    for inst in insts:
        if isinstance(inst, dict) and inst.get("name") and not inst.get("is_delisted"):
            out.add(_bare(inst["name"]))
    return out


def _closed_candles(ctx, name, interval):
    """The CLOSED bars for `name`, oldest first: the MCP's last row is the bar in progress and is dropped."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": name, "candle_intervals": [interval],
        "include_funding": False, "include_order_book": False,
    }, f"market_get_asset_data({name})")
    if not isinstance(md, dict):
        return None
    candles = (md.get("candles") or {}).get(interval)
    if not isinstance(candles, list) or len(candles) < 2:
        return None
    return candles[:-1]


def _bar_key(candle, fallback_day):
    """Identifies the closed bar a decision was made on: its open time when the row carries one,
    else the UTC day of the tick."""
    t = candle.get("t") if isinstance(candle, dict) else None
    return str(t) if t not in (None, "") else fallback_day


def scan(inputs, ctx):
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    max_slots = int(scoring._f(inputs.get("maxSlots"), 19))
    universe = [str(x) for x in (inputs.get("universe") or [])]
    interval = str(inputs.get("candleInterval") or "1d")
    max_adds = int(scoring._f(inputs.get("maxAddsPerAsset"), 1))
    add_step = scoring._f(inputs.get("addStepPct"), 3.0) / 100.0
    cap_mgn = scoring._f(inputs.get("maxMarginPctPerAsset"), 3.0)
    min_notional = scoring._f(inputs.get("venueMinNotionalUsd"), 12.0)
    headroom = scoring._f(inputs.get("freeMarginHeadroom"), 0.9)

    st = (ctx.state.last() or {}) if ctx.state else {}
    acted = dict(st.get("acted", {}) or {})       # bare -> key of the closed bar already acted on
    entries = dict(st.get("entries", {}) or {})   # bare -> {px, adds, margin, side, t}

    acct = _account(ctx)
    if acct is None:
        print("[manatee.scan] clearinghouse unreadable — no opens this tick", file=sys.stderr)
        return []
    held, free = acct
    budget = free * headroom                       # USD of margin this tick may commit
    listed = _listed(ctx)
    slots = max_slots - len(held)
    for bare in list(entries):            # a name that is no longer held starts fresh next time
        if bare not in held:
            del entries[bare]

    out = []
    for name in universe:
        bare = _bare(name)
        if listed is not None and bare not in listed:
            continue
        is_add = bare in held
        if not is_add and slots <= 0:
            continue
        rec = entries.get(bare) or {}
        if is_add and (int(rec.get("adds", 0)) >= max_adds or scoring._f(rec.get("margin"), 0.0) + scoring.sizing_for(inputs, add=True)[1] > cap_mgn):
            continue
        candles = _closed_candles(ctx, name, interval)
        if not candles:
            continue
        key = _bar_key(candles[-1], today)
        if acted.get(bare) == key:
            continue                      # this closed bar has been acted on (or judged) already
        th = scoring.build_thesis(name, candles, inputs)
        if th is None:
            acted[bare] = key             # judged: nothing to do on this bar
            continue
        close = scoring._close(candles[-1])
        if is_add:
            side, avg_px = held[bare]
            if th["direction"] != side:
                acted[bare] = key
                continue              # the signal flipped against an open position: the DSL owns that exit
            last_px = scoring._f(rec.get("px"), 0.0) or avg_px
            # scale only while the move deepens against the position AND it is still under water,
            # so an add never lands on a locked profit
            deeper = close <= last_px * (1.0 - add_step) if side == "LONG" else close >= last_px * (1.0 + add_step)
            under_water = (close < avg_px) if side == "LONG" else (close > avg_px)
            if not (last_px > 0 and deeper and (avg_px <= 0 or under_water)):
                acted[bare] = key
                continue
        lev, mgn = scoring.sizing_for(inputs, add=is_add)
        # ── free-margin gate: price the clip as the runtime will, off the one balance read ──
        margin_usd = free * mgn / 100.0
        if margin_usd * lev < min_notional:
            print(f"[manatee.scan] clip {mgn}% of ${free:.0f} free margin is under the ${min_notional:.0f} venue minimum — no opens this tick", file=sys.stderr)
            break
        if margin_usd > budget:
            print(f"[manatee.scan] free margin exhausted after {len(out)} signal(s) — the rest wait for the next tick", file=sys.stderr)
            break
        budget -= margin_usd
        adds = int(rec.get("adds", 0)) + 1 if is_add else 0
        if not is_add:
            slots -= 1
        acted[bare] = key
        entries[bare] = {"px": close, "adds": adds, "margin": scoring._f(rec.get("margin"), 0.0) + mgn, "side": th["direction"], "t": now}
        out.append({
            "asset": name, "direction": th["direction"], "marginPct": mgn, "leverage": lev,
            "data": scoring.signal_data(th, lev, "add" if is_add else "entry", adds),
        })

    if ctx.state is not None:
        try:
            ctx.state.append({"acted": acted, "entries": entries})
        except Exception as exc:  # noqa: BLE001
            print(f"[manatee.scan] state append failed: {exc!r}", file=sys.stderr)
    return out
