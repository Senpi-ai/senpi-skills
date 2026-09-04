"""VULTURE — supervised scanner (Runtime 3.0 port of the v2 Long-Tail Momentum Rider).

Multi-asset. Reads smart-money positioning across the whole market via
`leaderboard_get_markets`, scores each whitelist row through the pure
`scoring.score_market` (LONG_TAIL_MOMENTUM: SM-concentration tier + 4h momentum +
15m velocity + 1h acceleration + 4h continuation + trader depth + regime + funding
persistence), and emits EVERY gated candidate — the runtime applies `slots`, owns
the cooldowns/risk gates, and trails the DSL exit. No daemon, no push_signal.

Faithful port of vulture-producer.py v4.2.0:
  - MIN_SCORE 10 (the v4.2.0 producer floor — culled 9->10 after a 100-trade analysis;
    the task brief said "9" but PRODUCER thresholds win, copied verbatim — see runtime.yaml note)
  - conviction-scaled leverage 5x (score 10) / 7x (score >=11)
  - FP-001 quiet hours (00-04 UTC; apex score >=11 bypasses)
  - whitelist of 27 small/mid-cap perps; BTC/ETH/SOL + XYZ banned

Every per-asset / optional MCP read is read-guarded: a transient or permission error
on ONE enrichment fetch degrades that one asset (or the whole-tick shared context) to
neutral; it never propagates and rolls back the whole tick (scan-contract.md: any
unhandled exception in a tick rolls the WHOLE return back to [])."""

import sys
import time

import scoring

