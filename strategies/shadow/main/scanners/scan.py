"""SHADOW — supervised scanner: mirror vetted traders' FRESH opens only.

Per tick: resolve the watched cohort (explicit `traderAddresses`, else auto-select the
top-N traders who are PROVEN AND STILL AT THE DESK — see `_resolve_cohort`), fetch each
trader's current open positions, diff against the seeded per-trader book to find only
NEWLY opened (coin, side) pairs, require optional multi-trader confirmation, reject any
whose price has already run past the entry-slippage cap, and emit a budget-relative,
min-notional-floored INTENT. The runtime sizes, owns slots/dedup, and trails the DSL exit.
Read-only, single-pass. Never inherits an existing book — first sight of a trader seeds it."""

import sys
import time

import blacklist
import scoring

# Bumped 2026-10-09: the cached cohort from the ALL-TIME ranking is useless and must be
# re-resolved on the first tick after this ships, not carried for another 24h.
CACHE_VERSION = 2
_DEFAULT_TTL = 3600
_DEFAULT_TIME_FRAME = "WEEKLY"        # DAILY | WEEKLY | MONTHLY | ALL_TIME
_DEFAULT_MAX_LAST_TRADE_H = 48.0      # a "fresh-entry mirror" cannot mirror a dormant trader
_DEFAULT_MIN_COHORT = 4               # below this the mirror is structurally silent — see below
_DEFAULT_COHORT_MAX_AGE_H = 72.0      # refuse to emit on a cohort this stale
_DEFAULT_MAX_ENTRY_AGE = 240      # 2 ticks at the 120s cadence — a mirror of a FRESH open


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error must NOT roll back the whole tick."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[shadow.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _unwrap(resp, *keys):
    """discovery/market responses wrap payloads under data/<key>; return the first list/dict."""
    if resp is None:
        return None
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), (list, dict)):
                return data[k]
    return data


