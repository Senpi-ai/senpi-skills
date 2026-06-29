"""SAILFISH — supervised scanner (Runtime 3.0 port of the v2 Sailfish RS rotator).

Relative-Strength Rotator on crypto majors (BTC/ETH/SOL/HYPE by default). Per tick:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - fetches 4h candles per whitelist asset and computes RS = % change over
    rsLookbackBars bars (pure `scoring.relative_strength`),
  - ranks the universe and picks the leader iff it clears BOTH gates
    (leader RS >= minLeaderRsPct AND margin-vs-runner-up >= leaderMarginPct),
  - skips if the leader is already held (single-position; producer never closes),
    or recently signaled (race-window dedup),
  - emits the SINGLE leader at/above `minScore`, sized by a fixed margin PERCENT.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position. "Rotation" is realized by the DSL
trail exiting a stalled leader and this scan re-entering the new leader next tick.

FIDELITY NOTES vs the v2 producer (sailfish-producer.py v1.0.1):
  - v2 sized `marginUsd = account_value * config.marginPct` where marginPct was a
    FRACTION (0.20). This port emits a top-level `marginPct` PERCENT (20) and lets
    the runtime size `(marginPct/100)*withdrawable` (resolve-margin.ts). The
    FRACTION->PERCENT conversion (0.20 -> 20) is the only sizing change; the
    economic intent (20% of equity per slot) is identical.
  - v2 wire `score` was normalized to `min(score/6.0, 1.0)` for push_signal. In
    Runtime 3.0 the scaffold owns the wire envelope; we emit the RAW integer score
    on data{} (per scan-contract.md) and gate on it in scan.py.
  - v2's LLM gate (runtime.yaml decision_prompt) was pass-through (honor the
    signal; hard-skip on direction!=LONG / score<4 / lev<=0 / held / leaderRs<=0).
    Those hard-skips are ALREADY enforced here (LONG-only, score>=minScore floor,
    held-asset filter, leader_rs>=minLeaderRsPct gate), so the entry action is a
    RULE action (no LLM). Behaviour is identical to the pass-through gate.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240, prune at 4xTTL) ->
    ctx.state dedup map with the SAME TTL semantics.
  - v2 ranked the WHOLE whitelist (including held), then skipped if the leader was
    held. Preserved: ranking includes held assets so the margin-vs-runner-up
    comparison uses the true runner-up; the held check is applied to the LEADER
    only, exactly as in v2 main().
  - v2 main() emitted exactly one signal (the leader). Preserved: scan() emits <=1.
"""

import sys
import time

import scoring


