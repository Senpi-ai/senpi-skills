"""TERRAPIN — supervised scanner: one pyramid UNIT of a Turtle-style breakout.

The SAME file runs on all four unit wallets; the only thing that differs is the `unitIndex`
input (0..3) in each unit's runtime.yaml, which sets how far beyond the breakout this unit
arms. Each unit is a single-asset, single-position wallet:

Per tick:
  - reads account + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - if this wallet already holds the asset, does nothing — the DSL owns the exit,
  - fetches daily candles for the configured asset,
  - runs pure `scoring.build_thesis(coin, candles, unitIndex, inputs)`: Donchian breakout,
    frozen-anchor ATR ladder, MACD filter,
  - emits at most one signal (this unit either arms this tick or it doesn't).

Read-only + single-pass. Emits a `marginPct` INTENT (PERCENT in (0,100]) + `leverage`.
Candle values are strings keyed o/h/l/c/v — scoring._close/_f coerce.

Coherence note: the four wallets never talk. Each derives the SAME frozen breakout anchor
from the SAME candles, so the ladder is consistent without coordination. On a reversal the
pyramid unwinds top-down through the per-unit DSLs (the tighter upper units lock/stop first);
a genuine channel flip has closed the upper units before the opposite breakout arms. A wallet
never flips direction while holding — it must exit (via DSL) and go flat first.
"""

import sys
import time

import scoring

_DEFAULT_ASSET = "BTC"


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, held_bare_tickers) — dual-DEX equity via max() (never sum: two views of
    ONE cross-margined wallet), positions enumerated across BOTH sub-DEX sections. Read-guarded."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the tick
        print(f"[terrapin.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, set()
    if not ch:
        return 0.0, set()
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, set()
    held, account_value, used = set(), 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        used = max(used, scoring._f(ms.get("totalMarginUsed", 0)), abs(scoring._f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            if scoring._f(pos.get("szi", 0)) == 0:
                continue
            coin = str(pos.get("coin", "")).strip()
            if coin:
                held.add(coin.split(":", 1)[-1].upper())
    if used > 1.0 and not held:   # corrupt read: margin in use but empty positions — skip tick
        print("[terrapin.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, set()
    return account_value, held


def _candles(ctx, coin, interval):
    """Daily (or configured) candle list for `coin`, or None. Read-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": [interval],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[terrapin.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not md or (isinstance(md, dict) and md.get("success") is False):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    c = d.get("candles", {}) if isinstance(d, dict) else {}
    return c.get(interval, []) if isinstance(c, dict) else None


def scan(inputs, ctx):
    now = time.time()
    coin = str(inputs.get("asset", _DEFAULT_ASSET))
    unit_index = int(inputs.get("unitIndex", 0))
    interval = str(inputs.get("candleInterval", "1d"))
    min_score = float(inputs.get("minScore", 6))
    base_margin_pct = float(inputs.get("marginPctBase", 40))   # PERCENT of THIS unit's wallet
    lev_default = int(inputs.get("leverageDefault", 3))
    lev_min = int(inputs.get("leverageMin", 2))
    lev_max = int(inputs.get("leverageMax", 5))

    unit = unit_index + 1
    account_value, held = _get_account(ctx)
    if account_value <= 0:
        print(f"[terrapin.scan] u{unit} WAITING — no account value / corrupt read", file=sys.stderr)
        return []

    bare = coin.split(":", 1)[-1].upper()
    if bare in held:
        print(f"[terrapin.scan] u{unit} holding {bare} — DSL owns the exit", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"result": {"ts": now, "unit": unit, "state": "holding", "emitted": 0}})
            except Exception as exc:  # noqa: BLE001
                print(f"[terrapin.scan] u{unit} WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    candles = _candles(ctx, coin, interval)
    if not candles:
        print(f"[terrapin.scan] u{unit} WAITING — no candles for {coin}", file=sys.stderr)
        return []

    th = scoring.build_thesis(coin, candles, unit_index, inputs)
    armed = bool(th and th["score"] >= min_score)

    out = []
    if armed:
        leverage = max(lev_min, min(lev_default, lev_max))
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)
        out.append({
            "asset": coin,
            "direction": th["direction"],
            "marginPct": margin_pct,      # PERCENT of this unit's wallet — runtime sizes the dollars
            "leverage": leverage,
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "unit": unit, "atr": th["atr"], "rung": th["rung"], "beyondN": th["beyond_n"],
                "channelHigh": th["channel_high"], "channelLow": th["channel_low"],
                "macdHist": th["macd_hist"], "reasons": th["reasons"][:8],
            },
        })

    print(f"[terrapin.scan] u{unit} {'ARM ' + th['direction'] if armed else 'WAITING'} "
          f"{coin} price={scoring._close(candles[-1]):.6g} "
          f"{'rung=%.6g beyond=%.2fN score=%d' % (th['rung'], th['beyond_n'], th['score']) if th else 'no breakout'}",
          file=sys.stderr)
    if ctx.state is not None:
        try:
            ctx.state.append({"result": {"ts": now, "unit": unit, "emitted": len(out),
                                         "armed": armed, "score": (th or {}).get("score")}})
        except Exception as exc:  # noqa: BLE001
            print(f"[terrapin.scan] u{unit} WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
