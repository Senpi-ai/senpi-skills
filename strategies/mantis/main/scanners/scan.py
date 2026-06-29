"""MANTIS — supervised scanner (Runtime 3.0 port of the v2 Mantis Slipstream).

CROSS-ASSET LAG / catchup hunter. Its SIGNATURE read is
`market_get_cross_asset_flows`: for each leader (BTC only — the only asset with
pre-computed lag data) the tool returns the leader's 4h move + a list of
correlated alts ("laggards") that historically follow but haven't moved yet, each
with PRE-COMPUTED scores (follow_rate, confidence, gap_pct, avg_lag_minutes,
lag_stddev_minutes, sm_starting_to_rotate). Per tick scan():
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - walks each leader's flows, filters laggards by the verbatim entry filters,
  - picks the highest-confidence non-held laggard,
  - emits ONE signal sized by the conviction tier (margin PERCENT + leverage),
    in the direction the leader moved.

Read-only + single-pass — emits a `marginPct` (PERCENT, conviction-tiered) +
`leverage` at the top level; the runtime sizes the dollars, owns cooldowns/risk
gates, and trails the DSL exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (mantis-producer.py v6.0.1 / mantis_config.py v6.0.0):
  - SIGNATURE READ: `market_get_cross_asset_flows(leader_asset=<leader>)`. The v2
    config calls it with kwarg `leader_asset`; ported verbatim as the args dict
    {"leader_asset": leader}. READ-GUARDED: any exception (tool absent during
    warmup, BTC-only lag data, transport error) degrades to "no candidates" and
    the tick emits nothing — never crashes the tick (per the contract, ANY
    exception rolls the whole tick to []). Warmup / empty-laggards is the tool's
    documented normal state and is handled as a clean WAITING.
  - DROPPED (v2 -> Runtime 3.0): the LEADER-REVERSAL VETO. v2 re-called the flow
    tool per open position and, when the leader had reversed >1% from entry,
    called `close_position` DIRECTLY (a producer-authoritative mutation the
    runtime "cannot express"). In Runtime 3.0 scan() is READ-ONLY:
    `close_position` raises PermissionError and would roll the whole tick to [].
    The veto MATH is preserved in scoring.leader_reversed (documented + testable)
    but is NOT executed here. The runtime's DSL exit (hard_timeout 240 ceiling +
    Phase1/Phase2 retrace ladder + weak_peak_cut) is the exit authority; the v2
    dynamic per-trade hard_timeout is surfaced in signal data.hardTimeoutMinutes
    for observability/future runtime consumption. FLAGGED in the port report.
  - DROPPED (v2 -> Runtime 3.0): position-metadata.json + entry-log.jsonl state
    files. position-metadata existed ONLY to feed the (now-dropped) veto pass;
    entry-log was observability only. Cross-tick dedup uses ctx.state instead.
  - SIZING: v2 build_strike computed marginUsd = account_value*(margin_pct/100).
    This port emits the conviction-tier margin PERCENT (75/50/25) directly; the
    runtime sizes (marginPct/100)*withdrawable. Tier cutoffs + leverage clamp +
    direction + dynamic hard_timeout are VERBATIM (see scoring.py).
  - DEDUP: v2 skipped any laggard already in open positions (no-double-up) +
    relied on runtime guard_rails per_asset_cooldown. Preserved: held-asset skip
    BEFORE emit + a ctx.state recent-signal TTL dedup (race-window guard).
  - v2 emitted exactly one strike per tick (highest confidence). Preserved:
    scan() emits <= 1 signal/tick.
"""

import sys
import time

import scoring


