"""ORCA — supervised scanner (Runtime 3.0 port of the v2 ORCA Gen-1 Vanilla Striker).

UNIVERSE scanner. Per tick: read the account + held set (clearinghouse, dual-DEX
equity via max()), fetch the top-100 smart-money leaderboard markets and slice to the
top-50 (XYZ banned), maintain a 5-scan rank history in ctx.state, score every eligible
market via the pure `scoring.score_market` (FIRST_JUMP / IMMEDIATE_MOVER + base Striker
points + contribution velocity / climbing), confirm volume (>=1.5x via market_get_asset_data)
and the 15m freshness gate, apply the held / per-asset-cooldown / emit-dedup filters,
pick the SINGLE strongest candidate that clears `minScore`, and emit ONE signal at the
fixed 7x leverage and conviction margin. The runtime sizes the dollars (marginPct
intent), owns slots/dedup/risk gates, and trails the DSL exit. Read-only + single-pass —
no daemon, no push_signal, no create_position.

Faithful port of the v2 producer `main()` + `detect_signals()` flow (orca-producer.py
v4.0.1). v2-quirks preserved and flagged. The v2 producer's wallet-isolated JSON state
files (scan-history.json / asset-cooldowns.json) collapse into ctx.state records here
(the runtime owns the per-wallet isolation + transactional rollback).

FIDELITY NOTES vs orca-producer.py v4.0.1:
  - v2 emitted exactly ONE signal (the single highest-scoring `best`). Preserved:
    scan() emits <= 1 signal/tick.
  - v2 MARGIN_PCT was the FRACTION 0.18 (margin_usd = account_value * 0.18). This port
    emits `marginPct` = 18 (PERCENT) at the top level; the runtime sizes
    (marginPct/100)*withdrawable. Same dollar size for the same account. The dire/koala
    defensive "<=1.0 means a pasted v2 fraction -> x100" guard is included.
  - v2 leverage is FIXED 7 (MIN_LEVERAGE==MAX_LEVERAGE==DEFAULT_LEVERAGE==7), then run
    through get_safe_leverage(wallet, asset, 7) = min(7, venue_max). Preserved verbatim:
    the scan reads strategy_get_asset_trading_limits and clamps the fixed 7 to the venue
    cap (read-guarded; degrades to 7 on any read failure, exactly as v2 did).
  - v2 per-asset cooldown was 120 min, persisted in asset-cooldowns.json keyed at OPEN
    time by the runtime/scanner-side opener. In Runtime 3.0 the scanner no longer opens
    positions, so it cannot stamp an open time. This port keeps an EMIT cooldown in
    ctx.state (an asset is suppressed for 120 min after it is emitted) as the closest
    faithful analogue, AND the runtime's own per_asset_cooldown_seconds (7200s) is the
    primary backstop. FLAGGED: emit-time vs open-time differ by the entry latency only.
  - v2 daily-entry caps / dynamic slot sizing (maxEntriesPerDay) lived in Python state
    files in v3.0 and were already declarative risk.guard_rails by v4.0.0; that migration
    is honored here (risk.guard_rails owns it; no Python daily counter). The v2 v4.0.0
    note explicitly calls the Python daily-counter state "vestigial."
  - v2 had NO order-lifecycle management (no cancel_order / resting-order purge), so
    section-4.2's "drop order lifecycle" rule has nothing to drop here. (Noted for the
    report.)
"""

import sys
import time
from datetime import datetime, timezone

import scoring