def _resolve_cohort(ctx, cached, inputs, now):
    """Explicit `traderAddresses` win. Else auto-select traders who are PROVEN **and still
    trading**, cached daily.

    THE BUG THIS FIXES (2026-10-09). The cohort was auto-selected by ALL-TIME realized PnL,
    which is a hall of fame, not a desk. Measured live that day, the top five by all-time
    realized PnL had last traded 45.8, 50.1, 7.4, 190.7 and 47.4 days ago, and four of the
    five had `activityLabel: null` because they are not active enough to classify. This
    strategy mirrors opens younger than 240 seconds. Watching five dormant whales for a
    4-minute-old entry fires never — one deployed instance ran 194 clean ticks and found zero
    candidates in six hours, and because every print in this file was on a failure path it
    looked identical to working-and-waiting.

    The same response already carried the fix. `discovery_get_top_traders` returns
    `lastTradeTimestamp`, `activityLabel`, `traderActivityScore`, `winRate`,
    `gainToPainRatio` and `maxDrawdown` per trader, and this function read none of them.

    The fix is NOT to change which window defines "proven". `senpi-signals` — the engine puffin
    vendors — defines the smart cohort as realized >= $1M on the ALL_TIME ranking, SAMPLED up to
    150 members across paged reads. That definition is right and shadow should share it, so that
    "smart money" means the same population here as everywhere else in the product.

    Shadow's error was narrower: it took the TOP 3 of that ranking. The top of a PnL ranking is
    a hall of fame; the cohort is the hundreds below it. So: page deep enough to HAVE a cohort
    (`autoSelectPool`), keep the proven floor, then drop anyone who has not actually traded
    inside `maxLastTradeAgeHours`, and rank the survivors by QUALITY — gain-to-pain, not raw PnL
    — before taking `maxWatched`.

    The window ships as WEEKLY with a +$100k floor (Jason, 2026-10-09). That selects traders who
    made real money THIS WEEK rather than lifetime, which for a fresh-entry mirror is the
    population that is actually at the desk — measured the same day, the weekly top five had last
    traded 22h, 47m, 17h, 1.7h and 28m ago against 45.8 and 50.1 DAYS for the all-time top two.
    The cost is that a weekly ranking selects hot hands: that live sample ran three DEGEN labels,
    all AGGRESSIVE risk, drawdowns to -78%. The quality floors below are what make that tolerable
    — they are not decoration, they are the thing standing between this and mirroring a coin-flip.

    MARKET MAKERS ARE EXCLUDED TWICE, deliberately.

    First by Senpi's own blacklist — `GetDiscoveryBlacklist`, the same client quant-desk screens
    its cohorts with, vendored here byte-identically. Only wallets whose REASON is MARKET MAKER
    are dropped: the table also holds probe/test rows, and refusing one of those would be a false
    accusation against a real trader. It is called ONCE PER COHORT REFRESH (daily), not per tick,
    and it FAILS OPEN — an unscreened cohort is far more useful than no cohort, exactly as the
    desk treats it — but the tick line then says UNSCREENED so the miss is visible rather than
    assumed away.

    Second by the trader's own numbers, which need no service at all: `averageTradesPerDay` is
    the fingerprint (the live weekly cohort carried traders at 736, 515 and 488 trades/day, and
    the all-time list one at 2,655) and `averageHoldTimeSeconds` is the confirmation, since an MM
    round-trips in seconds. This cut is what still works when the blacklist is down, and it also
    catches quote machines the blacklist has not flagged yet.

    `excludeTraderAddresses` remains for operators who want to pin extra wallets out by hand.

    Two guards the old version lacked. `minCohortSize` refuses a cohort too small to confirm
    anything — a mirror watching one or two traders is structurally silent, and silence here
    is indistinguishable from breakage. `cohortMaxAgeHours` refuses to emit on a cohort older
    than the refusal window, because a failed refresh used to fall back to the cached list
    FOREVER: `cached.get("addrs", [])` is empty on a fresh deploy, so a single failed read on
    tick one produced a permanently empty cohort with no log line. Same shape as the phalanx
    cohort-age refusal.

    Returns (addrs, cohort_cache, note) — `note` is for the per-tick log line, so a human can
    always see WHY the cohort is what it is.
    """
    explicit = [a.lower() for a in (inputs.get("traderAddresses") or []) if isinstance(a, str) and a]
    max_watch = int(inputs.get("maxWatched", 10))
    if explicit:
        return explicit[:max_watch], cached, f"explicit cohort ({len(explicit[:max_watch])})"

    refresh_h = float(inputs.get("cohortRefreshHours", 24))
    age_h = (now - cached.get("refreshed_at", 0)) / 3600.0
    if (cached.get("addrs") and cached.get("cache_version") == CACHE_VERSION
            and age_h < refresh_h):
        return cached["addrs"], cached, f"cached cohort ({len(cached['addrs'])}), {age_h:.1f}h old"

    tf = str(inputs.get("cohortTimeFrame", _DEFAULT_TIME_FRAME)).upper()
    smin = float(inputs.get("autoSelectMinRealizedUsd", 100_000))
    max_age_h = float(inputs.get("maxLastTradeAgeHours", _DEFAULT_MAX_LAST_TRADE_H))
    min_wr = float(inputs.get("minTraderWinRatePct", 0))
    min_g2p = float(inputs.get("minTraderGainToPain", 0))
    max_tpd = float(inputs.get("maxTraderTradesPerDay", 0))
    max_tpd_unknown = float(inputs.get("maxTradesPerDayUnknownHold", 0))
    min_hold = float(inputs.get("minTraderHoldSeconds", 0))
    excluded = {a.lower() for a in (inputs.get("excludeTraderAddresses") or [])
                if isinstance(a, str) and a}

    resp = _read(ctx, "discovery_get_top_traders", {
        "time_frame": tf, "sort_by": "PROFIT_AND_LOSS_REALIZED",
        "open_position_filter": True, "limit": int(inputs.get("autoSelectPool", 50)), "offset": 0})
    raw = _unwrap(resp, "traders", "data") or []

    picked, rejected = [], {"pnl": 0, "dormant": 0, "mm": 0, "quality": 0, "excluded": 0,
                            "blacklist": 0}
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        addr = (t.get("address") or t.get("trader_address") or "").lower()
        if not addr or addr in excluded:
            if addr:
                rejected["excluded"] += 1
            continue
        rp = scoring._f(t, "realizedProfitAndLoss", "profit_and_loss_realized",
                        "realized_profit_and_loss", "realizedPnl", default=0.0)
        if rp < smin:
            rejected["pnl"] += 1
            continue
        # STILL TRADING. A missing/zero timestamp is treated as dormant: for a fresh-entry
        # mirror, "we cannot tell when they last traded" is not a reason to follow them.
        last_ts = scoring._f(t, "lastTradeTimestamp", "last_trade_timestamp", default=0.0)
        if last_ts <= 0 or (now - last_ts) / 3600.0 > max_age_h:
            rejected["dormant"] += 1
            continue
        # MARKET-MAKER CUT, from fields already in this row. HOLD TIME is the discriminator,
        # not frequency — measured over 200 live rows on 2026-10-09, a trades/day cut at 200
        # removed at least six legitimate high-frequency DISCRETIONARY traders (one at 736/day
        # holding 32 hours, $1.13M realized) while letting through a scalper at 178/day with a
        # ZERO-second hold, which is the one profile a fresh-entry mirror can never fill
        # alongside. So: a KNOWN hold below the floor disqualifies at any frequency, a missing
        # hold falls back to a tighter frequency cut, and the high frequency cap is only an
        # extreme backstop.
        #
        # `_f` cannot tell a missing field from a real 0, and here the difference matters: the
        # API returns null for "not computed" and 0 for "closes instantly". Default to -1 so
        # absent and zero stay distinguishable.
        tpd = scoring._f(t, "averageTradesPerDay", "average_trades_per_day", default=0.0)
        hold = scoring._f(t, "averageHoldTimeSeconds", "average_hold_time_seconds", default=-1.0)
        hold_known = hold >= 0
        is_mm = (
            (hold_known and min_hold and hold < min_hold)          # instant/short holds = MM
            or (not hold_known and max_tpd_unknown and tpd > max_tpd_unknown)
            or (max_tpd and tpd > max_tpd)                         # extreme backstop
        )
        if is_mm:
            rejected["mm"] += 1
            continue
        wr = scoring._f(t, "winRate", "win_rate", default=0.0)
        g2p = scoring._f(t, "gainToPainRatio", "gain_to_pain_ratio", default=0.0)
        if (min_wr and wr < min_wr) or (min_g2p and g2p < min_g2p):
            rejected["quality"] += 1
            continue
        g2p_rank = g2p if g2p > 0 else -1.0
        picked.append((g2p_rank, wr, addr))

    # Rank the ACTIVE proven survivors by quality, then take the watch list. Ranking by raw
    # realized PnL is what put three near-total-drawdown whales at the top in the first place.
    picked.sort(key=lambda r: (-r[0], -r[1]))
    picked = [a for _g, _w, a in picked][:max_watch]

    # Senpi's blacklist, once per refresh. Fails OPEN and SAYS SO — a cohort we could not screen
    # still beats no cohort, but "unscreened" must never read as "clean".
    screened = True
    if picked and bool(inputs.get("screenMarketMakers", True)):
        try:
            kept, dropped = blacklist.filter_out(picked)
            rejected["blacklist"] = len(dropped)
            picked = kept
        except blacklist.BlacklistUnavailable as exc:
            screened = False
            print(f"[shadow.scan] blacklist unavailable ({exc}) — cohort UNSCREENED for market "
                  f"makers; the trades/day and hold-time cuts still applied", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — a scanner tick must never die on a screen
            screened = False
            print(f"[shadow.scan] blacklist screen failed: {exc!r} — cohort UNSCREENED",
                  file=sys.stderr)

    min_cohort = int(inputs.get("minCohortSize", _DEFAULT_MIN_COHORT))
    note = (("" if screened else "UNSCREENED ")
            + f"{tf} cohort: {len(picked)} picked from {len(raw)} (rejected "
            f"{rejected['pnl']} PnL<{smin:.0f}, {rejected['dormant']} dormant>{max_age_h:.0f}h, "
            f"{rejected['mm']} market-maker-like, {rejected['quality']} quality, "
            f"{rejected['excluded']} pinned out, {rejected.get('blacklist', 0)} on Senpi's "
            f"market-maker blacklist)")

    if len(picked) < min_cohort:
        # Keep whatever we had rather than shrink to a silent cohort, and SAY SO.
        kept = cached.get("addrs", [])
        return kept, cached, note + f" — below minCohortSize {min_cohort}, keeping {len(kept)} cached"
    return (picked,
            {"refreshed_at": now, "cache_version": CACHE_VERSION, "addrs": picked,
             "mm_screened": screened},
            note)


def _fetch_states(ctx, addrs):
    """discovery_get_trader_state in batches of 50 -> {addr: trader-state dict}."""
    out = {}
    for i in range(0, len(addrs), 50):
        resp = _read(ctx, "discovery_get_trader_state",
                     {"trader_addresses": addrs[i:i + 50], "include_position_age": True})
        data = _unwrap(resp, "traders")
        for t in (data or []) if isinstance(data, list) else []:
            if isinstance(t, dict):
                addr = (t.get("address") or t.get("trader_address") or "").lower()
                if addr:
                    out[addr] = t
    return out


def scan(inputs, ctx):
    now = time.time()
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    min_confirm = int(inputs.get("minConfirm", 1))
    max_slip = float(inputs.get("maxEntrySlippagePct", 1.5))

    last = (ctx.state.last() or {}) if ctx.state else {}
    seen_map = dict(last.get("seen") or {})
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    addrs, cohort, cohort_note = _resolve_cohort(ctx, last.get("cohort", {}), inputs, now)

    def _persist(new_seen):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"cohort": cohort, "seen": new_seen, "recent": recent})
        except Exception as exc:  # noqa: BLE001
            print(f"[shadow.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if not addrs:
        _persist(seen_map)
        print(f"[shadow.scan] WAITING — no cohort to watch | {cohort_note}", file=sys.stderr)
        return []

    # A cohort this stale is not evidence of anything. Refuse to OPEN on it rather than mirror
    # a list resolved days ago — the trader who was active then may be long gone. Same refusal
    # phalanx applies to its proven cohort.
    cohort_age_h = (now - cohort.get("refreshed_at", 0)) / 3600.0 if cohort.get("refreshed_at") else None
    max_cohort_age = float(inputs.get("cohortMaxAgeHours", _DEFAULT_COHORT_MAX_AGE_H))
    if cohort_age_h is not None and cohort_age_h > max_cohort_age:
        _persist(seen_map)
        print(f"[shadow.scan] WAITING — cohort is {cohort_age_h:.1f}h old, past the "
              f"{max_cohort_age:.0f}h refusal | {cohort_note}", file=sys.stderr)
        return []

    states = _fetch_states(ctx, addrs)

    max_age = float(inputs.get("maxEntryAgeSeconds", _DEFAULT_MAX_ENTRY_AGE))
    fresh_by_trader = {}
    new_seen = {}
    for addr in addrs:                              # only re-seed CURRENTLY-watched traders
        state = states.get(addr)
        if state is None:
            # The read failed for this trader. Emptying their seen-set here made the NEXT tick read
            # their whole standing book as fresh opens, which is how positions 2-65% from their fill
            # got mirrored. Carry the book we already seeded and sit this trader out.
            if addr in seen_map:
                new_seen[addr] = seen_map[addr]
            print(f"[shadow.scan] no state for {addr[:10]}… — keeping the book already seeded",
                  file=sys.stderr)
            continue
        positions = scoring.extract_positions(state)
        fresh, keys = scoring.diff_fresh(addr, positions, seen_map)
        new_seen[addr] = keys
        fresh = [p for p in fresh if scoring.opened_within(p, now, max_age)]   # a FRESH open, not an old one
        if fresh:
            fresh_by_trader[addr] = fresh

    agg = scoring.aggregate_fresh(fresh_by_trader)
    cands = [a for a in agg.values() if a["confirm"] >= min_confirm]     # confirmation gate

    out = []
    for a in sorted(cands, key=lambda c: c["confirm"], reverse=True):
        k = scoring.pos_key(a["coin"], a["side"])
        if recent.get(k) is not None and (now - recent[k]) < ttl:        # dedup: one fire per book event
            continue
        slip = scoring.chase_pct(a["entry_avg"], a.get("mark", 0.0), a["side"])
        if a.get("mark", 0.0) > 0 and not scoring.chase_within(slip, max_slip):
            print(f"[shadow.scan] skip {k}: chase {slip:+.2f}% outside ±{max_slip}% of their fill",
                  file=sys.stderr)
            continue
        out.append({
            "asset": a["coin"],
            "direction": a["side"],
            "marginPct": scoring.margin_pct_for(a["confirm"], inputs),
            "leverage": scoring.leverage_for(a["lev_avg"], inputs),
            "data": {
                "score": scoring.score_candidate(a, slip),
                "direction": a["side"],
                "signalKind": "FRESH_ENTRY_MIRROR",
                "confirmCount": a["confirm"],
                "entrySlippagePct": slip,
                "traderEntry": round(a["entry_avg"], 6),
                "traders": a["traders"],
                "reasons": [f"{a['confirm']} watched trader(s) opened {a['side']} {a['coin']} fresh",
                            f"chase {slip:+.2f}% vs their entry (cap ±{max_slip}%)"],
            },
        })
        recent[k] = now

    _persist(new_seen)
    fresh_total = sum(len(v) for v in fresh_by_trader.values())
    print(f"[shadow.scan] {'EMIT ' + str(len(out)) if out else 'WAITING — no emit'} | "
          f"{cohort_note} | watching {len(addrs)}, states {len(states)}, "
          f"fresh opens {fresh_total}, candidates {len(cands)} after confirm>={min_confirm} "
          f"| max entry age {max_age:.0f}s, chase cap ±{max_slip}%",
          file=sys.stderr)
    return out
