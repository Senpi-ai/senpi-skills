"""PELICAN — supervised scanner (Orca's Gen-1 Vanilla Striker detector, conviction-sized).

UNIVERSE scanner. Per tick: read the account + held set (clearinghouse, dual-DEX
equity via max()), fetch the top-100 smart-money leaderboard markets and slice to the
top-50 (XYZ **admitted** — the one difference from Penguin), maintain a 5-scan rank history
in ctx.state, score every eligible
market via the pure `scoring.score_market` (FIRST_JUMP / IMMEDIATE_MOVER + base Striker
points + contribution velocity / climbing), confirm volume (>=1.5x via market_get_asset_data)
and the 15m freshness gate, apply the held / per-asset-cooldown / emit-dedup filters,
pick the SINGLE strongest candidate that clears `minScore`, and emit ONE signal at the
configured leverage and margin — runtime.yaml ships 10x on 90% margin. The runtime sizes
the dollars (marginPct intent), owns slots/dedup/risk gates, and trails the DSL exit.
Read-only + single-pass — no daemon, no push_signal, no create_position.

Faithful port of the v2 producer `main()` + `detect_signals()` flow (orca-producer.py
v4.0.1). v2-quirks preserved and flagged. The v2 producer's wallet-isolated JSON state
files (scan-history.json / asset-cooldowns.json) collapse into ctx.state records here
(the runtime owns the per-wallet isolation + transactional rollback).

FIDELITY NOTES vs orca-producer.py v4.0.1:
  - v2 emitted exactly ONE signal (the single highest-scoring `best`). Preserved:
    scan() emits <= 1 signal/tick.
  - SIZING IS WHERE PELICAN DEPARTS. v2/Orca sized at MARGIN_PCT 0.18 and a FIXED 7x;
    Pelican ships marginPct 90 and leverageDefault 10 in runtime.yaml (~9x equity exposure
    against Orca's ~1.26x). The runtime resolves a per-signal `marginPct` BEFORE config
    `strategy.margin_pct`, so the scanner inputs — not the strategy block — are what size
    the trade. The module defaults below mirror the shipped values so a missing `inputs:`
    cannot silently revert this template to Orca's sizing. marginPct is a PERCENT; the
    dire/koala defensive "<=1.0 means a pasted v2 fraction -> x100" guard is included.
  - Leverage is still run through get_safe_leverage(wallet, asset, N) = min(N, venue_max)
    against strategy_get_asset_trading_limits (read-guarded; degrades to the default on a
    read failure, exactly as v2 did) — at N=10 rather than v2's 7. NOTE: with a single
    slot the venue clamp decides the whole trade, so a name the venue caps at 2x is traded
    at 2x. That is the live configuration's behaviour and is preserved deliberately; the
    detector and the universe are Orca's, unfiltered.
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

import json
import sys
import time
from datetime import datetime, timezone

import scoring

# v2 producer constants (defaults; overridable via inputs)
_DEFAULT_MIN_LEVERAGE = 0    # 0 = accept any venue cap (warn only); raise to the ladder's leverage
_DEFAULT_MIN_SCORE = scoring.STRIKER_MIN_SCORE        # 9
_DEFAULT_MARGIN_PCT = 90.0                            # PERCENT — mirrors runtime.yaml (Orca: 18)
_DEFAULT_LEVERAGE = 10                                # mirrors runtime.yaml (Orca: fixed 7)
_DEFAULT_MAX_POSITIONS = 1                            # ONE strike at a time (Pelican: Orca ran 3)
_DEFAULT_TOP_N = scoring.TOP_N                        # 50 — score only the top-50 SM markets
_DEFAULT_LEADERBOARD_LIMIT = 100                     # v2 fetch_markets limit=100
_DEFAULT_MIN_VOL_RATIO = scoring.STRIKER_MIN_VOL_RATIO  # 1.5 volume confirmation
_DEFAULT_PER_ASSET_COOLDOWN = 7200                   # v2 assetCooldownMinutes=120 -> 7200s
_DEFAULT_TTL = 7200                                  # signal-dedup TTL (mirror per-asset cooldown)
# Concentrating into ONE position makes the quality of that one name matter far more than it does
# across three. Two filters keep the single slot off the names that punish size:
_SCAN_HISTORY_MAX = 5                                # v2 save_scan_history keeps last 5 scans
_XYZ_BANNED = False                                  # Pelican admits xyz; Orca/Penguin ban it
                                                     # (v2 XYZ_BANNED). Mirrors runtime.yaml so a
                                                     # dropped `inputs:` cannot silently revert this
                                                     # package to Penguin's crypto-only universe.


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick (per the contract ANY exception zeroes all emits). Returns None on failure
    so the existing degrade paths apply (markets empty -> skip; volume read fails -> v2's
    'return 0, True' permissive default; trading-limits read fails -> keep fixed 7x)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[pelican.scan] {name} read failed: {exc!r}", file=sys.stderr)
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
        print("[pelican.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── MARKET FETCHING (v2 fetch_markets + parse_scan, verbatim) ──

def _fetch_markets(ctx, limit, top_n, xyz_banned, min_traders=10):
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
        if int(m.get("trader_count", 0) or 0) < min_traders:   # a thin side never enters the rank order
            continue
        # a micro-cap cannot absorb one concentrated strike without moving against it
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
    """v2 check_asset_volume — (ratio, strong): 24h notional volume over the PRIOR 24h,
    confirmed at >= min_ratio.

    FIXED 2026-09-24. v2 read this as `dayNtlVlm / prevDayNtlVlm` off the asset context,
    but HL's asset context has NO `prevDayNtlVlm`. Its eleven keys are coin, dayBaseVlm,
    dayNtlVlm, funding, impactPxs, markPx, midPx, openInterest, oraclePx, premium and
    prevDayPx — the previous-day field carries a PRICE, not a volume. So `prev` was always
    0, the ratio branch never ran, and this returned the permissive (0, True) on EVERY
    call: the gate was dead from launch while still printing VOL_CONFIRMED downstream.

    Restored by computing both days from the 1h candles the same read already returns
    (~169 of them, 7 days), notional per candle as volume x close — which is the quantity
    the two v2 field names named. No extra MCP call.

    READ-GUARDED: still degrades to the PERMISSIVE (0, True) when the read fails or fewer
    than 48 candles come back, preserving v2's documented "a missing volume reference never
    blocks a strong signal". That guard was always meant for a failed read; it is no longer
    the only path through the function."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": token, "candle_intervals": ["1h"], "include_funding": False,
        "dex": ("xyz" if str(dex).lower() == "xyz" else ""),
    })
    if not md:
        return 0, True
    ad = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(ad, dict):
        return 0, True
    candles = ad.get("candles") or {}
    series = candles.get("1h") if isinstance(candles, dict) else None
    if not isinstance(series, list) or len(series) < 48:
        return 0, True

    def _ntl(c):
        # candle o/h/l/c/v arrive as STRINGS; v is base volume, so x close = notional
        return scoring.safe_float(c.get("v", 0)) * scoring.safe_float(c.get("c", 0))

    cur = sum(_ntl(c) for c in series[-24:])
    prev = sum(_ntl(c) for c in series[-48:-24])
    if prev > 0:
        ratio = cur / prev
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

