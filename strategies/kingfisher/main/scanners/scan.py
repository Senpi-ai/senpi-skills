"""KINGFISHER — supervised scanner: RSI + MACD classic-indicator crossover on a liquid-majors basket.

Per tick:
  - reads account + held positions (dual-DEX equity via max(), never sum; corrupt-read sanity guard),
  - iterates the configured `allowedAssets` basket (held + recently-signalled filtered first),
  - fetches 1h + 4h candles and scores each via pure `scoring.build_thesis` (MACD crossover trigger +
    RSI confirmation + 4h trend context),
  - emits EVERY candidate at/above `minScore`, best-first up to the open slots, sized by a conviction
    margin tier; the runtime applies the slots ceiling, sizes the dollars, owns cooldowns/risk gates,
    and trails the DSL exit.

Read-only + single-pass. Emits a `marginPct` INTENT (PERCENT in (0,100]) + `leverage`; no daemon, no
push_signal, no create_position. Candle values are strings keyed o/h/l/c/v — scoring._close/_f coerce.
"""

import sys
import time

import scoring

_ALLOWED_ASSETS_DEFAULT = ["BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "LINK", "DOGE"]
_DEFAULT_RECENT_TTL = 3600     # don't re-fire the same crossover within an hour (1h timeframe)


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position dicts]) from strategy_get_clearinghouse_state — dual-DEX equity via
    max() (never sum), plus the corrupt-read sanity guard. Read-guarded (a read error skips the tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the tick
        print(f"[kingfisher.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []
    positions, account_value, used = [], 0.0, 0.0
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
            positions.append({"coin": pos.get("coin", ""), "margin": scoring._f(pos.get("marginUsed", 0))})
    if used > 1.0 and not positions:   # corrupt read: margin in use but empty positions — skip tick
        print("[kingfisher.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _candles(ctx, coin):
    """{'1h':[...], '4h':[...]} for `coin` or None. Read-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": ["1h", "4h"],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[kingfisher.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not md or (isinstance(md, dict) and md.get("success") is False):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return d.get("candles", {}) if isinstance(d, dict) else None


def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    sig = (ctx.state.last() or {}).get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    allowed = inputs.get("allowedAssets", _ALLOWED_ASSETS_DEFAULT)
    min_score = float(inputs.get("minScore", 6))
    base_margin_pct = float(inputs.get("marginPctBase", 10))   # PERCENT in (0,100]
    max_slots = int(inputs.get("maxSlots", 3))
    lev_default = int(inputs.get("leverageDefault", 4))
    lev_min = int(inputs.get("leverageMin", 3))
    lev_max = int(inputs.get("leverageMax", 5))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[kingfisher.scan] WAITING — no account value / corrupt read", file=sys.stderr)
        return []
    held = {p["coin"].upper() for p in positions if p.get("coin")}
    open_slots = max_slots - len(held)
    if open_slots <= 0:
        print(f"[kingfisher.scan] slots full ({len(held)}/{max_slots}) — DSL manages exits", file=sys.stderr)
        return []

    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - v) < ttl * 4}

    candidates, scanned = [], 0
    for coin in allowed:
        if not coin or coin.lower().startswith("xyz:"):
            continue
        cu = coin.upper()
        if cu in held:
            continue
        if (now - signaled.get(cu, 0)) < ttl:
            continue
        scanned += 1
        candles = _candles(ctx, coin)
        if not candles:
            continue
        th = scoring.build_thesis(coin, candles.get("1h", []), candles.get("4h", []), inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    leverage = max(lev_min, min(lev_default, lev_max))
    out = []
    for th in candidates[:open_slots]:
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)
        signaled[th["coin"].upper()] = now
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,      # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "directionSource": th["directionSource"], "reasons": th["reasons"][:8],
                "rsi": th["rsi"], "macd": th["macd"], "signal": th["signal"], "hist": th["hist"],
                "trend4h": th["trend_4h"], "heldAssets": sorted(held),
            },
        })

    print(f"[kingfisher.scan] {'EMIT' if out else 'WAITING'} scanned={scanned} emitted={len(out)} "
          f"min_score={min_score:.0f} held={sorted(held)}", file=sys.stderr)
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled,
                              "result": {"ts": now, "scanned": scanned, "emitted": len(out)}})
        except Exception as exc:  # noqa: BLE001
            print(f"[kingfisher.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