# v2 producer constants (defaults; overridable via inputs)
_DEFAULT_MIN_SCORE = scoring.STRIKER_MIN_SCORE        # 9
_DEFAULT_MARGIN_PCT = scoring.MARGIN_PCT              # 18 PERCENT (v2 0.18 fraction)
_DEFAULT_LEVERAGE = scoring.DEFAULT_LEVERAGE          # fixed 7
_DEFAULT_MAX_POSITIONS = 3                            # v2 MAX_POSITIONS
_DEFAULT_TOP_N = scoring.TOP_N                        # 50 — score only the top-50 SM markets
_DEFAULT_LEADERBOARD_LIMIT = 100                     # v2 fetch_markets limit=100
_DEFAULT_MIN_VOL_RATIO = scoring.STRIKER_MIN_VOL_RATIO  # 1.5 volume confirmation
_DEFAULT_PER_ASSET_COOLDOWN = 7200                   # v2 assetCooldownMinutes=120 -> 7200s
_DEFAULT_TTL = 7200                                  # signal-dedup TTL (mirror per-asset cooldown)
_SCAN_HISTORY_MAX = 5                                # v2 save_scan_history keeps last 5 scans
_XYZ_BANNED = True                                   # v2 XYZ_BANNED


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick (per the contract ANY exception zeroes all emits). Returns None on failure
    so the existing degrade paths apply (markets empty -> skip; volume read fails -> v2's
    'return 0, True' permissive default; trading-limits read fails -> keep fixed 7x)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[orca.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── ACCOUNT + HELD ASSETS (v2 cfg.get_positions, verbatim shape + read-sanity guard) ──

def _get_account(ctx):
    """(account_value, [position_dicts]). READ-GUARDED. Dual-DEX equity collapse:
    account_value via max() across main/xyz (two views of ONE cross-margined wallet —
    summing double-counts the shared free balance -> 2x sizing). assetPositions are
    per-sub-DEX so they're enumerated across both. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
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
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring.safe_float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", "")})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a corrupt
    # clearinghouse read can report margin/notional IN USE while returning an EMPTY
    # positions list; sizing or running the held-asset dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[orca.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── MARKET FETCHING (v2 fetch_markets + parse_scan, verbatim) ──

def _fetch_markets(ctx, limit, top_n, xyz_banned):
    """Top-N normalized SM markets (rank = list index+1). v2 parse_scan verbatim:
    XYZ banned by dex OR `xyz:` token prefix; slice to TOP_N after filtering."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return None
    markets_raw = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets_raw = data.get("markets", [])
            if isinstance(markets_raw, dict):
                markets_raw = markets_raw.get("markets", [])
        elif isinstance(data, list):
            markets_raw = data
    elif isinstance(raw, list):
        markets_raw = raw

    markets = []
    for i, m in enumerate(markets_raw):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = m.get("dex", "")
        if xyz_banned and (dex == "xyz" or token.lower().startswith("xyz:")):
            continue
        if not token:
            continue
        markets.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,                                   # v2: rank = pre-filter list index + 1
            "direction": str(m.get("direction", "")).upper(),
            "contribution": scoring.safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": scoring.safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": scoring.safe_float(m.get("token_price_change_pct_1h",
                                               m.get("price_change_1h", 0))),
            "cc_15m": scoring.safe_float(m.get("contribution_pct_change_15m", 0)),
        })
    return markets[:top_n]


def _snapshot_of(markets):
    """Compact per-scan snapshot stored in ctx.state (mirrors v2 scan-history entries)."""
    return [{"token": m["token"], "dex": m.get("dex", ""), "rank": m["rank"],
             "contribution": m["contribution"]} for m in markets]


def _find_in_snapshot(snapshot, token, dex):
    """v2 get_market_in_scan — find (token,dex) in a stored scan snapshot, or None."""
    for m in snapshot or []:
        if m.get("token") == token and m.get("dex", "") == dex:
            return m
    return None


