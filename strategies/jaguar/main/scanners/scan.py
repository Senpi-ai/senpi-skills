"""JAGUAR — supervised scanner (Runtime 3.0 port of the v2 JAGUAR Striker producer).

Multi-asset, derived-universe rank-jump detector. Per tick it reads
`leaderboard_get_markets` (the live 4h smart-money concentration feed), builds a
compact scan snapshot, compares it against the previous snapshot(s) it stashed in
`ctx.state`, scores violent FIRST_JUMP signals via the pure
`scoring.detect_striker_signals`, and emits one conviction-tiered signal per
qualifying candidate (the runtime sizes the dollars, owns slots/dedup/cooldowns,
and trails the DSL exit). Read-only + single-pass — no daemon, no push_signal, no
create_position.

DERIVED UNIVERSE: there is NO fixed whitelist. The candidates are whatever the
smart-money leaderboard is concentrating into right now. xyz DEX assets are banned
in scoring (XYZ_BANNED) so the universe is main-DEX crypto only.

v4.0.1 ORDER-OF-OPERATIONS CONTRACT (ported verbatim, do NOT reorder): DETECT against
the prior history FIRST, THEN append the current snapshot. The v2 producer's silent-
scanner bug was appending current_scan to history BEFORE detecting, so prev_scans[-1]
returned the just-appended scan and every rank_jump computed as 0. Here the prior
history lives in ctx.state; we read it, detect, then append the current snapshot for
the next tick. The on-disk scan-history.json is replaced by ctx.state (bounded by
state_history_max_count)."""

import sys
import time
from datetime import datetime, timezone

import scoring


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply (no markets
    this tick → emit nothing, keep prior history; held/account reads → empty/zero)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[jaguar.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _fetch_markets(ctx, limit):
    """leaderboard_get_markets → flat list of market dicts (unwrap the v2 shapes)."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return None
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else None


def _fetch_held_assets(ctx, wallet):
    """Current open-position coins on the strategy wallet (defence-in-depth dedup;
    the runtime's per_asset_cooldown + held-position check are the real owners)."""
    if not wallet:
        return []
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    if not ch:
        return []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return []
    held = []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
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
    limit = int(inputs.get("marketLimit", 100))
    min_score = int(inputs.get("minScore", scoring.MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", 50))      # PERCENT of withdrawable (0,100], not a fraction
    tiers = inputs.get("leverageTiers", scoring.DEFAULT_LEVERAGE_TIERS)
    max_history = int(inputs.get("scanHistoryLen", 60))  # mirrors v2 MAX_SCAN_HISTORY
    now_iso = _now_iso()

    # ── prior scan history from ctx.state (replaces on-disk scan-history.json) ──
    prev_scans = []
    if ctx.state is not None:
        for rec in ctx.state.recent(max_history):
            snap = rec.get("scan") if isinstance(rec, dict) else None
            if snap:
                prev_scans.append(snap)

    # ── READ live SM markets ──
    markets = _fetch_markets(ctx, limit)
    if markets is None:
        print("[jaguar.scan] no markets this tick — holding (keep prior history)", file=sys.stderr)
        return []

    current_scan = scoring.build_scan_snapshot(markets, now_iso)

    # First-ever scan? Seed history and return — nothing to compare against yet.
    if not prev_scans:
        if ctx.state is not None:
            try:
                ctx.state.append({"scan": current_scan})
            except Exception as exc:  # noqa: BLE001
                print(f"[jaguar.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        print(f"[jaguar.scan] seeded history (need 2+ scans) — scanned {len(markets)}", file=sys.stderr)
        return []

    # ── DETECT against the prior history FIRST (v4.0.1 contract), THEN append below ──
    candidates = scoring.detect_striker_signals(current_scan, prev_scans)

    # Append current snapshot AFTER detection so the next tick has a real prior.
    if ctx.state is not None:
        try:
            ctx.state.append({"scan": current_scan})
        except Exception as exc:  # noqa: BLE001
            print(f"[jaguar.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if not candidates:
        print(f"[jaguar.scan] scanned {len(markets)}, 0 candidates (min_score={min_score})", file=sys.stderr)
        return []

    held = {h.upper() for h in _fetch_held_assets(ctx, ctx.wallet)}

    out = []
    for c in candidates:
        if c["score"] < min_score:                 # defensive: scoring already floors at MIN_SCORE
            continue
        if c["token"].upper() in held:             # skip assets we already hold
            continue
        leverage, tier_label = scoring.get_leverage_for_score(c["score"], tiers)
        out.append({
            "asset": c["token"],
            "direction": c["direction"],
            "marginPct": margin_pct,               # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,                  # conviction-tiered (7/10); runtime clamps to venue max
            # raw wire score preserved at v2 denominator (score/14); data{}.score carries the points
            "data": {
                "score": c["score"],
                "tier": tier_label,
                "leverage": leverage,
                "marginPct": margin_pct,
                "reasons": c["reasons"],
                "mode": "STRIKER",
                "direction": c["direction"],
                "currentRank": c["currentRank"],
                "rankJump": c["rankJump"],
                "isFirstJump": c["isFirstJump"],
                "contribVelocity": c["contribVelocity"],
                "contrib15m": c["contrib15m"],
                "contrib1h": c["contrib1h"],
                "volRatio": c["volRatio"],
                "smPct": c["contribution"],
                "smTraders": c["traders"],
                "priceChange4hPct": c["priceChg4h"],
                "dayNotionalUsd": c["dayNotionalUsd"],
            },
        })

    if out:
        best = candidates[0]
        print(f"[jaguar.scan] EMIT {len(out)}/{len(candidates)} — best score={best['score']} "
              f"{best['direction']} {best['token']} (rankJump {best['rankJump']})", file=sys.stderr)
    return out
