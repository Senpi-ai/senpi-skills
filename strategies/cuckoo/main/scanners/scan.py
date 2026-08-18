"""CUCKOO — supervised scanner (Runtime 3.0 port of the v2 copy-the-copiers meta-follower).

Copy-the-Copiers / COHORT. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - auto-discovers the top `topN` strategies by realized performance
    (discovery_get_top_strategies) — READ-GUARDED, falls back to the last cached
    cohort if the discovery read fails/returns empty (never crashes the tick),
  - pulls each top strategy's current positions (leaderboard_get_trader_positions),
  - builds a PERFORMANCE-WEIGHTED consensus across (asset, direction) votes
    (pure `scoring.gather_entries` / `tally_consensus` / `consensus_score`),
  - emits the SINGLE highest weighted-consensus candidate that >= minStrategies
    top strategies agree on, at/above minScore (v2 main() emitted only `best`).

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns slots/cooldowns/risk gates, and trails the DSL
exit. No daemon, no push_signal, no create_position. Derived universe — the coins
come from the followed strategies' positions, not a fixed list.

FIDELITY NOTES vs cuckoo-producer.py v1.0.1:
  - v2 sized margin from marginPct=0.15 (a FRACTION) * account_value -> marginUsd.
    This port emits `marginPct` as a PERCENT in (0,100] (×100 -> 15) at the top
    level; the runtime sizes (marginPct/100)*withdrawable. Includes the defensive
    "<=1.0 means a pasted fraction, ×100" guard (dire/koala pattern). The 0.15
    sizing is otherwise identical.
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup
    map (same 4x-TTL prune + was_recently_signaled semantics).
  - COHORT->CACHE->DEGRADE: v2 returned a "WAITING" status and emitted nothing
    when discovery_get_top_strategies returned empty. This port instead falls
    back to the last cached strategy list in ctx.state (whalehunter/spider
    pattern), so a single transient discovery hiccup doesn't blank the consensus.
    If there is no cache yet, it degrades to [] exactly like v2's no-strategies path.
  - DROPPED v2 order-lifecycle/mutation: the v2 producer's `push_signal` (a POST
    via SenpiClient) and `record_signal` disk write are gone — scan() is read-only;
    the runtime owns signal intake/dedup/execution. Cross-tick dedup is kept in
    ctx.state. FLAGGED in the port report.
  - v2 fetched discovery_get_top_strategies(limit=top_n) and
    leaderboard_get_trader_positions(trader_id=wallet); both are reads, both
    READ-GUARDED here. The defensive multi-key shape unwrap is reproduced verbatim.
"""

import sys
import time

import scoring