_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (anti re-fire)
# Conviction-scaled leverage — small caps capped at 7x (low-liq slippage).
# Ported verbatim from v2 SIZING_TIERS (v4.2.0). # v2-quirk: floor tier is score 10.
_DEFAULT_TIERS = [
    {"min_score": 11, "leverage": 7, "label": "apex"},        # score 11-12: the right tail
    {"min_score": 10, "leverage": 5, "label": "conviction"},  # score 10: the floor tier (best bucket)
]


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply (regime ->
    None, persistence -> None, held -> [])."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[vulture.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _markets(ctx):
    """leaderboard_get_markets -> flat list of market rows (or [] on failure)."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": 100})
    if not raw:
        return []
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else []


def _funding_regime(ctx):
    """market_get_funding_regime -> regime string or None (degrade to neutral)."""
    fr = _read(ctx, "market_get_funding_regime", {})
    if not fr:
        return None
    data = fr.get("data", fr) if isinstance(fr, dict) else fr
    if isinstance(data, dict):
        return data.get("regime")
    return None


def _funding_persistence(ctx, asset):
    """market_get_funding_history -> persistence_hours float or None."""
    fh = _read(ctx, "market_get_funding_history", {"asset": asset})
    if not fh:
        return None
    data = fh.get("data", fh) if isinstance(fh, dict) else fh
    if isinstance(data, dict):
        ph = data.get("persistence_hours")
        try:
            return float(ph) if ph is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _held_assets(ctx):
    """Current open positions on the strategy wallet -> list of coin strings.
    Defence-in-depth: skip any asset already held (the runtime's slot/dedup would
    reject it anyway). Degrades to [] on any read failure."""
    if not ctx.wallet:
        return []
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    held = []
    if not isinstance(data, dict):
        return []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held.append(coin)
    return held


def scan(inputs, ctx):
    whitelist = inputs.get("whitelist", scoring.DEFAULT_WHITELIST)
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    margin_pct = float(inputs.get("marginPct", 45))    # PERCENT of withdrawable (0,100], not a fraction
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()
    hour = time.gmtime(now).tm_hour

    quiet, apex_bypass = scoring.in_quiet_hours(hour, inputs)

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[vulture.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    markets = _markets(ctx)
    if not markets:
        print("[vulture.scan] HOLD: no markets from leaderboard_get_markets", file=sys.stderr)
        _persist({"ts": now, "scanned": 0, "candidates": 0, "emitted": 0, "gate": "no_markets"})
        return []

    # Whole-tick shared context — enriched once, each read degrades to neutral.
    regime = _funding_regime(ctx)
    held_assets = _held_assets(ctx)
    held_upper = {h.upper() for h in held_assets}
    wl_upper = {t.upper() for t in whitelist}

    # Lazy per-asset persistence: only fetch for whitelist rows actually present.
    persistence_map = {}

    candidates = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        token_raw = str(m.get("token", "")).upper()
        if token_raw in scoring.BANNED_ASSETS:
            continue
        if token_raw not in wl_upper:
            continue
        matched = None
        for tracked in whitelist:
            if token_raw == tracked.upper():
                matched = tracked
                break
        if matched is None:
            continue
        if matched not in persistence_map:
            persistence_map[matched] = _funding_persistence(ctx, matched)

        c = scoring.score_market(m, regime, persistence_map, whitelist, tiers, inputs)
        if c is not None:
            candidates.append(c)

    if not candidates:
        print(f"[vulture.scan] HOLD: scanned={len(markets)} no candidates >= floor | regime={regime}",
              file=sys.stderr)
        _persist({"ts": now, "scanned": len(markets), "candidates": 0, "emitted": 0,
                  "gate": "no_candidates", "regime": regime})
        return []

    # Highest conviction first.
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best_score = candidates[0]["score"]

    # FP-001 quiet hours: block sub-apex during the window.
    if quiet and best_score < apex_bypass:
        print(f"[vulture.scan] HOLD: QUIET_HOURS hour={hour}_UTC best={best_score} < apex {apex_bypass}",
              file=sys.stderr)
        _persist({"ts": now, "scanned": len(markets), "candidates": len(candidates), "emitted": 0,
                  "gate": "quiet_hours", "best_score": best_score, "regime": regime})
        return []

    out = []
    for c in candidates:
        cu = c["asset"].upper()
        if cu in held_upper:                              # already holding — skip
            continue
        if recent.get(cu) is not None and (now - recent[cu]) < ttl:   # signal-dedup
            continue
        out.append({
            "asset": c["asset"],
            "direction": c["direction"],
            "marginPct": margin_pct,          # SIZING INTENT (PERCENT) — runtime sizes the dollars
            "leverage": c["leverage"],        # conviction-tiered (5/7); runtime applies it
            # `required: false` in signal_data_schema permits an ABSENT key, never a present-and-null:
            # the intake discards the WHOLE candidate on a null optional. Drop them here rather than
            # coercing to 0/"" — a coerced value would assert a measurement that was never taken.
            "data": {k: v for k, v in {
                "score": c["score"],
                "tier": c["tier_label"],
                "leverage": c["leverage"],
                "direction": c["direction"],
                "reasons": c["reasons"],
                "smPct": c["sm_pct"],
                "smTraders": c["sm_traders"],
                "priceChange4hPct": c["p4h"],
                "priceChange1hPct": c["p1h"],
                "priceChange15mPct": c["p15m"],
                "contribChange15m": c["c15m"],
                "contribChange1h": c["c1h"],
                "contribChange4h": c["c4h"],
                "fundingRegime": c["regime"],
                "persistenceHours": c["persistence_hours"],
            }.items() if v is not None},
        })
        recent[cu] = now

    print(f"[vulture.scan] EMIT {len(out)}/{len(candidates)} | scanned={len(markets)} "
          f"best={best_score} regime={regime} | "
          f"{[(o['asset'], o['direction'], o['data']['score'], o['leverage']) for o in out]}",
          file=sys.stderr)
    _persist({"ts": now, "scanned": len(markets), "candidates": len(candidates),
              "emitted": len(out), "gate": "emit", "best_score": best_score, "regime": regime,
              "assets": [o["asset"] for o in out]})
    return out
