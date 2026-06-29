"""JACKAL — supervised scanner (Runtime 3.0 port of the v2 Jackal Smart-Stalker producer).

TRADER-FOLLOWER / COHORT copy-trader. Per tick it:
  - refreshes a quality-scored trader pool from discovery_get_top_traders (cached ~24h
    in ctx.state; on a failed refresh it DEGRADES to the stale cohort cache, never crashes),
  - fetches every pool member's current open positions (discovery_get_trader_state, batched),
  - diffs against last-tick's per-trader baseline to detect FRESH new entries (< 10 min old),
  - enriches each candidate with pool consensus + per-asset multi-timeframe TA + funding
    regime (market_get_funding_regime, neutral on fail) + asset funding + BTC 24h macro,
  - emits ONE marginPct intent per fresh entry (the runtime sizes, owns slots/dedup/risk,
    and trails the DSL exit).

Read-only + single-pass. Derived universe — coins come from what pool members enter, not a
fixed list. Every ctx.senpi_mcp.call_tool is wrapped in try/except -> degrade (stale cohort
cache / skip member / neutral regime / UNKNOWN TA), so one bad read never rolls the whole
tick back to []. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs jackal-producer.py v3.0.1:
  - v2 was a 60s producer_daemon pushing signals via SenpiClient.push_signal(); this is a
    single-pass scan() emitting plain dicts the runtime sizes/executes. No HTTP POST, no
    daemon loop, no reentrancy lock (the runtime supervises ticks).
  - v2 emitted `score = quality_score/100` (0..1 confidence) and the runtime sized every
    entry from a FLAT margin_pct: 30 (NO producer-side conviction scaling). This port emits
    `marginPct` TOP-LEVEL at the same flat base (scoring.margin_pct_for, qualityMarginScale
    default 0 => flat) — v2 sizing preserved exactly. quality_score is still carried on
    data{} as sourceQualityScore (and as `score`).
  - v2 LLM gate (decision_mode: llm, min_confidence 7) is replaced by decision_mode: rule
    in the runtime.yaml. The producer already applied every HARD filter (pool quality floors,
    fresh-entry window, direction derivation); the v2 LLM prompt's SOFT checks (consensus>0,
    TA-not-all-UNKNOWN, funding-not-fighting, quality>=55) are reproduced as RULE gates here
    (scan-level skips) so behaviour matches the gated v2 without an LLM in the loop. FLAGGED.
  - v2 cohort cache (state/pool.json, daily) + last-seen baseline (state/last-seen.json) ->
    ctx.state records. The v2 baseline-seed guard is preserved VERBATIM: on an EMPTY baseline
    (first tick / state reset) emit 0 signals and just seed, so existing positions are never
    re-detected as "new" on startup (the v2.0.3 live bug).
  - Dropped: nothing order-lifecycle (the v2 producer had no cancel_order / order purge —
    it never managed orders, only emitted signals). Nothing to drop.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import sys
import time

import scoring


CACHE_VERSION = 1            # bump if pool-BUILDING logic changes (busts a stale cache)
_DEFAULT_RECENT_TTL = 600    # 10m signal-dedup: don't re-fire a coin+dir while a signal is in flight
_DEFAULT_MAX_ENTRY_AGE = 600  # v2 MAX_ENTRY_AGE_SECONDS — only mirror entries < 10 min old


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll the whole
    tick back to []. Returns None on failure so the existing degrade paths apply (cohort
    falls back to its daily cache; a failed member-state batch is skipped; TA -> UNKNOWN)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[jackal.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _unwrap_list(resp, *keys):
    """Unwrap the discovery list document: resp -> resp.data -> resp.data.<key> -> list."""
    if not resp:
        return []
    raw = resp.get("data", resp) if isinstance(resp, dict) else resp
    if isinstance(raw, dict):
        for k in keys:
            if isinstance(raw.get(k), list):
                return raw[k]
        # fall through: a single-key dict wrapping the list
        for v in raw.values():
            if isinstance(v, list):
                return v
        return []
    return raw if isinstance(raw, list) else []


# ── trader pool: refresh from discovery_get_top_traders, cached ~24h in ctx.state ──

def _refresh_pool(ctx, cached, inputs, now):
    """Return the active trader pool. Fresh cache (< refresh_h) is reused with NO fetch.
    On a stale/missing cache, fetch + filter + rank (scoring.build_pool). On a FAILED
    refresh, DEGRADE to the stale cache (v2 refresh_pool fallback). Cached in ctx.state."""
    refresh_h = float(inputs.get("poolRefreshHours", 24))
    fetch_limit = int(inputs.get("poolFetchLimit", 60))   # v2 overfetch then filter
    if (cached.get("traders") and cached.get("cache_version") == CACHE_VERSION
            and (now - cached.get("refreshed_at", 0)) / 3600 < refresh_h):
        return cached                                     # fresh cache — no fetch

    resp = _read(ctx, "discovery_get_top_traders", {
        "time_frame": "MONTHLY",
        "sort_by": "RETURN_ON_INVESTMENT",
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
    """{addr -> [open_position dicts]} for every pool member (discovery_get_trader_state,
    batched by 50). A failed batch is skipped (those members just look unchanged)."""
    addresses = [t["address"] for t in pool]
    by_addr = {}
    for i in range(0, len(addresses), 50):
        resp = _read(ctx, "discovery_get_trader_state",
                     {"trader_addresses": addresses[i:i + 50]})
        for t in _unwrap_list(resp, "traders"):
            if not isinstance(t, dict):
                continue
            addr = (t.get("address") or "").lower()
            if not addr:
                continue
            by_addr[addr] = t.get("openPositions") or t.get("open_positions") or []
    return by_addr


def _fetch_funding_regime(ctx):
    """Market-wide funding regime once per tick, or None (neutral on fail). v2 fetch_funding_regime."""
    resp = _read(ctx, "market_get_funding_regime", {})
    if not resp:
        return None
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    return data.get("regime") if isinstance(data, dict) else None


def _dex_for(coin):
    return "xyz" if str(coin).lower().startswith("xyz:") else ""


def _enrich_ta(ctx, coin, funding_regime):
    """Per-candidate 15m/1h/4h trend + asset funding for `coin`. Degrades to UNKNOWN/None
    on a failed read (v2 enrich_with_ta swallowed exceptions to a None-filled dict)."""
    out = {"trend_4h": None, "trend_1h": None, "trend_15m": None,
           "price_change_4h_pct": None, "funding_regime": funding_regime,
           "funding_annualized_pct": None}
    resp = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["15m", "1h", "4h"],
        "include_funding": True,
        "include_order_book": False,
        "dex": _dex_for(coin),
    })
    if not resp:
        return out
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    if not isinstance(data, dict):
        return out
    candles = data.get("candles", {}) or {}
    out["price_change_4h_pct"] = scoring.trend_pct(candles.get("4h"))
    out["trend_4h"] = scoring.trend_label(out["price_change_4h_pct"])
    out["trend_1h"] = scoring.trend_label(scoring.trend_pct(candles.get("1h")))
    out["trend_15m"] = scoring.trend_label(scoring.trend_pct(candles.get("15m")))
    ac = data.get("asset_context") or data.get("assetContext") or {}
    if isinstance(ac, dict) and ac.get("funding") is not None:
        out["funding_annualized_pct"] = scoring.annualize_funding(ac.get("funding"))
    return out


def _fetch_btc_macro(ctx):
    """BTC 24h direction/pct from 1h candles, or (None, None). v2 fetch_btc_macro."""
    resp = _read(ctx, "market_get_asset_data", {
        "asset": "BTC",
        "candle_intervals": ["1h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": "",
    })
    if not resp:
        return None, None
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    candles_1h = (data.get("candles", {}) or {}).get("1h", []) if isinstance(data, dict) else []
    return scoring.macro_pct(candles_1h)


# ── soft gates (port of the v2 LLM decision_prompt hard-skip conditions, as RULES) ──

def _passes_soft_gates(c, ta, inputs):
    """Reproduce the v2 LLM gate's HARD SKIP conditions as deterministic rule gates
    (no LLM in the 3.0 loop). Returns (ok, reason).
      - sourceQualityScore < qualityFloor (55)            -> skip
      - poolConsensusCount == 0 AND all TA UNKNOWN/contra -> skip (no independent confirm)
      - all of trend4h/1h/15m UNKNOWN                      -> skip (no data)
      - fundingRegime actively fights direction           -> skip (SHORT into SHORT_CROWDED, etc.)
    """
    floor = float(inputs.get("qualityFloor", 55))
    q = scoring._f(c["trader"].get("quality_score"), default=0.0)
    if q < floor:
        return False, f"quality<{floor:.0f}"

    labels = [ta.get("trend_4h"), ta.get("trend_1h"), ta.get("trend_15m")]
    known = [x for x in labels if x not in (None, "UNKNOWN")]
    if not known:
        return False, "TA_all_unknown"

    want = "BULLISH" if c["direction"] == "LONG" else "BEARISH"
    confirms = any(x == want for x in known)
    if c["pool_consensus_count"] == 0 and not confirms:
        return False, "solo_no_TA_confirm"

    regime = (ta.get("funding_regime") or "").upper()
    if c["direction"] == "SHORT" and "SHORT_CROWDED" in regime:
        return False, "regime_fights_short"
    if c["direction"] == "LONG" and "LONG_CROWDED" in regime:
        return False, "regime_fights_long"
    return True, "ok"


def scan(inputs, ctx):
    now = time.time()
    max_entry_age = float(inputs.get("maxEntryAgeSeconds", _DEFAULT_MAX_ENTRY_AGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    std_lev = int(inputs.get("stdLeverage", 5))
    max_lev = int(inputs.get("maxLeverage", 5))
    soft_gates = bool(inputs.get("applySoftGates", True))

    last = (ctx.state.last() or {}) if ctx.state else {}
    last_seen = {k.lower(): v for k, v in (last.get("last_seen") or {}).items()
                 if isinstance(v, list)}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    pool = _refresh_pool(ctx, last.get("cohorts", {}), inputs, now)
    traders = pool.get("traders", [])

    # current positions for every pool member
    current_positions = _fetch_pool_positions(ctx, traders) if traders else {}

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({
                "cohorts": pool,
                "last_seen": current_positions,   # next-tick baseline
                "recent": recent,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[jackal.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    if not traders:
        result = {"ts": now, "pool_size": 0, "emitted": 0, "note": "WAITING (no pool)"}
        print("[jackal.scan] WAITING — empty trader pool (cohort refresh/cache empty)",
              file=sys.stderr)
        _persist(result)
        return []

    # v2.0.4 baseline-seed guard (preserved VERBATIM): on an EMPTY baseline (first tick
    # ever / state reset), DO NOT treat every current pool-member position as "new" —
    # that would emit signals for all existing positions on startup (the v2.0.3 live bug).
    # Emit 0, just seed the baseline; the NEXT tick can safely diff.
    if not last_seen:
        result = {"ts": now, "pool_size": len(traders), "emitted": 0,
                  "note": "WAITING (baseline seed)"}
        print(f"[jackal.scan] WAITING — baseline seed (pool={len(traders)}, "
              f"{len(current_positions)} members snapshotted); no diff on first tick",
              file=sys.stderr)
        _persist(result)
        return []

    candidates = scoring.detect_new_entries(traders, current_positions, last_seen,
                                            now, max_entry_age)
    candidates = scoring.enrich_with_consensus(candidates, current_positions)

    if not candidates:
        result = {"ts": now, "pool_size": len(traders), "emitted": 0,
                  "note": "WAITING (no fresh entries)"}
        print(f"[jackal.scan] WAITING — no fresh pool-member entries "
              f"(pool={len(traders)})", file=sys.stderr)
        _persist(result)
        return []

    # shared per-tick context (v2.0.3: fetch ONCE per run, not per candidate)
    btc_dir, btc_pct = _fetch_btc_macro(ctx)
    funding_regime = _fetch_funding_regime(ctx)
    leverage = min(std_lev, max_lev)

    out = []
    emitted = 0
    for c in candidates:
        cu = c["coin"].upper()
        dk = f"{cu}|{c['direction']}"
        if recent.get(dk) is not None and (now - recent[dk]) < ttl:   # signal-dedup
            continue

        ta = _enrich_ta(ctx, c["coin"], funding_regime)
        if soft_gates:
            ok, why = _passes_soft_gates(c, ta, inputs)
            if not ok:
                print(f"[jackal.scan] SKIP {c['coin']} {c['direction']} ({why})",
                      file=sys.stderr)
                continue

        trader = c["trader"]
        q = scoring._f(trader.get("quality_score"), default=0.0)
        margin_pct = scoring.margin_pct_for(q, inputs)
        out.append({
            "asset": c["coin"],
            "direction": c["direction"],
            "marginPct": margin_pct,           # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,              # std; runtime clamps to venue max
            "data": {
                "score": scoring.confidence_score(q),   # 0..1 confidence (v2 push_signal score)
                "direction": c["direction"],
                "sourceTraderAddress": trader["address"],
                "sourceTraderUserId": str(trader.get("user_id") or ""),
                "sourceTraderUsername": str(trader.get("username") or ""),
                "sourceQualityScore": q,
                "sourceWinRate": scoring._f(trader.get("win_rate"), default=0.0),
                "sourceRoi30d": scoring._f(trader.get("roi_30d"), default=0.0),
                "sourceConsecutiveWins": int(scoring._f(trader.get("consecutive_wins"), default=0)),
                "entryTimestamp": c["entry_ts"],
                "sourceLeverage": c["leverage"],
                "sizeUsd": c["size_usd"],
                "entryPrice": c["entry_price"],
                "poolConsensusCount": c["pool_consensus_count"],
                "poolConsensusAssetCount": c["pool_consensus_asset_count"],
                "trend4h": ta["trend_4h"] or "UNKNOWN",
                "trend1h": ta["trend_1h"] or "UNKNOWN",
                "trend15m": ta["trend_15m"] or "UNKNOWN",
                "priceChange4hPct": ta["price_change_4h_pct"] if ta["price_change_4h_pct"] is not None else 0,
                "fundingRegime": ta["funding_regime"] or "UNKNOWN",
                "fundingAnnualizedPct": ta["funding_annualized_pct"] if ta["funding_annualized_pct"] is not None else 0,
                "btcMacroDirection": btc_dir or "UNKNOWN",
                "btcMacro24hPct": btc_pct if btc_pct is not None else 0,
            },
        })
        recent[dk] = now
        emitted += 1
        print(f"[jackal.scan] EMIT {c['coin']} {c['direction']} q={q:.1f} "
              f"consensus={c['pool_consensus_count']} {leverage}x marginPct={margin_pct:.2f}% "
              f"src={trader['address'][:8]}", file=sys.stderr)

    if emitted == 0:
        print(f"[jackal.scan] WAITING — {len(candidates)} fresh entries, none passed "
              f"dedup/soft-gates (pool={len(traders)})", file=sys.stderr)

    result = {"ts": now, "pool_size": len(traders),
              "candidates": len(candidates), "emitted": emitted,
              "btc_macro": [btc_dir, btc_pct], "funding_regime": funding_regime}
    _persist(result)
    return out
