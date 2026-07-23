"""MEERKAT — supervised scanner (Runtime 3.0 port of the v2 Meerkat momentum-event sniper).

EVENT-DRIVEN scanner. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - pulls the Senpi momentum-event feed (leaderboard_get_momentum_events — the
    4h rolling-window momentum / rank-jump events),
  - for each event: classifies |momentum| into a TIER (1/2/3), measures FRESHNESS
    (minutes since it fired), extracts direction, fetches per-asset smart-money
    lean (leaderboard_get_markets) + rising-1h-volume (market_get_asset_data),
  - scores via the pure `scoring.build_thesis`, drops held / recently-signaled,
  - emits the SINGLE best fresh tier>=minTier event clearing `minScore`, in the
    momentum direction.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a `leverage`;
the runtime sizes the dollars, owns cooldowns/slots/risk gates, and trails the DSL
exit. No daemon, no push_signal, no create_position, no order-lifecycle.

The universe is DYNAMIC — there is no fixed ticker list; Meerkat trades whatever
the momentum-event feed surfaces in the current 4h window (crypto majors + alts).
So there is nothing to validate against the Hyperliquid meta at author time.

FIDELITY NOTES vs the v2 producer (meerkat-producer.py v1.0.1):
  - v2 sized margin as `account_value * marginPct` with marginPct a FRACTION
    (config marginPct=0.15) -> marginUsd. This port emits `marginPct` as a
    PERCENT in (0,100] and the runtime sizes (marginPct/100)*withdrawable. The
    v2 config FRACTION 0.15 is converted ×100 -> 15 (PERCENT). A defensive guard
    treats any value <= 1.0 as a pasted fraction and ×100 it. NOTE: the v2
    runtime.yaml declared margin_pct: 20 which CONTRADICTS the producer/config
    0.15 (=15%); per the source-of-truth order (producer + config win) this port
    uses 15%. FLAGGED in the report.
  - v2 emitted exactly one signal (best, sorted by score then tier then |mag|).
    Preserved: scan() emits <= 1 signal/tick, identical sort key.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup
    map with the same 240s TTL + 4×TTL prune window.
  - v2's `_wrapper_client.push_signal` POST is dropped (the runtime ingests the
    returned dict). v2 never called any mutation tool (close/cancel) — DSL owns
    exits — so there is NO order-lifecycle behaviour to drop.
  - leverage: v2 clamped `min(config.leverage, MAX_LEVERAGE=10)` (default 4).
    Preserved verbatim (no MIN clamp in v2).
"""

import sys
import time

import scoring

def _sm_row_matches(row, token, target):
    """True if leaderboard row `row` is the market for `target`.

    `leaderboard_get_markets` returns BARE tickers (`NVDA`) plus a separate `dex`
    field, while our universe carries the qualified name (`xyz:NVDA`). A raw
    `token != target` compare therefore NEVER matches an xyz name, so every xyz
    instrument reads as "no smart-money data" and a hard SM gate blocks it
    permanently. Compare bare tickers, and require the dex to agree so a main-DEX
    name cannot cross-match its xyz twin (e.g. main `GOLD` vs `xyz:GOLD`)."""
    tok = str(token or "").upper()
    want = str(target or "").upper()
    if tok.split(":", 1)[-1] != want.split(":", 1)[-1]:
        return False
    row_xyz = (str((row or {}).get("dex", "")).strip().lower() == "xyz"
               or tok.startswith("XYZ:"))
    return row_xyz == want.startswith("XYZ:")