# v2 producer defaults (cuckoo-producer.py / cuckoo-config.json)
_DEFAULT_TOP_N = 12              # how many top strategies to follow
_DEFAULT_MIN_STRATEGIES = 2     # require at least this many agreeing
_DEFAULT_MIN_NOTIONAL_USD = 2000
_DEFAULT_WEIGHT_CAP = 3.0       # max per-strategy weight (outlier guard)
_DEFAULT_HIGH_WEIGHT = 6.0      # aggregate weight that earns the bonus point
_DEFAULT_MIN_SCORE = 4
_DEFAULT_MARGIN_PCT = 15.0      # PERCENT of withdrawable (v2 0.15 fraction ×100)
_DEFAULT_LEVERAGE = 4
_MAX_LEVERAGE = 10              # v2 MAX_LEVERAGE (hardcoded clamp)
_DEFAULT_RECENT_TTL = 240       # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup
_CACHE_VERSION = 1             # bump if cohort-BUILDING logic changes (busts stale cache)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back
    the whole tick (per the contract, ANY exception rolls the tick back to []).
    Returns None on failure so the degrade paths apply (cohort -> cached; a failed
    per-strategy positions read -> that strategy contributes no votes this tick)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[cuckoo.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
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
            positions.append({"coin": pos.get("coin", ""),
                              "direction": "LONG" if szi > 0 else "SHORT"})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; running the held-asset dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[cuckoo.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _fetch_top_strategies(ctx, top_n):
    """[{wallet, roi}] for the top strategies by performance. READ-GUARDED.
    Defensive multi-key unwrap of the discovery response (verbatim from v2)."""
    raw = _read(ctx, "discovery_get_top_strategies", {"limit": top_n})
    if not raw:
        return []
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    items = d
    if isinstance(d, dict):
        items = d.get("strategies", d.get("top_strategies", d.get("results", [])))
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:top_n]:
        if not isinstance(it, dict):
            continue
        wallet = (
            it.get("strategyWalletAddress")
            or it.get("strategy_wallet")
            or it.get("wallet")
            or it.get("trader_id")
            or it.get("address")
        )
        if not wallet:
            continue
        roi = scoring.safe_float(
            it.get("roi", it.get("roe", it.get("totalPnlPct", it.get("totalPnl", 0))))
        )
        out.append({"wallet": str(wallet), "roi": roi})
    return out


def _fetch_strategy_positions(ctx, wallet):
    """Positions for one strategy, unwrapping the nested data.positions.positions
    shape (same as Remora/Spider). READ-GUARDED -> [] on failure."""
    raw = _read(ctx, "leaderboard_get_trader_positions", {"trader_id": wallet})
    if not raw:
        return []
    if not isinstance(raw, dict):
        return raw if isinstance(raw, list) else []
    d = raw.get("data", raw)
    if isinstance(d, list):
        return d
    if not isinstance(d, dict):
        return []
    rp = d.get("positions", d.get("top_positions", []))
    if isinstance(rp, list):
        return rp
    if isinstance(rp, dict):
        nested = rp.get("positions", [])
        return nested if isinstance(nested, list) else []
    return []


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(last):
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def _normalize_margin_pct(raw):
    """marginPct must be a PERCENT in (0,100]. A pasted v2 fraction (<=1.0) is
    converted ×100 (dire/koala defensive guard)."""
    mp = float(raw)
    if mp <= 1.0:
        mp = mp * 100.0
    return mp


def scan(inputs, ctx):
    now = time.time()
    top_n = int(inputs.get("topN", _DEFAULT_TOP_N))
    min_strategies = int(inputs.get("minStrategies", _DEFAULT_MIN_STRATEGIES))
    min_notional = float(inputs.get("minNotionalUsd", _DEFAULT_MIN_NOTIONAL_USD))
    cap = float(inputs.get("weightCap", _DEFAULT_WEIGHT_CAP))
    high_weight = float(inputs.get("highWeight", _DEFAULT_HIGH_WEIGHT))
    min_score = int(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    base_margin_pct = _normalize_margin_pct(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    last = (ctx.state.last() or {}) if ctx.state is not None else {}
    signaled = _prune_signaled(_load_signaled(last), ttl, now)
    cached_cohort = last.get("cohort", {}) if isinstance(last.get("cohort"), dict) else {}

    account_value, positions = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    def _persist(cohort):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"signaled": signaled, "cohort": cohort, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[cuckoo.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal or refetch the cohort: {exc!r}", file=sys.stderr)

    if account_value <= 0:
        result = {"ts": now, "emitted": False, "note": "no account value", "held": held_assets}
        print("[cuckoo.scan] WAITING — no account value", file=sys.stderr)
        _persist(cached_cohort)
        return []

    # ── auto-discover top strategies; COHORT->CACHE->DEGRADE ──
    strategies = _fetch_top_strategies(ctx, top_n)
    if strategies:
        cohort = {"refreshed_at": now, "cache_version": _CACHE_VERSION, "strategies": strategies}
    else:
        cohort = cached_cohort if cached_cohort.get("cache_version") == _CACHE_VERSION else {}
        strategies = cohort.get("strategies", [])
        if strategies:
            print(f"[cuckoo.scan] discovery empty — using cached cohort ({len(strategies)} strategies)",
                  file=sys.stderr)

    if not strategies:
        result = {"ts": now, "emitted": False,
                  "note": "WAITING — no top strategies (discovery empty, no cache)",
                  "held": held_assets}
        print("[cuckoo.scan] WAITING — discovery_get_top_strategies returned no strategies "
              "and no cohort cache", file=sys.stderr)
        _persist(cohort)
        return []

    # ── pull each strategy's positions (read-guarded), build weighted consensus ──
    positions_by_wallet = {s["wallet"]: _fetch_strategy_positions(ctx, s["wallet"])
                           for s in strategies}
    entries = scoring.gather_entries(strategies, positions_by_wallet, min_notional, cap)
    consensus = scoring.tally_consensus(entries)

    scored = []
    for cand in consensus.values():
        if cand["count"] < min_strategies:
            continue
        if cand["asset"].upper() in held_set:
            continue
        if _was_recently_signaled(signaled, cand["asset"], ttl, now):
            continue
        score = scoring.consensus_score(cand["count"], cand["weight"], high_weight)
        if score >= min_score:
            reasons = [
                f"top strategies are {cand['direction'].lower()} {cand['asset']}",
                f"{cand['count']} top strategies agree",
                f"combined vote weight {cand['weight']:.1f}",
            ]
            scored.append((score, reasons, cand))

    out = []
    if not scored:
        result = {"ts": now, "emitted": False,
                  "note": f"WAITING — no asset held by >= {min_strategies} top strategies",
                  "strategiesFollowed": len(strategies),
                  "candidatesSeen": len(consensus), "held": held_assets}
        print(f"[cuckoo.scan] WAITING — no >= {min_strategies}-strategy consensus; "
              f"strategies={len(strategies)} candidates={len(consensus)} held={held_assets}",
              file=sys.stderr)
        _persist(cohort)
        return out

    # v2 emitted exactly the single best: sort by (score, weight, count) desc.
    scored.sort(key=lambda t: (t[0], t[2]["weight"], t[2]["count"]), reverse=True)
    best_score, best_reasons, best = scored[0]

    signaled[best["asset"].upper()] = now
    result = {"ts": now, "emitted": True, "coin": best["asset"], "direction": best["direction"],
              "score": best_score, "strategyCount": best["count"],
              "consensusWeight": round(best["weight"], 2), "leverage": leverage,
              "marginPct": round(base_margin_pct, 4), "strategiesFollowed": len(strategies),
              "held": held_assets, "reasons": best_reasons}
    print(f"[cuckoo.scan] EMIT {best['asset']} {best['direction']} score={best_score} "
          f"{leverage}x marginPct={base_margin_pct:.2f}% count={best['count']} "
          f"weight={best['weight']:.1f}", file=sys.stderr)
    out = [{
        "asset": best["asset"],
        "direction": best["direction"],
        "marginPct": base_margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
        "leverage": leverage,                  # 1..10; runtime applies it
        "data": {
            "score": best_score,
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": best_reasons,
            "strategyCount": best["count"],
            "consensusWeight": round(best["weight"], 2),
            "strategiesFollowed": len(strategies),
            "heldAssets": held_assets,
        },
    }]

    _persist(cohort)
    return out
