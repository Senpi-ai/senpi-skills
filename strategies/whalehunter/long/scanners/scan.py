"""WHALEHUNTER — supervised scanner (Runtime 3.0 port of the v2 cohort engine).

Direction-parametrized and shared verbatim by both sleeves: the `long` instance
passes direction=LONG (positions with a net-long smart cohort); the `short` instance
passes direction=SHORT. Each sleeve runs on its OWN wallet and its OWN ctx.state, so
the cohort cache + daily ledger are per-sleeve (the v2 daemon shared them on disk;
3.0 instances are isolated — signals are identical, only the cache is duplicated).

Per tick: refresh the smart/crowd cohorts (cached daily in ctx.state), aggregate each
cohort's net positioning (discovery_get_trader_state), update the daily ledger to get
the 'adding daily' growth, score the divergences for THIS direction, and emit a
conviction-scaled marginUsd (pct of max()-collapsed account equity; runtime owns slots/dedup, trails DSL).
Read-only, single-pass. Derived universe — the coins come from the cohort's positions,
not a fixed list. NOTE: ~1 UTC-day warmup before requireGrowing can pass."""

import sys
import time

import scoring

CACHE_VERSION = 1     # bump if the cohort-BUILDING logic changes (busts a stale cache)
_DEFAULT_TTL = 3600   # 60m signal-dedup: don't re-fire a coin while a signal is in flight


def _build_cohorts(ctx, cached, inputs, now):
    """Smart cohort (lifetime realized >= smartMinRealizedUsd) + crowd cohort
    (crowdMin..crowdMax) from the ALL_TIME realized-PnL ranking, PAGED by offset to
    reach the deep crowd band, each capped. Cached daily in ctx.state."""
    refresh_h = float(inputs.get("cohortRefreshHours", 24))
    if (cached.get("smart") and cached.get("cache_version") == CACHE_VERSION
            and (now - cached.get("refreshed_at", 0)) / 3600 < refresh_h):
        return cached                                       # fresh cache — no fetch
    smin = float(inputs.get("smartMinRealizedUsd", 1_000_000))
    cmin = float(inputs.get("crowdMinRealizedUsd", 10_000))
    cmax = float(inputs.get("crowdMaxRealizedUsd", 100_000))
    cap = int(inputs.get("cohortSampleCap", 250))
    page_size = int(inputs.get("cohortFetchLimit", 1000))
    max_pages = int(inputs.get("cohortMaxPages", 6))
    smart, crowd, seen = [], [], set()
    for page in range(max_pages):
        resp = ctx.senpi_mcp.call_tool("discovery_get_top_traders", {
            "time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
            "open_position_filter": False, "limit": page_size, "offset": page * page_size})
        if not resp:
            break
        raw = resp.get("data", resp) if isinstance(resp, dict) else resp
        if isinstance(raw, dict):
            raw = raw.get("traders", raw.get("data", []))
        if not isinstance(raw, list) or not raw:
            break
        page_top = None
        for t in raw:
            if not isinstance(t, dict):
                continue
            addr = (t.get("address") or t.get("trader_address") or "").lower()
            if not addr or addr in seen:
                continue
            rp = scoring.realized(t)
            page_top = rp if page_top is None else max(page_top, rp)
            if rp >= smin:
                if len(smart) < cap:
                    smart.append(addr)
                    seen.add(addr)
            elif cmin <= rp <= cmax:
                if len(crowd) < cap:
                    crowd.append(addr)
                    seen.add(addr)
        if len(smart) >= cap and len(crowd) >= cap:
            break
        if page_top is not None and page_top < cmin:        # whole page below the crowd floor
            break
    if not smart and not crowd:
        return cached                                       # failed refresh — keep the old cache
    return {"refreshed_at": now, "cache_version": CACHE_VERSION, "smart": smart, "crowd": crowd}


def _fetch_states(ctx, addrs):
    """discovery_get_trader_state in batches of 50 -> flat list of trader-state dicts."""
    traders = []
    for i in range(0, len(addrs), 50):
        resp = ctx.senpi_mcp.call_tool("discovery_get_trader_state",
                                       {"trader_addresses": addrs[i:i + 50]})
        if not resp:
            continue
        data = resp.get("data", resp) if isinstance(resp, dict) else resp
        if isinstance(data, dict):
            traders.extend(data.get("traders", []) or [])
    return traders


def _account_value(ctx):
    """max()-collapsed account equity from strategy_get_clearinghouse_state. main/xyz
    are TWO VIEWS of ONE cross-margin wallet — take max(), never sum (summing doubles
    the equity and 2x every position size). The dex holding a position reports full
    equity; the other reports free collateral, so max() yields the true equity."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception:
        return 0.0
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0
    av = 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if isinstance(s, dict):
            ms = s.get("marginSummary", {})
            av = max(av, float(ms.get("accountValue", 0) or 0))
    return av


def scan(inputs, ctx):
    direction = (inputs.get("direction", "LONG") or "LONG").upper()
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    std_lev = int(inputs.get("stdLeverage", 3))
    max_lev = int(inputs.get("maxLeverage", 5))
    min_members = int(inputs.get("cohortMinMembers", 5))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    last = (ctx.state.last() or {}) if ctx.state else {}
    ledger_days = (last.get("ledger") or {}).get("days", {})
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    cohorts = _build_cohorts(ctx, last.get("cohorts", {}), inputs, now)
    smart, crowd = cohorts.get("smart", []), cohorts.get("crowd", [])

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"cohorts": cohorts, "ledger": {"days": ledger_days}, "recent": recent})
        except Exception as exc:  # noqa: BLE001
            print(f"[whalehunter.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if len(smart) < min_members:                            # cohort too small — cache & bail
        _persist()
        return []

    smart_per = scoring.aggregate_bias(_fetch_states(ctx, smart))
    crowd_per = scoring.aggregate_bias(_fetch_states(ctx, crowd))
    ledger_days, growth = scoring.update_ledger(ledger_days, smart_per, today)
    strikes = scoring.cohort_signals(smart_per, crowd_per, growth, direction, inputs)

    leverage = min(std_lev, max_lev)
    account_value = _account_value(ctx)
    if account_value <= 0:                                   # can't size — emit nothing this tick
        print("[whalehunter.scan] WARNING: account value unavailable; no signals emitted",
              file=sys.stderr)
        _persist()
        return []

    out = []
    for s in strikes:
        cu = s["coin"].upper()
        if recent.get(cu) is not None and (now - recent[cu]) < ttl:   # signal-dedup
            continue
        # marginUsd = equity x conviction-scaled fraction from the `customMarginPct` input.
        # `customMarginPct` is a per-signal producer tunable — do NOT confuse it with the
        # runtime's strategy.margin_pct config field (a different sizing mechanism).
        margin_usd = round(account_value * scoring.margin_pct_for(s["score"], inputs), 2)
        if margin_usd * leverage < venue_min_notional:        # below venue min notional — skip
            continue
        out.append({
            "asset": s["coin"],
            "direction": direction,
            "marginUsd": margin_usd,                          # conviction-scaled USD; runtime opens it
            "leverage": leverage,                             # std, runtime clamps to venue max
            "data": {
                "score": s["score"], "direction": direction, "signalKind": "COHORT_DIVERGENCE",
                "smartBias": s["smart_bias"], "crowdBias": s["crowd_bias"],
                "smartGrowth": s["growth"], "smartMembers": s["n_confirm"], "reasons": s["reasons"],
            },
        })
        recent[cu] = now

    _persist()
    return out