# v2 config defaults (mantis_config.py) — overridable via runtime inputs
_LEADER_UNIVERSE_DEFAULT = ["BTC"]   # only BTC has pre-computed lag data (v1 tool)
_DEFAULT_RECENT_TTL = 240            # race-window dedup (informational; runtime cooldown is authoritative)


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[mantis.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; running the held-asset dedup off that re-enters
    # held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[mantis.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_flows(ctx, leader):
    """Cross-asset flows for one leader. SIGNATURE READ. READ-GUARDED.

    Returns the unwrapped flow dict ({leader/leader_move_pct, laggards}) or None.
    None on: transport error, tool-absent-during-warmup, BTC-only lag data
    yielding an empty/None payload, or success:false. Degrades to None so the
    caller treats this leader as having no candidates — never crashes the tick."""
    try:
        raw = ctx.senpi_mcp.call_tool("market_get_cross_asset_flows",
                                      {"leader_asset": leader})
    except Exception as exc:  # noqa: BLE001 — warmup / BTC-only / transport: degrade, never crash
        print(f"[mantis.scan] cross_asset_flows({leader}) read failed "
              f"(degrade to no-candidates): {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    if isinstance(raw, dict) and raw.get("success") is False:
        return None
    return scoring.unwrap_flow_response(raw)


def _gather_candidates(ctx, inputs, leaders):
    """Walk each leader's flows; return filtered laggards (each tagged with its
    leader asset + leader move), sorted by confidence desc. Verbatim port of
    producer.gather_candidates. Each per-leader read is independently guarded."""
    candidates = []
    for leader in leaders:
        flow = _get_flows(ctx, leader)
        if not flow:
            print(f"[mantis.scan] no flow data for leader={leader} "
                  f"(warmup/empty/degraded)", file=sys.stderr)
            continue
        leader_move_pct = scoring.leader_move_from_flow(flow)
        laggards = flow.get("laggards", []) or []
        if not laggards:
            # empty laggards = leader hasn't moved enough; documented-normal state
            print(f"[mantis.scan] empty laggards for leader={leader} "
                  f"(move={leader_move_pct:+.2f}%)", file=sys.stderr)
            continue
        for laggard in laggards:
            if not isinstance(laggard, dict):
                continue
            if not scoring.passes_entry_filters(laggard, inputs):
                continue
            tagged = dict(laggard)
            tagged["_leader_asset"] = leader
            tagged["_leader_move_pct"] = leader_move_pct
            candidates.append(tagged)
    candidates.sort(key=lambda x: scoring._f(x.get("confidence")), reverse=True)
    return candidates


# ── ctx.state: recent-signal dedup (race-window guard) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    leaders = inputs.get("leaderUniverse", _LEADER_UNIVERSE_DEFAULT)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        # No account value (read degraded / read-sanity guard tripped / zero equity) -> hold.
        print("[mantis.scan] WAITING — no account value (read degraded or zero equity)",
              file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # SIGNATURE READ + filter (each per-leader flow read is independently guarded)
    candidates = _gather_candidates(ctx, inputs, leaders)

    out = []
    # walk by confidence; skip any already open OR recently signaled (no-double-up)
    pick = None
    for c in candidates:
        asset = (c.get("asset") or "").upper()
        if not asset:
            continue
        if asset in held_set:
            continue
        if _was_recently_signaled(signaled, asset, ttl, now):
            continue
        pick = c
        break

    if not pick:
        result = {"ts": now, "emitted": False, "candidates": len(candidates),
                  "held": held_assets, "leaders": list(leaders),
                  "note": "no_qualifying_laggards" if not candidates else "all_qualifying_held_or_signaled"}
        print(f"[mantis.scan] WAITING — no qualifying laggard "
              f"(candidates={len(candidates)} held={held_assets})", file=sys.stderr)
    else:
        strike = scoring.build_strike(pick, inputs)
        margin_pct = strike["margin_pct"]
        leverage = strike["leverage"]
        asset = strike["asset"]

        signaled[asset.upper()] = now
        result = {"ts": now, "emitted": True, "asset": asset,
                  "direction": strike["direction"], "confidence": strike["confidence"],
                  "leverage": leverage, "marginPct": round(margin_pct, 4),
                  "leaderAsset": strike["leader_asset"],
                  "leaderMovePct": strike["leader_move_pct"],
                  "gapPct": strike["gap_pct"], "held": held_assets}
        print(f"[mantis.scan] EMIT {asset} {strike['direction']} conf={strike['confidence']:.2f} "
              f"{leverage}x marginPct={margin_pct:.2f}% | leader {strike['leader_asset']} "
              f"{strike['leader_move_pct']:+.2f}% gap {strike['gap_pct']:+.2f}%", file=sys.stderr)
        out = [{
            "asset": asset,
            "direction": strike["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 5/7/8 by confidence tier; runtime applies it
            "data": {
                "score": strike["confidence"],          # 0..1 confidence is Mantis's score
                "leverage": leverage,
                "direction": strike["direction"],
                "hardTimeoutMinutes": strike["hard_timeout_minutes"],
                "gapPct": strike["gap_pct"],
                "followRate": strike["follow_rate"],
                "avgLagMinutes": strike["avg_lag_minutes"],
                "lagStddevMinutes": strike["lag_stddev_minutes"],
                "smStartingToRotate": strike["sm_starting_to_rotate"],
                "leaderAsset": strike["leader_asset"],
                "leaderMovePct": strike["leader_move_pct"],
                "heldAssets": held_assets,
                "reasons": strike["reasons"],
            },
        }]

    # persist dedup map + this tick's result every tick (bounded by
    # state_history_max_count); read back via ctx.state.last()/recent(n)
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[mantis.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
