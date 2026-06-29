"""REMORA — supervised scanner (Runtime 3.0 port of the v2 Remora whale mirror).

TRADER-FOLLOWER archetype: mirrors a small, hand-picked set of whale traders
(NOT a leaderboard universe). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - for each configured whale, pulls their open positions
    (leaderboard_get_trader_positions — the producer signature) and takes their
    single largest-notional position above minNotionalUsd,
  - optionally validates whale quality (discovery_get_trader_state ELITE /
    RELIABLE / PROFITABLE tier) as a scoring bonus,
  - aggregates across whales into (asset, direction) candidates (consensus is the
    edge multiplier), scores via the pure `scoring` module, and emits the SINGLE
    highest-scoring candidate at/above minScore (v2 main() emitted only `best`),
    sized by Remora's OWN marginPct + leverage clamp.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates/dedup, and trails the
DSL exit. No daemon, no push_signal, no create_position.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped try/except -> degrade (skip
that whale / neutral tier / skip the tick), NEVER propagate. Per the scan
contract ANY exception rolls the whole tick back to [], so one bad whale read
would silently kill all emits — guarded so a single flaky whale just drops out.

FIDELITY NOTES vs the v2 producer (remora-producer.py v1.0.1):
  - v2 stored margin as a FRACTION (config.marginPct = 0.15) and multiplied by
    account_value to get a marginUsd, then emitted marginUsd. This port emits
    `marginPct` (PERCENT in (0,100]) at the top level; the runtime sizes
    (marginPct/100)*withdrawable. The default 0.15 -> 15 PERCENT. A defensive
    "<=1.0 means a pasted fraction, x100" guard preserves either input form.
  - v2 emitted exactly one signal (best, highest score, tie-break by consensus
    count then max_notional). Preserved: scan() emits <= 1 signal/tick with the
    SAME sort key.
  - v2's recent-signals.json race-window dedup cache -> ctx.state dedup map (same
    TTL semantics: TTL=240s, prune at 4x TTL). The on-chain held-asset filter is
    preserved verbatim (held names are skipped before scoring).
  - v2 leverage clamp `min(int(config.leverage), MAX_LEVERAGE)` preserved.
  - v2 read-sanity guard (margin in use + empty positions -> skip tick) ported
    verbatim from cfg.get_positions.
  - DROPPED: nothing — the v2 producer is enter-only (no cancel_order /
    has_resting_orders / stale-order purge to drop). The DSL owns exits; a
    whale-exit mirror remains a future enhancement (as in v2).
  - No fixed universe to validate against HL meta: Remora has NO whitelist — the
    assets it mirrors are whatever the configured whales hold, resolved live each
    tick. (config.whales is the operator's hand-picked trader set, default empty.)
"""

import sys
import time

