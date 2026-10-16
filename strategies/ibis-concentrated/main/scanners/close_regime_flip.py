"""IBIS CONCENTRATED — regime-flip invalidation.

Ibis is regime-CONDITIONAL by construction: it classifies the 4h regime, then applies the
entry rule belonging to that regime. So when the regime that justified a position no longer
supports its direction, the thesis is dead — not losing, dead. The parent can afford to
ignore that: it holds three positions, and a stale one costs a third of the book. This fork
holds ONE, so a dead position occupies the entire strategy until a price level resolves it.
That is what this scanner is for, and it is the reason one slot is defensible at all.

Signal invalidation, never a clock. A position is closed only when the CURRENT regime
contradicts it:

  TREND  — structure has flipped against the position. A long in a market whose 4h structure
           now prints lower lows is not a continuation trade any more.
  RANGE  — either the fade is now at the WRONG edge (a long that started in the bottom 22%
           and is now in the top 22% has had its move; the band edge it was faded from is
           behind it), or funding has gone extreme, which is the parent's own tell that the
           range is about to break — and a fade into a break is the losing side.
  UNCLEAR— NOT a contradiction. The no-trade band blocks new entries; it does not condemn an
           open one, and treating it as a close would flatten the book every time efficiency
           drifted through the middle.

HYSTERESIS. A single tick is not a flip. `flipConfirmTicks` consecutive contradicting scans
are required, counted per coin in ctx.state, and any non-contradicting tick resets the count.
Without it a borderline ER oscillating across `trendErThreshold` would churn the position and
pay a round trip of fees each time.

The DSL still owns every price-based exit — the stop, the four profit tiers. This scanner
never closes a position for being down, only for being wrong. Read-only, single-pass, and on
any failure it returns [] rather than crashing the tick or guessing a flip.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys

import scoring

_PREFIX = "[ibis-cc.close]"


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args=args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"{_PREFIX} {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    if isinstance(raw, dict) and raw.get("success") is False:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _open_positions(ctx):
    """[{coin, direction, roe}] across both sub-DEX views of the one wallet, or None on a
    failed read. None and [] are different: None means 'unknown', and an unknown book must
    never be interpreted as a flat one."""
    data = _read(ctx, "strategy_get_clearinghouse_state",
                 {"strategy_wallet": ctx.wallet}, "clearinghouse")
    if not isinstance(data, dict):
        return None
    out = []
    for sec in ("main", "xyz"):
        s = data.get(sec, {}) or {}
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []) or []:
            if not isinstance(ap, dict):
                continue
            p = ap.get("position", {}) or {}
            coin = str(p.get("coin", "")).strip()
            szi = scoring._f(p.get("szi"))
            margin_used = scoring._f(p.get("marginUsed"))
            if not coin or abs(szi) < 0.0001:
                continue
            upnl = scoring._f(p.get("unrealizedPnl"))
            out.append({
                "coin": coin,
                "direction": "LONG" if szi > 0 else "SHORT",
                "roe": (upnl / margin_used * 100.0) if margin_used > 0 else 0.0,
            })
    return out


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _candles_4h(ctx, coin):
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin, "candle_intervals": ["4h"],
        "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
    }, f"candles({coin})")
    if not isinstance(md, dict):
        return None
    c = md.get("candles", {})
    if not isinstance(c, dict):
        return None
    return c.get("4h") or None


def _funding_apr(ctx, coin):
    """Annualized funding for one name, or None when unreadable — never 0.0, which would
    read as 'calm' and could suppress a legitimate extreme-funding close."""
    rows = _read(ctx, "market_list_instruments", {"dex": _dex_for(coin)}, "instruments")
    if not isinstance(rows, list):
        if isinstance(rows, dict):
            rows = rows.get("instruments", rows.get("universe", rows.get("markets")))
        if not isinstance(rows, list):
            return None
    want = coin.split(":", 1)[-1].upper()
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or r.get("coin") or "")
        if name.split(":", 1)[-1].upper() != want:
            continue
        c = r.get("context", {})
        if not isinstance(c, dict):
            return None
        return scoring.annualized_funding_pct(c.get("funding"))
    return None


def _contradiction(position, candles, funding_apr, inputs):
    """(bool, reason) — does the CURRENT regime contradict this open position?"""
    regime, er, struct, _strength = scoring.classify_regime(candles, inputs)
    direction = position["direction"]

    if regime == "TREND":
        supported = "LONG" if struct == "UP" else "SHORT" if struct == "DOWN" else None
        if supported is not None and supported != direction:
            return True, (f"TREND regime (ER {er:.2f}) now prints {struct} structure, against a "
                          f"{direction} — the continuation thesis has flipped")
        return False, f"TREND regime (ER {er:.2f}) still {struct} — holding"

    if regime == "RANGE":
        extreme_apr = float(inputs.get("extremeFundingApr", 35.0))
        if funding_apr is not None and abs(funding_apr) >= extreme_apr:
            return True, (f"funding {funding_apr:+.1f}% APR is extreme (>= {extreme_apr:.0f}) — "
                          f"the range is set to break and a fade into a break is the wrong side")
        rb = scoring.range_bounds(candles, int(inputs.get("rangeBars", 24)))
        if rb is None:
            return False, "range bounds unreadable — holding"
        _lo, _hi, pos = rb
        buy_below = float(inputs.get("rangeBuyBelowPct", 0.22))
        sell_above = float(inputs.get("rangeSellAbovePct", 0.78))
        if direction == "LONG" and pos >= sell_above:
            return True, (f"a LONG fade is now at the range HIGH (pos {pos:.2f}) — the edge it "
                          f"was faded from is behind it")
        if direction == "SHORT" and pos <= buy_below:
            return True, (f"a SHORT fade is now at the range LOW (pos {pos:.2f}) — the edge it "
                          f"was faded from is behind it")
        return False, f"RANGE regime, {direction} still on its side (pos {pos:.2f}) — holding"

    # UNCLEAR — the no-trade band gates ENTRIES. It is not a verdict on an open position.
    return False, f"UNCLEAR regime (ER {er:.2f}) — no entries, but no verdict on an open position"


def _load_streaks(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    prev = (ctx.state.last() or {}).get("streaks", {})
    return dict(prev) if isinstance(prev, dict) else {}


def scan(inputs, ctx):
    confirm_ticks = max(1, int(inputs.get("flipConfirmTicks", 2)))

    positions = _open_positions(ctx)
    if positions is None:
        print(f"{_PREFIX} book unreadable — skipping tick (no close on an unknown book)",
              file=sys.stderr)
        return []
    if not positions:
        if ctx.state is not None:
            try:
                ctx.state.append({"streaks": {}})
            except Exception as exc:  # noqa: BLE001
                print(f"{_PREFIX} WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    prev = _load_streaks(ctx)
    streaks, out = {}, []

    for p in positions:
        coin = p["coin"]
        candles = _candles_4h(ctx, coin)
        if not candles:
            # Unknown regime is not a flipped regime. Carry the streak rather than resetting it,
            # so a flaky candle read cannot indefinitely postpone a confirmed flip.
            streaks[coin] = int(prev.get(coin, 0))
            print(f"{_PREFIX} {coin}: candles unreadable — holding, streak kept at "
                  f"{streaks[coin]}", file=sys.stderr)
            continue

        funding = _funding_apr(ctx, coin)
        bad, reason = _contradiction(p, candles, funding, inputs)
        if not bad:
            streaks[coin] = 0
            print(f"{_PREFIX} {coin} {p['direction']}: {reason}", file=sys.stderr)
            continue

        streaks[coin] = int(prev.get(coin, 0)) + 1
        if streaks[coin] < confirm_ticks:
            print(f"{_PREFIX} {coin} {p['direction']}: {reason} — {streaks[coin]}/{confirm_ticks} "
                  f"confirmations, waiting", file=sys.stderr)
            continue

        full = (f"regime invalidation after {streaks[coin]} confirmations: {reason}")
        print(f"{_PREFIX} CLOSE {coin} {p['direction']} @ {p['roe']:+.1f}% ROE — {full}",
              file=sys.stderr)
        out.append({
            "asset": coin,
            "direction": p["direction"],
            "data": {
                "roe": round(p["roe"], 2),
                "confirmations": streaks[coin],
                "reason": full,
            },
        })

    if ctx.state is not None:
        try:
            ctx.state.append({"streaks": streaks})
        except Exception as exc:  # noqa: BLE001
            print(f"{_PREFIX} WARNING: state append failed: {exc!r}", file=sys.stderr)

    return out