def _check_asset_volume(ctx, token, dex, min_ratio):
    """v2 check_asset_volume — (ratio, strong). dayNtlVlm / prevDayNtlVlm via
    market_get_asset_data(1h). READ-GUARDED: v2 returns the PERMISSIVE (0, True) on any
    failure or missing prevDayNtlVlm (a missing volume reference never blocks a strong
    signal), so degrade to (0, True) here too."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": token, "candle_intervals": ["1h"], "include_funding": False,
        "dex": ("xyz" if str(dex).lower() == "xyz" else ""),
    })
    if not md:
        return 0, True
    ad = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(ad, dict):
        return 0, True
    ac = ad.get("asset_context", ad.get("assetContext", {}))
    if not isinstance(ac, dict):
        return 0, True
    vol = scoring.safe_float(ac.get("dayNtlVlm", 0))
    prev = scoring.safe_float(ac.get("prevDayNtlVlm", 0))
    if prev > 0:
        ratio = vol / prev
        return ratio, ratio >= min_ratio
    return 0, True


def _safe_leverage(ctx, asset, dex, requested):
    """v2 get_safe_leverage — clamp the fixed request to the venue max from
    strategy_get_asset_trading_limits. READ-GUARDED: degrade to `requested` on any failure
    (v2's `except: pass; return requested_leverage`)."""
    limits = _read(ctx, "strategy_get_asset_trading_limits",
                   {"strategy_wallet": ctx.wallet, "coin": asset})
    if not limits:
        return requested
    data = limits.get("data", limits) if isinstance(limits, dict) else limits
    if not isinstance(data, dict):
        return requested
    lev = data.get("leverage", {})
    try:
        if isinstance(lev, dict):
            max_lev = int(float(lev.get("value", requested)))
            return min(requested, max_lev)
        if isinstance(lev, (int, float)):
            return min(requested, int(lev))
    except (TypeError, ValueError):
        return requested
    return requested


def scan(inputs, ctx):
    now = time.time()
    hour_utc = datetime.now(timezone.utc).hour
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    max_positions = int(inputs.get("maxPositions", _DEFAULT_MAX_POSITIONS))
    top_n = int(inputs.get("topN", _DEFAULT_TOP_N))
    leaderboard_limit = int(inputs.get("leaderboardLimit", _DEFAULT_LEADERBOARD_LIMIT))
    leverage_default = int(inputs.get("leverageDefault", _DEFAULT_LEVERAGE))
    min_vol_ratio = float(inputs.get("minVolRatio", _DEFAULT_MIN_VOL_RATIO))
    xyz_banned = bool(inputs.get("xyzBanned", _XYZ_BANNED))
    per_asset_cooldown = float(inputs.get("perAssetCooldownSeconds", _DEFAULT_PER_ASSET_COOLDOWN))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1 (an
    # operator who pasted the v2 FRACTION 0.18) into a PERCENT so it never silently sizes
    # ~100x small (resolve-margin sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[orca.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    # ── prior state (rank-history window, emit cooldown / dedup maps) ──
    last = (ctx.state.last() or {}) if ctx.state else {}
    scan_history = list(last.get("scan_history") or [])     # [ [ {token,dex,rank,contribution}... ] ... ]
    emit_cooldowns = dict(last.get("emit_cooldowns") or {})  # {ASSET: ts} — per-asset suppression
    recent = dict(last.get("recent") or {})                  # {ASSET: ts} — signal-dedup

    def _persist(extra=None):
        if ctx.state is None:
            return
        rec = {
            "ts": now,
            "scan_history": scan_history[-_SCAN_HISTORY_MAX:],
            "emit_cooldowns": emit_cooldowns,
            "recent": recent,
        }
        if extra:
            rec.update(extra)
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[orca.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    # ── account state + held assets ──
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[orca.scan] cannot read account value (<=0); skip tick", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_account"}})
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    # ── max-positions guard (v2 MAX_POSITIONS=3) ──
    if len(positions) >= max_positions:
        print(f"[orca.scan] at max positions ({len(positions)}/{max_positions}): "
              f"{sorted(held_set)}", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "max_positions", "held": sorted(held_set)}})
        return []

    # ── fetch + parse top-N SM markets ──
    markets = _fetch_markets(ctx, leaderboard_limit, top_n, xyz_banned)
    if markets is None:
        print("[orca.scan] failed to fetch leaderboard_get_markets; skip tick", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_markets"}})
        return []

    # Build this scan's snapshot; need a prior scan to measure rank-jumps (v2:
    # detect_signals returns [] when history is empty — still record this scan).
    current_snapshot = _snapshot_of(markets)
    if not scan_history:
        scan_history.append(current_snapshot)
        print(f"[orca.scan] WAITING — seeding scan history (scanned={len(markets)}); "
              "need a prior scan to measure rank-jumps", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "history_seed", "scanned": len(markets)}})
        return []

    # v2: latest_prev = last scan; oldest_available = up to 5 scans back; prev_top50 set.
    latest_prev = scan_history[-1]
    oldest_available = scan_history[-min(len(scan_history), 5)]
    prev_top50_tokens = {(m["token"], m.get("dex", "")) for m in latest_prev}

    # ── score every eligible market (held / cooldown / dedup filtered as in v2 main) ──
    candidates = []
    scored = 0
    for market in markets:
        token = market["token"]
        dex = market.get("dex", "")

        prev_market = _find_in_snapshot(latest_prev, token, dex)
        old_market = _find_in_snapshot(oldest_available, token, dex)

        # contribution-velocity window: last <=5 scans of this (token,dex) + current.
        recent_contribs = []
        for snap in scan_history[-5:]:
            m = _find_in_snapshot(snap, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(market["contribution"])

        res = scoring.score_market(market, prev_market, old_market,
                                   prev_top50_tokens, recent_contribs, hour_utc)
        if res is None:
            continue
        score, reasons, meta = res
        scored += 1
        if score < min_score:
            continue

        # 15m velocity freshness gate (v2: `if cc_15m <= 0: continue`).
        if scoring.safe_float(market.get("cc_15m", 0)) <= 0:
            continue

        # held / per-asset emit-cooldown / signal-dedup filters (v2 main()).
        if token in held_set:
            continue
        ec = emit_cooldowns.get(token, 0)
        if ec and (now - ec) < per_asset_cooldown:
            continue
        rc = recent.get(token, 0)
        if rc and (now - rc) < ttl:
            continue

        # Volume confirmation (>=1.5x) — v2: scored gate first, THEN the per-asset volume
        # MCP read; only ran for candidates that already cleared the score floor.
        vol_ratio, vol_strong = _check_asset_volume(ctx, token, dex, min_vol_ratio)
        if not vol_strong:
            continue
        reasons = list(reasons) + [f"volume running {vol_ratio:.1f}x normal"]

        cand = dict(meta)
        cand["token"] = token
        cand["dex"] = dex if dex else None
        cand["direction"] = market["direction"]
        cand["score"] = score
        cand["reasons"] = reasons
        cand["volRatio"] = round(vol_ratio, 2)
        candidates.append(cand)

    # always record this scan into the rolling history (v2 always appended).
    scan_history.append(current_snapshot)
    scan_history = scan_history[-_SCAN_HISTORY_MAX:]

    if not candidates:
        print(f"[orca.scan] WAITING — no Striker signal (min score {min_score:.0f}); "
              f"scanned={len(markets)} scored={scored} held={sorted(held_set)}", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_candidate",
                             "scanned": len(markets), "scored": scored,
                             "held": sorted(held_set)}})
        return []

    # ── pick the single strongest candidate (v2 sorted by score desc, emits best) ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    # fixed 7x, clamped to the venue max (v2 get_safe_leverage on DEFAULT_LEVERAGE).
    leverage = _safe_leverage(ctx, best["token"], best.get("dex"), leverage_default)

    out = [{
        "asset": best["token"],
        "direction": best["direction"],
        "marginPct": margin_pct,               # SIZING INTENT (PERCENT) — runtime sizes the dollars
        "leverage": leverage,                  # fixed 7, venue-clamped; runtime applies
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "mode": best["mode"],
            "currentRank": int(best["currentRank"]),
            "rankJump": int(best["rankJump"]),
            "isFirstJump": bool(best["isFirstJump"]),
            "isContribExplosion": bool(best["isContribExplosion"]),
            "contribVelocity": float(best["contribVelocity"]),
            "volRatio": float(best["volRatio"]),
            "contribution": float(best["contribution"]),
            "traders": int(best["traders"]),
            "priceChg4h": float(best["priceChg4h"]),
            "reasons": " | ".join(best["reasons"]),
            "heldAssets": sorted(held_set),
        },
    }]

    # mark per-asset emit cooldown + signal-dedup for the emitted asset.
    emit_cooldowns[best["token"]] = now
    recent[best["token"]] = now
    print(f"[orca.scan] EMIT {best['token']} {best['direction']} score={best['score']} "
          f"{leverage}x marginPct={margin_pct:.2f}% | {' | '.join(best['reasons'][:6])}",
          file=sys.stderr)
    _persist({"result": {"emitted": True, "asset": best["token"], "direction": best["direction"],
                         "score": best["score"], "leverage": leverage,
                         "marginPct": round(margin_pct, 4), "rankJump": best["rankJump"],
                         "candidates": len(candidates), "scanned": len(markets),
                         "held": sorted(held_set)}})
    return out