import scoring


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Defensive — only matters if a whale holds an xyz: position."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[remora.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[remora.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _fetch_whale_positions(ctx, trader_id):
    """List of position dicts for one whale (leaderboard_get_trader_positions),
    unwrapping the nested data.positions.positions shape. READ-GUARDED -> []
    on any failure (the whale just drops out of this tick). Verbatim unwrap from
    v2 fetch_whale_positions."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_trader_positions",
                                      {"trader_id": trader_id})
    except Exception as exc:  # noqa: BLE001 — one flaky whale must not kill the tick
        print(f"[remora.scan] leaderboard_get_trader_positions({str(trader_id)[:10]}) "
              f"read failed (whale skipped): {exc!r}", file=sys.stderr)
        return []
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
    if isinstance(rp, dict):  # nested one level deeper (observed shape)
        nested = rp.get("positions", [])
        return nested if isinstance(nested, list) else []
    return []


def _fetch_whale_tier(ctx, trader_id):
    """ELITE / RELIABLE / etc. for one whale, or None if unavailable.
    READ-GUARDED -> None (quality bonus simply not awarded). Verbatim parse from
    v2 fetch_whale_tier."""
    try:
        raw = ctx.senpi_mcp.call_tool("discovery_get_trader_state",
                                      {"trader_id": trader_id})
    except Exception as exc:  # noqa: BLE001 — quality is a bonus; never crash the tick
        print(f"[remora.scan] discovery_get_trader_state({str(trader_id)[:10]}) "
              f"read failed (tier -> none): {exc!r}", file=sys.stderr)
        return None
    if not raw or not isinstance(raw, dict):
        return None
    d = raw.get("data", raw)
    if not isinstance(d, dict):
        return None
    tier = d.get("tier", d.get("classification", d.get("rating")))
    return str(tier).upper() if tier else None


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
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


def _normalize_whale_id(whale):
    """A whale entry can be a dict {trader_id|wallet} or a bare string (v2
    gather_candidates accepted both)."""
    if isinstance(whale, dict):
        return whale.get("trader_id") or whale.get("wallet") or ""
    return whale or ""


def scan(inputs, ctx):
    now = time.time()
    whales_cfg = inputs.get("whales", [])
    use_tier = bool(inputs.get("useWhaleQuality", True))
    min_notional = float(inputs.get("minNotionalUsd", scoring.DEFAULT_MIN_NOTIONAL_USD))
    min_score = int(inputs.get("minScore", scoring.DEFAULT_MIN_SCORE))
    leverage = min(int(inputs.get("leverage", scoring.DEFAULT_LEVERAGE)), scoring.MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", 240))

    # marginPct intent (PERCENT in (0,100]). v2 stored a FRACTION (0.15); the
    # defensive guard converts a pasted fraction (<=1.0) to a percent (x100).
    margin_pct = float(inputs.get("marginPct", 15))
    if margin_pct <= 1.0:
        margin_pct *= 100.0

    if not whales_cfg:
        print("[remora.scan] WAITING — no whales configured (set inputs.whales)", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"signaled": {}, "result": {"ts": now, "emitted": False,
                                                             "note": "no_whales_configured"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[remora.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── per-whale: fetch positions, take the top, validate tier (READ-GUARDED) ──
    whale_tops = []
    for whale in whales_cfg:
        trader_id = _normalize_whale_id(whale)
        if not trader_id:
            continue
        whale_positions = _fetch_whale_positions(ctx, trader_id)
        top = scoring.top_position(whale_positions, min_notional)
        if not top:
            continue
        tier = _fetch_whale_tier(ctx, trader_id) if use_tier else None
        whale_tops.append((trader_id, top, tier))

    # ── aggregate into (asset, direction) candidates + score (pure) ──
    candidates = scoring.aggregate_candidates(whale_tops, use_tier)
    scored = []
    for cand in candidates:
        if cand["asset"].upper() in held_set:                 # on-chain held-asset filter
            continue
        if _was_recently_signaled(signaled, cand["asset"], ttl, now):
            continue
        score, reasons = scoring.score_candidate(cand)
        if score >= min_score:
            scored.append((score, reasons, cand))

    out = []
    if not scored:
        result = {"ts": now, "emitted": False, "whales_tracked": len(whales_cfg),
                  "candidates_seen": len(candidates), "held": held_assets,
                  "note": f"WAITING (min score {min_score})"}
        print(f"[remora.scan] WAITING — no qualifying whale position to mirror "
              f"(min score {min_score}); whales={len(whales_cfg)} "
              f"candidates={len(candidates)} held={held_assets}", file=sys.stderr)
    else:
        # v2 sort: highest score, tie-break by consensus count then max_notional.
        scored.sort(key=lambda t: (t[0], t[2]["count"], t[2]["max_notional"]), reverse=True)
        best_score, best_reasons, best = scored[0]

        signaled[best["asset"].upper()] = now
        result = {"ts": now, "emitted": True, "coin": best["asset"],
                  "direction": best["direction"], "score": best_score,
                  "whaleCount": best["count"], "maxNotionalUsd": round(best["max_notional"], 2),
                  "eliteTier": bool(best.get("quality")), "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best_reasons}
        print(f"[remora.scan] EMIT {best['asset']} {best['direction']} score={best_score} "
              f"whales={best['count']} maxNotional=${best['max_notional']:,.0f} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best_reasons[:5]}", file=sys.stderr)
        out = [{
            "asset": best["asset"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # clamped to <= MAX_LEVERAGE; runtime applies it
            "data": {
                "score": best_score,
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best_reasons,
                "whaleCount": best["count"],
                "maxNotionalUsd": round(best["max_notional"], 2),
                "eliteTier": bool(best.get("quality")),
                "whales": best.get("whales", []),
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[remora.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
