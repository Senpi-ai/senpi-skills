"""OXPECKER — supervised scanner (NET-NEW Runtime 3.0 elite-conviction mirror).

COPY-TRADER / TRADER-FOLLOWER. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum();
    bison/remora pattern, incl. the read-sanity guard),
  - refreshes a quality-tier pool from discovery_get_top_traders (consistency
    ELITE/RELIABLE, cached ~24h in ctx.state like Jackal; on a failed refresh it
    DEGRADES to the stale cohort cache, never crashes),
  - reads every pooled trader's open positions (discovery_get_trader_state, batched),
  - for each trader takes their LARGEST-notional position and computes CONCENTRATION
    (largest notional / total book notional); keeps only positions with
    concentration >= minConcentration (the spec's elite-conviction gate),
  - aggregates the concentrated elite positions into (asset, direction) candidates
    weighted by trader quality x concentration (consensus across multiple elite
    traders = stronger), and emits the TOP 1-2 highest-conviction mirrors sized by
    Oxpecker's OWN marginPct + leverage clamp.

Read-only + single-pass — emits `marginPct` (PERCENT) + `leverage` intents; the
runtime sizes the dollars, owns slots/cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position.

READ-GUARD: EVERY ctx.senpi_mcp.call_tool is wrapped in try/except -> degrade (stale
cohort cache / skipped trader batch / skip the tick), NEVER propagate. Per the scan
contract ANY exception rolls the whole tick back to [], so one bad read would
silently kill all emits — guarded so a single flaky read just drops out.

FIELD-SHAPE FLAGS (no live token in the build env): quality tier (`tcsLabel`),
trader-state open-position list key, and per-position notional field are sourced from
the gold templates (jackal/raptor/remora) + the senpi-overview guide §8, NOT
re-confirmed against a live response. Every parse has fallbacks; concentration is
derived from the positions themselves so it never depends on a trader-level field.
See scoring.py module docstring for the per-field flags.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import sys
import time

import scoring


CACHE_VERSION = 1            # bump if pool-BUILDING logic changes (busts a stale cache)
_DEFAULT_RECENT_TTL = 300    # race-window signal-dedup: don't re-fire a coin+dir while in flight


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll the whole
    tick back to []. Returns None on failure so the degrade paths apply (cohort falls
    back to its daily cache; a failed trader-state batch is skipped)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[oxpecker.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _unwrap_list(resp, *keys):
    """Unwrap a discovery list document: resp -> resp.data -> resp.data.<key> -> list."""
    if not resp:
        return []
    raw = resp.get("data", resp) if isinstance(resp, dict) else resp
    if isinstance(raw, dict):
        for k in keys:
            if isinstance(raw.get(k), list):
                return raw[k]
        for v in raw.values():           # single-key dict wrapping the list
            if isinstance(v, list):
                return v
        return []
    return raw if isinstance(raw, list) else []


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


# ── account (dual-DEX equity, read-sanity guard) — bison/remora pattern ──

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts the shared free
    balance -> 2x sizing). assetPositions are per-sub-DEX so they are enumerated
    across both sections. Includes the read-sanity guard (margin in use + empty
    positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[oxpecker.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring.safe_float(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring.safe_float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", "")})

    # read-sanity guard (funding/$0 glitch family): a corrupt clearinghouse read can
    # report margin/notional IN USE while returning an EMPTY positions list; sizing or
    # running the held-asset dedup off that re-enters held names (pyramiding) and
    # mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[oxpecker.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── trader pool: refresh from discovery_get_top_traders (ELITE/RELIABLE), cached ~24h ──

def _refresh_pool(ctx, cached, inputs, now):
    """Return the active elite-trader pool. Fresh cache (< refresh_h) is reused with
    NO fetch. On a stale/missing cache, fetch + filter to ELITE/RELIABLE + rank
    (scoring.build_pool). On a FAILED refresh, DEGRADE to the stale cache. Cached in
    ctx.state (jackal pattern)."""
    refresh_h = float(inputs.get("poolRefreshHours", 24))
    fetch_limit = int(inputs.get("poolFetchLimit", 60))
    time_frame = str(inputs.get("poolTimeFrame", "MONTHLY"))
    sort_by = str(inputs.get("poolSortBy", "RETURN_ON_INVESTMENT"))
    tiers = list(inputs.get("qualityTiers", list(scoring.DEFAULT_QUALITY_TIERS)))

    if (cached.get("traders") and cached.get("cache_version") == CACHE_VERSION
            and (now - cached.get("refreshed_at", 0)) / 3600 < refresh_h):
        return cached                                     # fresh cache — no fetch

    resp = _read(ctx, "discovery_get_top_traders", {
        "time_frame": time_frame,
        "sort_by": sort_by,
        "consistency": tiers,                             # ELITE/RELIABLE quality gate at the source
        "open_position_filter": True,                     # only traders with at least one open position
        "limit": fetch_limit,
    })
    raw = _unwrap_list(resp, "traders", "data")
    if not raw:
        return cached                                     # failed refresh — keep old cache (degrade)

    top = scoring.build_pool(raw, inputs)
    if not top:
        return cached                                     # filtered to empty — keep old cache
    return {"refreshed_at": now, "cache_version": CACHE_VERSION,
            "size": len(top), "traders": top}


def _fetch_pool_positions(ctx, pool):
    """{addr -> [open_position dicts]} for every pooled trader (discovery_get_trader_state,
    batched by 50, sorted by POSITION_VALUE desc so the largest is first). A failed
    batch is skipped (those traders just look empty this tick)."""
    addresses = [t["address"] for t in pool]
    by_addr = {}
    for i in range(0, len(addresses), 50):
        resp = _read(ctx, "discovery_get_trader_state", {
            "trader_addresses": addresses[i:i + 50],
            "sort_open_positions_by": "POSITION_VALUE",
            "sort_open_positions_direction": "DESC",
        })
        for t in _unwrap_list(resp, "traders"):
            if not isinstance(t, dict):
                continue
            addr = (t.get("address") or t.get("trader_address") or "").lower()
            if not addr:
                continue
            by_addr[addr] = (t.get("openPositions") or t.get("open_positions")
                             or t.get("positions") or [])
    return by_addr


# ── ctx.state: recent-signal dedup ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (remora/bison pattern)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, key, ttl, now):
    last = signaled.get(key)
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    min_conc = float(inputs.get("minConcentration", scoring.DEFAULT_MIN_CONCENTRATION))
    min_notional = float(inputs.get("minNotionalUsd", scoring.DEFAULT_MIN_NOTIONAL_USD))
    min_score = int(inputs.get("minScore", scoring.DEFAULT_MIN_SCORE))
    max_emit = max(1, int(inputs.get("maxEmit", 2)))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    lev = int(inputs.get("leverage", scoring.DEFAULT_LEVERAGE))
    min_lev = int(inputs.get("minLeverage", scoring.MIN_LEVERAGE))
    max_lev = int(inputs.get("maxLeverage", scoring.MAX_LEVERAGE))
    leverage = max(min_lev, min(lev, max_lev))            # mirror-cap to [1,5]

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    last = (ctx.state.last() or {}) if ctx.state else {}
    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    pool = _refresh_pool(ctx, last.get("cohorts", {}), inputs, now)
    traders = pool.get("traders", [])

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"cohorts": pool, "signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[oxpecker.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    if not traders:
        result = {"ts": now, "pool_size": 0, "emitted": 0, "note": "WAITING (no pool)"}
        print("[oxpecker.scan] WAITING — empty elite-trader pool (cohort refresh/cache empty)",
              file=sys.stderr)
        _persist(result)
        return []

    # current open book for every pooled (elite) trader
    current_positions = _fetch_pool_positions(ctx, traders)

    # ── per trader: largest position + concentration; keep only concentrated ones ──
    trader_tops = []
    concentrated = 0
    for trader in traders:
        book = current_positions.get(trader["address"], [])
        top, concentration = scoring.concentrated_top(book, min_notional)
        if not top:
            continue
        if concentration < min_conc:                      # the elite-conviction gate
            continue
        concentrated += 1
        trader_tops.append((trader, top, concentration))

    # ── aggregate into (asset, direction) candidates weighted by quality x concentration ──
    candidates = scoring.aggregate_candidates(trader_tops)
    scored = []
    for cand in candidates:
        if cand["asset"].upper() in held_set:             # on-chain held-asset filter
            continue
        key = f"{cand['asset'].upper()}|{cand['direction']}"
        if _was_recently_signaled(signaled, key, ttl, now):
            continue
        score, reasons = scoring.score_candidate(cand)
        if score >= min_score:
            scored.append((score, reasons, cand))

    out = []
    if not scored:
        result = {"ts": now, "pool_size": len(traders), "concentrated": concentrated,
                  "candidates": len(candidates), "emitted": 0, "held": held_assets,
                  "note": f"WAITING (min score {min_score}, min conc {min_conc:.0%})"}
        print(f"[oxpecker.scan] WAITING — no elite-conviction mirror "
              f"(pool={len(traders)} concentrated={concentrated} "
              f"candidates={len(candidates)} held={held_assets})", file=sys.stderr)
        _persist(result)
        return out

    # rank: highest score, tie-break by concentration, then consensus count, then notional
    scored.sort(key=lambda t: (t[0], t[2]["max_concentration"], t[2]["count"],
                               t[2]["max_notional"]), reverse=True)

    emitted = 0
    for score, reasons, cand in scored:
        if emitted >= max_emit:
            break
        key = f"{cand['asset'].upper()}|{cand['direction']}"
        margin_pct = scoring.margin_pct_for(cand, inputs)
        signaled[key] = now
        emitted += 1
        print(f"[oxpecker.scan] EMIT {cand['asset']} {cand['direction']} score={score} "
              f"conc={cand['max_concentration']:.0%} traders={cand['count']} "
              f"maxNotional=${cand['max_notional']:,.0f} {leverage}x "
              f"marginPct={margin_pct:.2f}% | {reasons[:5]}", file=sys.stderr)
        out.append({
            "asset": cand["asset"],
            "direction": cand["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # clamped to [1,5]; runtime applies + venue-caps it
            "data": {
                "score": score,
                "leverage": leverage,
                "direction": cand["direction"],
                "reasons": reasons,
                "concentration": cand["max_concentration"],
                "maxConcentration": cand["max_concentration"],
                "traderCount": cand["count"],
                "maxNotionalUsd": round(cand["max_notional"], 2),
                "eliteTier": bool(cand.get("any_elite")),
                "qualityScore": round(cand.get("best_quality", 0.0), 4),
                "sourceTraders": [str(t.get("address", ""))[:10] for t in cand.get("traders", [])],
                "heldAssets": held_assets,
            },
        })

    result = {"ts": now, "pool_size": len(traders), "concentrated": concentrated,
              "candidates": len(candidates), "emitted": emitted, "held": held_assets}
    _persist(result)
    return out