def _pick_leverable(ctx, candidates, min_leverage, requested_for, strict=False):
    """Walk the ranking and take the first name the venue will actually lever.

    WHY THIS EXISTS. Every DSL threshold is ROE, and the engine converts ROE to a price floor by
    DIVIDING BY LEVERAGE (senpi-strategy-author/references/dsl-configuration.md). So a ladder
    authored at Nx silently stretches every exit distance in PRICE by N/actual when the venue caps
    an asset below N. A 10x ladder on a 5x-capped name arms its first rung at twice the intended
    price move and may put its later rungs out of reach entirely — the position rides a ladder that
    was never calibrated for it, and nothing in the config says so.

    `minLeverage: 0` (the default) keeps the previous behaviour exactly — the first candidate always
    clears it — and only adds the warning. Raise it to the leverage the ladder was authored for to
    make the scanner prefer a lower-scoring name the ladder actually fits.

    `strict` decides what happens when NOTHING clears the floor. Default False falls back to the
    top candidate with a warning, because a scanner that goes silent on every clamped tick stops
    being the strategy the user deployed. True emits nothing instead — correct for a package whose
    ladder is calibrated for one leverage band and would be materially wrong outside it.

    Returns (candidate, venue_clamped_leverage), or (None, None) when strict and nothing clears.
    """
    fallback = None
    for rank, cand in enumerate(candidates):
        want = requested_for(cand)
        lev = _safe_leverage(ctx, cand['token'], cand.get("dex"), want)
        if fallback is None:
            fallback = (cand, lev, want)
        if lev >= min_leverage:
            if rank:
                print(f"[pelican.scan] LEVERAGE_SKIP passed over {rank} higher-scoring name(s) the venue "
                      f"caps below minLeverage={min_leverage}x", file=sys.stderr)
            if lev < want:
                print(f"[pelican.scan] LEVERAGE_CLAMP {cand['token']} {lev}x vs {want}x authored — "
                      f"every ROE threshold is {want / lev:.1f}x its intended price move "
                      f"(ladder calibrated for {want}x)", file=sys.stderr)
            return cand, lev
    if strict:
        cand, lev, want = fallback
        print(f"[pelican.scan] LEVERAGE_FLOOR_UNMET_STRICT no candidate reaches minLeverage="
              f"{min_leverage}x (best was {cand['token']} at {lev}x) — emitting nothing rather than "
              f"running a ladder calibrated for {want}x on it", file=sys.stderr)
        return None, None
    cand, lev, want = fallback
    print(f"[pelican.scan] LEVERAGE_FLOOR_UNMET no candidate reaches minLeverage={min_leverage}x; taking "
          f"{cand['token']} at {lev}x vs {want}x authored — every ROE threshold is "
          f"{want / lev:.1f}x its intended price move", file=sys.stderr)
    return cand, lev


