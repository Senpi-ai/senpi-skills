"""PANGOLIN — supervised scanner (Runtime 3.0 port of the v2 Pangolin funding-fade producer).

Multi-asset universe scanner. Per tick:
  1. Build the live universe from market_list_instruments — main-DEX perps with OI > minOiUsd
     (DERIVED, never a hardcoded list; names not in the live read are dropped).
  2. Fetch the market funding regime once (market_get_funding_regime).
  3. Fetch the smart-money map once (leaderboard_get_markets).
  4. For each universe asset that clears the |funding| floor and regime-confirms-or-neutral
     gate, read its hourly funding rows (market_get_asset_data funding_history) and derive
     persistence + trend at the minFundingRate floor (scoring.funding_history_stats).
  5. Score VERBATIM via scoring.score_candidate (funding extremity + persistence + trend +
     regime + smart-money + sticky OI + price-reversal).
  6. Apply hard gates (score >= minScore, persistence >= minPersistenceHours), the quiet-hours
     gate, and cross-tick dedup (post-EMIT cooldown + post-CLOSE thrash guard).
  7. Emit the TOP candidate (v2 emit-one-per-tick behavior — the runtime owns slots + cooldowns).

Read-only + single-pass. EVERY ctx.senpi_mcp.call_tool is wrapped in _read() so a transient or
permission error degrades gracefully (universe falls back to [], a failed hourly-funding read skips
that one candidate) and NEVER rolls back the whole tick. No daemon, no push_signal — the runtime
sizes (marginPct/100 * withdrawable), executes, owns cooldowns/risk gates, and trails the DSL exit.

FIDELITY NOTE — the quiet-hours gate (00-04 UTC) is a TASK-SPEC requirement that is NOT present
in the v2 producer source (the v2 producer has no time-of-day logic at all). It is implemented here
as a hard pre-emit gate using the kodiak clock-passed idiom (scoring.in_quiet_hours), clearly marked,
so a fidelity harness diff surfaces it. All funding-persistence + exhaustion + fade SCORING is ported
verbatim in scoring.py; only this one gate is additive.

FIDELITY NOTE — minOiUsd defaults to 10_000_000. The task spec said OI > $3M and the v2 producer floor
was 1_000_000; with persistence read correctly, thin names cleared the gates, so the floor rose. It is
operator-tunable via inputs.minOiUsd.
"""

import sys
import time

import scoring