# v1.0.x defaults (sailfish-producer.py / sailfish-config.json)
_DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_RS_LOOKBACK = 16          # 16 x 4h bars = ~2.7 days
_DEFAULT_MIN_LEADER_RS_PCT = 1.0   # leader's own RS must be >= this %
_DEFAULT_LEADER_MARGIN_PCT = 1.5   # leader must beat the runner-up by this much
_DEFAULT_MIN_SCORE = 4             # v2 DEFAULT_MIN_SCORE
_DEFAULT_MARGIN_PCT = 20.0         # PERCENT in (0,100] — v2 fraction 0.20 x100
_DEFAULT_LEVERAGE = 3              # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 5                  # v2 MAX_LEVERAGE (hardcoded clamp)
_DEFAULT_RECENT_TTL = 240          # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Sailfish's default universe is all main-DEX majors, so this returns '' in
    practice — kept for parity if an operator adds an XYZ asset to the whitelist."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


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
        print(f"[sailfish.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[sailfish.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _fetch_closes(ctx, asset):
    """4h close list for `asset` or [] on any read failure. READ-GUARDED.
    Ported from v2 fetch_candles -> closes (market_get_asset_data, 4h only)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(asset),
        })
    except Exception as exc:  # noqa: BLE001 — one asset failing must not crash the tick
        print(f"[sailfish.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return []
    if not md:
        return []
    if isinstance(md, dict) and md.get("success") is False:
        return []
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return []
    candles = (d.get("candles", {}) or {}).get("4h", []) or []
    if not isinstance(candles, list):
        return []
    return [scoring._close(c) for c in candles]


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


def scan(inputs, ctx):
    now = time.time()
    whitelist = inputs.get("whitelist", _DEFAULT_WHITELIST)
    lookback = int(inputs.get("rsLookbackBars", _DEFAULT_RS_LOOKBACK))
    min_leader_rs = float(inputs.get("minLeaderRsPct", _DEFAULT_MIN_LEADER_RS_PCT))
    margin_pct_threshold = float(inputs.get("leaderMarginPct", _DEFAULT_LEADER_MARGIN_PCT))
    min_score = int(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT in (0,100]
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── compute RS per whitelist asset (held INCLUDED in ranking so the
    #    margin-vs-runner-up comparison uses the true runner-up, as in v2) ──
    strength_by_asset = {}
    scanned = 0
    for asset in whitelist:
        if not asset:
            continue
        closes = _fetch_closes(ctx, asset)
        if len(closes) <= lookback:
            continue
        scanned += 1
        strength_by_asset[asset.upper()] = scoring.relative_strength(closes, lookback)

    ranked = scoring.rank_assets(strength_by_asset)
    picked = scoring.leader_above_runner_up(ranked, min_leader_rs, margin_pct_threshold)

    out = []
    note = None
    leader_asset = None
    score = None
    if picked is None:
        note = "WAITING — no clear leader (insufficient RS or margin)"
    else:
        leader_asset, leader_rs, margin_vs_runner = picked
        if leader_asset in held_set:
            note = f"HOLDING — {leader_asset} is the leader and already held"
        elif _was_recently_signaled(signaled, leader_asset, ttl, now):
            note = f"WAITING — {leader_asset} leader but recently-signaled (race-window dedup)"
        else:
            score, reasons = scoring.build_score(
                leader_asset, leader_rs, margin_vs_runner, bool(held_assets))
            if score < min_score:
                note = f"WAITING — {leader_asset} cleared gates but score {score} < min {min_score}"
            else:
                margin_finite = margin_vs_runner if margin_vs_runner != float('inf') else 0.0
                signaled[leader_asset.upper()] = now
                out = [{
                    "asset": leader_asset,
                    "direction": "LONG",                       # Sailfish is LONG-only
                    "marginPct": margin_pct,                   # PERCENT in (0,100] — runtime sizes the dollars
                    "leverage": leverage,                      # 1..5; runtime applies it
                    "data": {
                        "score": score,
                        "leverage": leverage,
                        "direction": "LONG",
                        "reasons": reasons,
                        "leaderRsPct": float(leader_rs),
                        "leaderMarginPct": float(margin_finite),
                        "rankSnapshot": [{"asset": a, "rs": round(s, 2)} for a, s in ranked[:6]],
                        "heldAssets": held_assets,
                    },
                }]

    # ── observability: one-line stderr + per-tick result append (guarded) ──
    if out:
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": leader_asset, "direction": "LONG", "score": score,
                  "leverage": leverage, "marginPct": round(margin_pct, 4),
                  "ranked": [{"asset": a, "rs": round(s, 2)} for a, s in ranked],
                  "held": held_assets}
        print(f"[sailfish.scan] EMIT {leader_asset} LONG score={score} {leverage}x "
              f"marginPct={margin_pct:.2f}% | ranked={[(a, round(s, 2)) for a, s in ranked[:4]]} "
              f"held={held_assets}", file=sys.stderr)
    else:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "note": note,
                  "ranked": [{"asset": a, "rs": round(s, 2)} for a, s in ranked],
                  "held": held_assets}
        print(f"[sailfish.scan] {note}; scanned={scanned} "
              f"ranked={[(a, round(s, 2)) for a, s in ranked[:4]]} held={held_assets}",
              file=sys.stderr)

    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[sailfish.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