def scan(inputs, ctx):
    now = time.time()
    hour_utc = datetime.now(timezone.utc).hour
    min_leverage = float(inputs.get("minLeverage", _DEFAULT_MIN_LEVERAGE))
    min_leverage_strict = bool(inputs.get("minLeverageStrict", False))
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
        print(f"[pelican.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    # ── prior state (rank-history window, emit cooldown / dedup maps) ──
    last = (ctx.state.last() or {}) if ctx.state else {}
    scan_history = list(last.get("scan_history") or [])     # [ [ {token,dex,rank,contribution}... ] ... ]
    emit_cooldowns = dict(last.get("emit_cooldowns") or {})  # {ASSET: ts} — per-asset suppression
    recent = dict(last.get("recent") or {})                  # {ASSET: ts} — signal-dedup
    history_ts = float(last.get("history_ts") or 0)          # when scan_history[-1] was recorded

    def _persist(extra=None):
        if ctx.state is None:
            return
        rec = {
            "ts": now,
            "scan_history": scan_history[-_SCAN_HISTORY_MAX:],
            "history_ts": history_ts,
            "emit_cooldowns": emit_cooldowns,
            "recent": recent,
        }
        if extra:
            rec.update(extra)
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[pelican.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    # ── account state + held assets ──
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[pelican.scan] cannot read account value (<=0); skip tick", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_account"}})
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    # ── max-positions guard (v2 MAX_POSITIONS=3) ──
    if len(positions) >= max_positions:
        print(f"[pelican.scan] at max positions ({len(positions)}/{max_positions}): "
              f"{sorted(held_set)}", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "max_positions", "held": sorted(held_set)}})
        return []

    # ── fetch + parse top-N SM markets ──
    markets = _fetch_markets(ctx, leaderboard_limit, top_n, xyz_banned,
                             int(inputs.get("minTraderCount", 10)))
    if markets is None:
        print("[pelican.scan] failed to fetch leaderboard_get_markets; skip tick", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_markets"}})
        return []

    # Build this scan's snapshot; need a prior scan to measure rank-jumps (v2:
    # detect_signals returns [] when history is empty — still record this scan).
    current_snapshot = _snapshot_of(markets)
    # Rank jumps are only measured between CONSECUTIVE scans. The early returns above (max
    # positions, no account, no markets) record no snapshot, so after a held position
    # scan_history[-1] can be an hour old and a slow climb reads as an IMMEDIATE_MOVER.
    # More than one missed tick = the history is stale: drop it and re-seed.
    interval = float(getattr(ctx, "interval_seconds", 0) or 0)
    if interval and now - history_ts > 2.5 * interval:
        scan_history = []
    if not scan_history:
        scan_history.append(current_snapshot)
        history_ts = now
        print(f"[pelican.scan] WAITING — seeding scan history (scanned={len(markets)}); "
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
    near_miss = 0
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

        # floor=False returns candidates that cleared the HARD gates even when they miss the
        # score/reason floor, so the near-miss band can be logged. The floor itself is enforced
        # below, unchanged — meta["passedFloor"] is the identical condition, precomputed.
        res = scoring.score_market(market, prev_market, old_market,
                                   prev_top50_tokens, recent_contribs, hour_utc,
                                   floor=False)
        if res is None:
            continue
        score, reasons, meta = res

        # ── CAND: one structured line per candidate that cleared the HARD gates ──
        # The score/reason floor used to live INSIDE score_market and return None, so a
        # near-miss left no trace anywhere but the `scored=N` count on the WAITING line. The
        # fleet therefore has no data at all on the 7-8 band: 11 sub-9 emits in 14 days, all
        # from one wallet running a lowered floor. That makes three questions undecidable
        # without risking capital — is the 7-8 band worth trading, does a finer score separate
        # 9.45 from 9.01, and should the 4h alignment window be 1h or 2h. All three are
        # answerable offline from this line plus the forward price tape.
        #
        # Cheap because the hard gates run first: jump >= 15, prev_rank >= 25 and the 4h
        # alignment kill nearly everything, so this is ~1-3 lines per 90s tick, not 50.
        #
        # NOTE volRatio is absent by construction — the volume MCP read runs AFTER the floor
        # and only for passers, so any study of the sub-floor band is pre-volume-confirmation.
        print(f"[pelican.scan] CAND " + json.dumps({
            "token": token, "dex": dex or None, "direction": market["direction"],
            "score": score, "scoreFine": meta["scoreFine"], "passed": meta["passedFloor"],
            "rankJump": meta["rankJump"], "prevRank": meta["prevRank"],
            "currentRank": meta["currentRank"], "contribRatio": meta["contribRatio"],
            "contribVelocity": meta["contribVelocity"], "totalClimb": meta["totalClimb"],
            "priceChg1h": meta["priceChg1h"], "priceChg4h": meta["priceChg4h"],
            "traders": meta["traders"], "reasons": reasons,
        }, separators=(",", ":")), file=sys.stderr)

        # THE FLOOR. `minScore` from runtime.yaml is now authoritative: it used to be shadowed
        # by the identical hardcoded 9 inside score_market, so setting it lower had no effect at
        # all. It defaults to scoring.STRIKER_MIN_SCORE, and the templates ship 9, so every wallet
        # on the shipped config is unaffected — but a package that wants a different floor can now
        # actually have one (see purple-penguin). The reasons floor is still the module's.
        # `scored` keeps its old meaning — candidates PAST the floor — so the WAITING line stays
        # comparable with every tick already in the tape.
        if score < min_score or len(reasons) < scoring.STRIKER_MIN_REASONS:
            near_miss += 1
            continue
        scored += 1

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
        # ratio 0 with strong=True is the FAIL-OPEN path (read failed, or <48 candles) — say so.
        # "VOL_CONFIRMED 0.0x" is the exact string the dead gate emitted 982 times; a degraded read
        # must never again be indistinguishable from a real confirmation in the tape.
        reasons = list(reasons) + [f"VOL_CONFIRMED {vol_ratio:.1f}x" if vol_ratio > 0
                                   else "VOL_UNVERIFIED (volume read degraded)"]

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
    history_ts = now
    scan_history = scan_history[-_SCAN_HISTORY_MAX:]

    if not candidates:
        print(f"[pelican.scan] WAITING — no Striker signal (min score {min_score:.0f}); "
              f"scanned={len(markets)} scored={scored} near_miss={near_miss} "
              f"held={sorted(held_set)}", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_candidate",
                             "scanned": len(markets), "scored": scored, "nearMiss": near_miss,
                             "held": sorted(held_set)}})
        return []

    # ── pick the single strongest candidate that the venue will actually lever ──
    # With one slot the leverage clamp decides the whole trade: a top-scoring name the venue caps
    # at 2x turns a 10x thesis into a different strategy. Walks the ranking and takes the first that
    # clears minLeverage; falls back to the top name with a warning rather than skipping the tick.
    # (Until 2026-10-01 this comment described a walk the code did not do — it took candidates[0]
    # and clamped it silently. _pick_leverable now implements it.)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best, leverage = _pick_leverable(ctx, candidates, min_leverage,
                                     lambda _c: leverage_default, strict=min_leverage_strict)

    if best is None:
        return []

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
    print(f"[pelican.scan] EMIT {best['token']} {best['direction']} score={best['score']} "
          f"{leverage}x marginPct={margin_pct:.2f}% | {' | '.join(best['reasons'][:6])}",
          file=sys.stderr)
    _persist({"result": {"emitted": True, "asset": best["token"], "direction": best["direction"],
                         "score": best["score"], "leverage": leverage,
                         "marginPct": round(margin_pct, 4), "rankJump": best["rankJump"],
                         "candidates": len(candidates), "scanned": len(markets),
                         "held": sorted(held_set)}})
    return out