_DEFAULT_RECENT_TTL = 14400        # 240m post-EMIT cooldown (v1 ASSET_COOLDOWN_MINUTES)
_DEFAULT_POST_CLOSE_TTL = 14400    # 240m post-CLOSE cooldown (v2.1.2 thrash-cycle guard)
_DEFAULT_TIERS = [[13, 5], [9, 3]]


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the whole
    tick. Returns None on failure so the existing degrade paths apply (universe -> [], a failed
    regime/hourly-funding read -> treated as unavailable)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[pangolin.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _is_xyz(name):
    # v2 banned XYZ; instruments carry no `dex` field, so the xyz: name prefix is the
    # reliable discriminator (matches the live market_list_instruments shape + spider's _dex_for).
    return str(name).lower().startswith("xyz:")


def _build_universe(ctx, inputs):
    """DERIVED universe: every live main-DEX instrument with OI(USD) > minOiUsd, each carrying
    its context block (funding/OI/markPx/volume). One market_list_instruments read.
    Read-guarded — returns [] on failure (the tick then emits nothing, not a crash)."""
    min_oi_usd = float(inputs.get("minOiUsd", 10_000_000))
    raw = _read(ctx, "market_list_instruments", {"dex": inputs.get("dex", "")})
    if not raw:
        return []
    instruments = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return []

    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = str(inst.get("name", inst.get("coin", ""))).upper()
        if not name:
            continue
        if _is_xyz(name):                                  # v2-quirk: XYZ banned (no funding thesis on equities)
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = scoring._f(ctx_block.get("openInterest", inst.get("openInterest", 0)))
        mark_px = scoring._f(ctx_block.get("markPx", ctx_block.get("midPx",
                             inst.get("markPx", inst.get("midPx", 0)))))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if oi_usd < min_oi_usd:                            # v1.3 floor — ensures liquidity (task: >$3M)
            continue
        volume_24h = scoring._f(ctx_block.get("dayNtlVlm", inst.get("dayNtlVlm",
                                inst.get("volume24h", 0))))
        universe.append({"name": name, "ctx": ctx_block, "volume_24h": volume_24h})
    return universe


def _get_regime(ctx):
    """Market funding regime once per scan. Read-guarded — None on failure/unavailable (treated
    as neutral by regime_confirms_fade, which is the v2 fail-open behavior)."""
    r = _read(ctx, "market_get_funding_regime", {})
    if not r:
        return None
    data = r.get("data", r) if isinstance(r, dict) else r
    return data.get("regime") if isinstance(data, dict) else None


def _get_sm_map(ctx, min_traders=10):
    """{COIN: market_row} of the smart-money lean per asset (leaderboard_get_markets, limit=100).
    Read-guarded — {} on failure (smart-money scoring then contributes 0, never crashes)."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": 100})
    sm_map = {}
    if not raw:
        return sm_map
    sm_markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(sm_markets, dict):
        sm_markets = sm_markets.get("markets", sm_markets)
    if isinstance(sm_markets, dict):
        sm_markets = sm_markets.get("markets", [])
    if isinstance(sm_markets, list):
        for m in sm_markets:
            if not isinstance(m, dict):
                continue
            token = str(m.get("token", "")).upper()
            dex = str(m.get("dex", "")).lower()
            # LONG and SHORT are separate rows per token: keep the dominant side, never the last row.
            if not m.get("is_dominant_direction", False):
                continue
            if dex != "xyz" and token and int(m.get("trader_count", 0) or 0) >= min_traders:   # v2-quirk: skip xyz rows; a thin side never sets the lean
                sm_map[token] = m
    return sm_map


def _get_hourly_funding(ctx, asset, floor_rate, current_funding):
    """Persistence + trend from the venue's own HOURLY funding rows (market_get_asset_data
    funding_history). Read-guarded — None on failure (that candidate is skipped). Replaces the
    market_get_funding_history read: its annualized rate is 8x too low and its default 20% filter
    hid all but the most extreme names, so persistence rarely cleared the gate."""
    r = _read(ctx, "market_get_asset_data", {"asset": asset, "candle_intervals": [],
                                             "include_funding": True, "include_order_book": False})
    if not r:
        return None
    data = r.get("data", r) if isinstance(r, dict) else None
    if not isinstance(data, dict):
        return None
    return scoring.funding_history_stats(data.get("funding_history"), floor_rate, current_funding)


def scan(inputs, ctx):
    min_score = float(inputs.get("minScore", 9))
    min_funding_rate = float(inputs.get("minFundingRate", 0.0000228))
    min_persistence = float(inputs.get("minPersistenceHours", 3))
    margin_pct = float(inputs.get("marginPct", 25))           # PERCENT of withdrawable (0,100]
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    default_leverage = int(inputs.get("defaultLeverage", 3))
    quiet_start = int(inputs.get("quietHoursStartUtc", 0))
    quiet_end = int(inputs.get("quietHoursEndUtc", 4))
    emit_ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    post_close_ttl = float(inputs.get("postCloseCooldownSeconds", _DEFAULT_POST_CLOSE_TTL))
    now = time.time()
    hour = time.gmtime(now).tm_hour

    # ── cross-tick state: emit-cooldown map, post-close map, last held set ──
    last = (ctx.state.last() or {}) if ctx.state else {}
    emitted = {k: v for k, v in (last.get("emitted") or {}).items() if (now - v) < emit_ttl}
    last_closed = {k: v for k, v in (last.get("last_closed") or {}).items() if (now - v) < post_close_ttl}
    prev_held = set(last.get("prev_held") or [])

    # ── account read (held set for post-close diff). Read-guarded. ──
    held = _account_held(ctx)
    # v2.1.2: detect closes by diffing held vs last tick. Anything that disappeared just closed.
    for asset in (prev_held - held):
        last_closed[asset] = now

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({
                "emitted": emitted,
                "last_closed": last_closed,
                "prev_held": sorted(held),
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[pangolin.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    # ── QUIET-HOURS GATE (task spec — additive vs v2; see module docstring) ──
    if scoring.in_quiet_hours(hour, quiet_start, quiet_end):
        print(f"[pangolin.scan] quiet hours ({hour:02d}:00 UTC in "
              f"[{quiet_start:02d},{quiet_end:02d})) — no entries", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "quiet_hours", "hour": hour})
        return []

    universe = _build_universe(ctx, inputs)
    if not universe:
        print("[pangolin.scan] empty universe (read failed or no OI>min asset)", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "empty_universe"})
        return []

    regime = _get_regime(ctx)
    sm_map = _get_sm_map(ctx, int(inputs.get("minTraderCount", 10)))

    candidates = []
    for u in universe:
        name = u["name"]
        ctx_block = u["ctx"]
        funding = scoring._f(ctx_block.get("funding", 0))

        # HARD GATE: |funding| >= floor (v2-quirk)
        if abs(funding) < min_funding_rate:
            continue

        # HARD GATE 1: regime must confirm fade OR be neutral/unavailable (v2-quirk)
        fade_direction = "SHORT" if funding > 0 else "LONG"
        if scoring.regime_confirms_fade(fade_direction, regime) is False:
            continue

        # HARD GATE 2: persistence >= minPersistenceHours (v2-quirk) — needs the hourly funding rows
        fh = _get_hourly_funding(ctx, name, min_funding_rate, funding)
        if fh is None:
            continue
        ph = fh.get("persistence_hours")
        if ph is None:
            continue
        try:
            ph = float(ph)
        except (TypeError, ValueError):
            continue
        if ph < min_persistence:
            continue

        c = scoring.score_candidate(name, ctx_block, fh, regime, sm_map.get(name), u["volume_24h"])
        if c is not None:
            candidates.append(c)

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # ── filter order: score floor > held (no pyramiding) > post-close > emit-cooldown (v2.1.2) ──
    eligible = []
    for c in candidates:
        if c["score"] < min_score:
            continue
        t = c["token"]
        if t in held:                                      # v2.1.0: never add to an existing position
            continue
        if t in last_closed:                               # v2.1.2: post-close thrash guard
            continue
        if t in emitted:                                   # v1 post-emit debounce
            continue
        eligible.append(c)

    if not eligible:
        best = candidates[0] if candidates else None
        note = (f"best={best['token']} score={best['score']}" if best else "no candidates")
        print(f"[pangolin.scan] no eligible candidate ({note}, regime={regime})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_eligible",
                  "candidates": len(candidates), "regime": regime})
        return []

    # ── emit ONLY the top candidate per tick (v2 behavior; runtime owns slot parallelism) ──
    c = eligible[0]
    leverage = scoring.get_leverage(c["score"], tiers, default_leverage)
    emitted[c["token"]] = now
    out = [{
        "asset": c["token"],
        "direction": c["fade_direction"],
        "marginPct": margin_pct,                           # SIZING INTENT — runtime sizes the dollars
        "leverage": leverage,                              # conviction-tiered (3 or 5); runtime applies it
        "signal_type": "PANGOLIN_FUNDING_FADE",
        "data": scoring.build_signal_data(c, leverage),
    }]
    print(f"[pangolin.scan] EMIT {c['token']} {c['fade_direction']} {leverage}x score={c['score']} "
          f"ann={c['annualized_pct']:.0f}% persist={c['persistence_hours']:.0f}h regime={regime}",
          file=sys.stderr)
    _persist({"ts": now, "emitted": True, "gate": "emit", "token": c["token"],
              "direction": c["fade_direction"], "score": c["score"], "leverage": leverage,
              "regime": regime, "reasons": c["reasons"]})
    return out


def _account_held(ctx):
    """Set of currently-held coin symbols (uppercase) across main + xyz, for the held-asset
    dedup + post-close diff. Read-guarded — returns set() on failure (no dedup that tick, but
    the runtime's per-asset cooldown is the authority anyway). Dual-DEX read ported from v2
    get_account_value()."""
    held = set()
    if not getattr(ctx, "wallet", None):
        return held
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return held
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    if not isinstance(data, dict):
        return held
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring._f(pos.get("szi", 0)) != 0:
                coin = pos.get("coin")
                if coin:
                    held.add(str(coin).upper())
    return held