# v2 producer constants (defaults; overridable via inputs)
_DEFAULT_MIN_TIER = 2               # v2 DEFAULT_MIN_TIER — snipe tier 2+
_DEFAULT_TIER2_MIN_PCT = 5.0        # v2 DEFAULT_TIER2_MIN_PCT
_DEFAULT_TIER3_MIN_PCT = 10.0       # v2 DEFAULT_TIER3_MIN_PCT
_DEFAULT_MAX_EVENT_AGE_MIN = 30.0   # v2 DEFAULT_MAX_EVENT_AGE_MIN — freshness gate
_DEFAULT_SM_TILT_MIN = 55           # v2 DEFAULT_SM_TILT_MIN
_DEFAULT_SM_STRONG = 70             # v2 DEFAULT_SM_STRONG
_DEFAULT_MIN_SCORE = 4              # v2 DEFAULT_MIN_SCORE (producer floor)
_DEFAULT_MARGIN_PCT = 15.0         # v2 config marginPct 0.15 (FRACTION) ×100 -> 15 PERCENT
_DEFAULT_LEVERAGE = 4              # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 10                # v2 MAX_LEVERAGE (hardcoded clamp)
_DEFAULT_RECENT_TTL = 240        # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick. Returns None on failure so the degrade paths apply
    (events empty -> WAITING; sm -> neutral; volume -> not-rising)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[meerkat.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── ACCOUNT + HELD ASSETS (port of v2 cfg.get_positions, verbatim shape) ──

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
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
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring.safe_float(pos.get("marginUsed", 0))})

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
        print("[meerkat.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── MOMENTUM-EVENT FEED (port of v2 fetch_momentum_events, verbatim unwrap) ──

def _fetch_momentum_events(ctx):
    raw = _read(ctx, "leaderboard_get_momentum_events", {})
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    d = raw.get("data", raw)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        ev = d.get("events", d.get("momentum_events", d.get("results", [])))
        return ev if isinstance(ev, list) else []
    return []


# ── SMART-MONEY LEAN (port of v2 fetch_sm_direction, verbatim) ──

def _get_sm_direction(ctx, asset):
    """Net smart-money lean for `asset` from leaderboard_get_markets. Returns
    (direction, pct) or (None, 0). READ-GUARDED. Verbatim v2 thresholds:
    long_ratio >= 50 -> LONG else SHORT; tilt is the dominant-side ratio."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, asset):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring.safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ── RISING 1h VOLUME (port of v2 fetch_volume_rising; MCP fetch here, math pure) ──

def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _volume_rising(ctx, asset):
    data = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": _dex_for(asset),
    })
    if not data or (isinstance(data, dict) and not data.get("success", True)):
        return False
    d = data.get("data", data) if isinstance(data, dict) else {}
    candles = (d.get("candles", {}) or {}).get("1h", []) if isinstance(d, dict) else []
    return scoring.volume_rising(candles)


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


def _resolve_margin_pct(raw):
    """v2 config margin was a FRACTION (0.15). Emit PERCENT in (0,100].
    Defensive: any value <= 1.0 is a pasted fraction -> ×100 (dire/koala guard)."""
    mp = float(raw)
    if mp <= 1.0:
        mp *= 100.0
    return mp


def scan(inputs, ctx):
    now = time.time()
    min_tier = int(inputs.get("minTier", _DEFAULT_MIN_TIER))
    tier2_min = float(inputs.get("tier2MinPct", _DEFAULT_TIER2_MIN_PCT))
    tier3_min = float(inputs.get("tier3MinPct", _DEFAULT_TIER3_MIN_PCT))
    max_age = float(inputs.get("maxEventAgeMinutes", _DEFAULT_MAX_EVENT_AGE_MIN))
    sm_tilt_min = float(inputs.get("smTiltMinPct", _DEFAULT_SM_TILT_MIN))
    sm_strong = float(inputs.get("smStrongTiltPct", _DEFAULT_SM_STRONG))
    min_score = int(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = _resolve_margin_pct(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))  # PERCENT (0,100]
    leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # event-classification config consumed by scoring.build_thesis
    th_config = {
        "minTier": min_tier, "tier2MinPct": tier2_min, "tier3MinPct": tier3_min,
        "maxEventAgeMinutes": max_age, "smTiltMinPct": sm_tilt_min, "smStrongTiltPct": sm_strong,
    }

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[meerkat.scan] no account value; skip tick", file=sys.stderr)
        result = {"ts": now, "emitted": False, "gate": "no_account"}
        _persist(ctx, {}, result)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    events = _fetch_momentum_events(ctx)
    if not events:
        result = {"ts": now, "emitted": False, "gate": "no_events", "held": held_assets}
        print(f"[meerkat.scan] WAITING — no momentum events in the feed; held={held_assets}",
              file=sys.stderr)
        _persist(ctx, signaled, result)
        return []

    # ── score every fresh tier>=minTier event (held + recently-signaled filtered
    #    BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    for event in events:
        asset = scoring.event_asset(event)
        if not asset or asset.upper() in held_set or _was_recently_signaled(signaled, asset, ttl, now):
            continue
        # cheap structural gate (tier + freshness + direction) BEFORE the SM/vol
        # reads — match v2: build_thesis returns None early when the event can't
        # clear, but the SM/vol fetches are needed for the score bonuses. We do
        # the two reads then score (v2 fetched them inside build_thesis).
        sm = _get_sm_direction(ctx, asset)
        vol_rising = _volume_rising(ctx, asset)
        th = scoring.build_thesis(event, th_config, now, sm, vol_rising)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "emitted": False, "gate": "no_candidate",
                  "events_seen": len(events), "held": held_assets}
        print(f"[meerkat.scan] WAITING — no fresh tier>={min_tier} event cleared minScore "
              f"{min_score}; events_seen={len(events)} held={held_assets}", file=sys.stderr)
    else:
        # v2: highest score, tie-break by tier then |magnitude|.
        candidates.sort(key=lambda c: (c["score"], c["tier"], abs(c["magnitude_pct"])), reverse=True)
        best = candidates[0]
        signaled[best["coin"].upper()] = now
        result = {"ts": now, "emitted": True, "coin": best["coin"], "direction": best["direction"],
                  "tier": best["tier"], "magnitudePct": best["magnitude_pct"],
                  "ageMin": best["age_min"], "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "candidates": len(candidates),
                  "events_seen": len(events), "held": held_assets, "reasons": best["reasons"]}
        print(f"[meerkat.scan] EMIT {best['coin']} {best['direction']} tier={best['tier']} "
              f"mag={best['magnitude_pct']:+.1f}% score={best['score']} {leverage}x "
              f"marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..10; runtime applies + clamps to venue max
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "tier": best["tier"],
                "magnitudePct": best.get("magnitude_pct") or 0.0,
                "ageMin": best.get("age_min") if best.get("age_min") is not None else 0.0,
                "smDirection": best.get("sm_direction") or "NONE",
                "smTiltPct": best.get("sm_tilt_pct") or 0.0,
                "volRising": bool(best.get("vol_rising")),
                "heldAssets": held_assets,
            },
        }]

    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    """Append dedup map + this tick's result every tick; bounded by
    state_history_max_count. Read back via ctx.state.recent(n)."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[meerkat.scan] WARNING: state append failed; next tick may re-emit "
              f"a suppressed signal: {exc!r}", file=sys.stderr)
