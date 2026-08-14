"""HEN — supervised scanner: pre-market-open equity BASKET positioning (Rooster's companion).

One of two clock-signal strategies (with Rooster); Hen runs the basket into the US open. Every tick it asks "are we inside the
pre-open window of a configured session?" — and outside that window it emits nothing at all,
cheaply, without a single market read.

Per tick, inside the window:
  - reads account + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - fetches 15m + 4h candles for the configured asset(s),
  - scores the pre-open setup via pure `scoring.build_thesis` (drift + volume expansion +
    prior-range breakout + 4h context),
  - emits at most one signal PER SESSION per asset (session-keyed dedup in ctx.state), so a
    45-minute window scanned every 5 minutes cannot fire nine times.

Read-only + single-pass. Emits a `marginPct` INTENT (PERCENT in (0,100]) + `leverage`.
Candle values are strings keyed o/h/l/c/v — scoring._close/_f coerce.

TIMEZONE: `sessionOpensUtc` is UTC, always. A US equity open is 13:30 UTC during EDT and
14:30 UTC during EST — set it for the half of the year you are in, or list both.
"""

import sys
import time

import scoring

_DEFAULT_ASSETS = ["xyz:NVDA", "xyz:TSLA", "xyz:AAPL", "xyz:META", "xyz:MSFT",
                   "xyz:GOOGL", "xyz:AMZN", "xyz:AMD", "xyz:MU", "xyz:INTC", "xyz:TSM", "xyz:ORCL"]
_DEFAULT_OPENS = ["13:30"]                  # US equity cash open (EDT), UTC


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position dicts]) from strategy_get_clearinghouse_state — dual-DEX equity via
    max() (never sum: two views of ONE cross-margined wallet), positions enumerated across BOTH
    sub-DEX sections. Read-guarded (a read error skips the tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the tick
        print(f"[hen.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        print("[hen.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _candles(ctx, coin):
    """{'15m':[...], '4h':[...]} for `coin` or None. Read-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": ["15m", "4h"],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[hen.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not md or (isinstance(md, dict) and md.get("success") is False):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return d.get("candles", {}) if isinstance(d, dict) else None


def _load_fired(ctx):
    """{session_key: ts} — which session opens we have already positioned into."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    f = (ctx.state.last() or {}).get("fired", {})
    return dict(f) if isinstance(f, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    assets = inputs.get("assets", _DEFAULT_ASSETS)
    opens = inputs.get("sessionOpensUtc", _DEFAULT_OPENS)
    pre_open_minutes = int(inputs.get("preOpenMinutes", 45))
    min_score = float(inputs.get("minScore", 5))
    base_margin_pct = float(inputs.get("marginPctBase", 12))   # PERCENT in (0,100]
    max_slots = int(inputs.get("maxSlots", 1))
    lev_default = int(inputs.get("leverageDefault", 3))
    lev_min = int(inputs.get("leverageMin", 2))
    lev_max = int(inputs.get("leverageMax", 5))

    # ── THE CLOCK GATE — outside the pre-open window we do nothing, and read nothing ──
    minute_of_day = int((now // 60) % 1440)             # UTC (time.time() is epoch/UTC)
    phase, open_minute, mins_to_open = scoring.session_phase(minute_of_day, opens, pre_open_minutes)
    if phase != "pre_open":
        print(f"[hen.scan] IDLE — {minute_of_day // 60:02d}:{minute_of_day % 60:02d} UTC is outside "
              f"every pre-open window (opens={opens}, window={pre_open_minutes}m)", file=sys.stderr)
        return []

    # ── THE TRADING-DAY GATE — equities have no open on weekends, so there is nothing to
    # position into. Gate on the weekday of the OPEN itself (now + mins_to_open), which is
    # correct even if the pre-open window wraps past midnight into the open's day.
    trading_days = inputs.get("tradingDaysUtc", [])
    open_weekday = time.gmtime(now + mins_to_open * 60).tm_wday    # 0=Mon .. 6=Sun, UTC
    if not scoring.is_trading_day(open_weekday, trading_days):
        print(f"[hen.scan] IDLE — the {open_minute // 60:02d}:{open_minute % 60:02d} UTC open falls on "
              f"weekday {open_weekday} (trading_days={trading_days}); no equity open to position into",
              file=sys.stderr)
        return []

    session_key = f"{int(now // 86400)}:{open_minute}"  # one key per calendar-day per session open
    fired = {k: v for k, v in _load_fired(ctx).items() if (now - v) < 86400 * 2}

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[hen.scan] WAITING — no account value / corrupt read", file=sys.stderr)
        return []
    held = {p["coin"].upper() for p in positions if p.get("coin")}
    open_slots = max_slots - len(held)
    if open_slots <= 0:
        print(f"[hen.scan] slots full ({len(held)}/{max_slots}) — DSL manages exits", file=sys.stderr)
        return []

    candidates, scanned = [], 0
    for coin in assets:
        if not coin:
            continue
        cu = coin.upper()
        if cu in held:
            continue
        if f"{session_key}:{cu}" in fired:              # already positioned into THIS open
            continue
        scanned += 1
        candles = _candles(ctx, coin)
        if not candles:
            continue
        th = scoring.build_thesis(coin, candles.get("15m", []), candles.get("4h", []),
                                  mins_to_open, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    leverage = max(lev_min, min(lev_default, lev_max))
    out = []
    for th in candidates[:open_slots]:
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)
        fired[f"{session_key}:{th['coin'].upper()}"] = now
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,      # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,
            # Runtime schema validation REJECTS a null for a field declared `type: number|string`,
            # even when `required: false` — the whole candidate is dropped (`candidate_rejected`),
            # silently. An optional field that does not apply must be OMITTED, never set to None.
            "data": {k: v for k, v in {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "reasons": th["reasons"][:8], "driftPct": th["drift_pct"],
                "volRatio": th["vol_ratio"], "rangePos": th["range_pos"],
                "trend4h": th["trend_4h"], "minutesToOpen": th["minutes_to_open"],
                "sessionOpenUtc": f"{open_minute // 60:02d}:{open_minute % 60:02d}",
                "heldAssets": sorted(held),
            }.items() if v is not None},
        })

    print(f"[hen.scan] {'EMIT' if out else 'WAITING'} — {mins_to_open}m to the "
          f"{open_minute // 60:02d}:{open_minute % 60:02d} UTC open; scanned={scanned} "
          f"emitted={len(out)} min_score={min_score:.0f} held={sorted(held)}", file=sys.stderr)
    if ctx.state is not None:
        try:
            ctx.state.append({"fired": fired,
                              "result": {"ts": now, "session": session_key, "scanned": scanned,
                                         "emitted": len(out), "minutesToOpen": mins_to_open}})
        except Exception as exc:  # noqa: BLE001
            print(f"[hen.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
