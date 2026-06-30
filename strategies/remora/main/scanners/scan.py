"""REMORA — supervised scanner (Runtime 3.0 port of the v2 Remora whale mirror).

AUTONOMOUS SMART-MONEY WHALE MIRROR. Out of the box, with NO config, Remora
auto-builds a cohort of proven top traders and mirrors their highest-conviction
positions. Optionally, name specific whales via inputs.whales to mirror exactly
those (the override — Remora's named-whale identity).

WHALE-SOURCE RESOLUTION (per tick):
  - inputs.whales NON-EMPTY  -> use those hand-picked trader_ids/wallets (the
    override; original v2 Remora behavior).
  - inputs.whales EMPTY (default) -> AUTO-BUILD a smart-money cohort using
    WhaleHunter's engine: discovery_get_top_traders (ALL_TIME, ranked by realized
    PnL, paged by offset), take the top `cohortSize` (default 10) proven traders,
    cache the cohort in ctx.state and refresh every `cohortRefreshHours`
    (default 24). On a failed refresh read, DEGRADE to the cached cohort (so
    Remora ALWAYS has whales to mirror — never a dead WAITING-on-config tick).

Then Remora's existing mirror logic (unchanged): for each whale, pull their open
positions (read-guarded), take the single largest-notional position above
minNotionalUsd, aggregate across whales into (asset, direction) candidates, score
by consensus count + whale quality, and emit the top `emitTopN` (default 2)
candidates at/above minScore. Direction comes from the mirrored position; leverage
is clamped to [1, MAX_LEVERAGE].

Read-only + single-pass — emits `marginPct` (PERCENT) + `leverage` intents; the
runtime sizes the dollars, owns cooldowns/risk gates/dedup, and trails the DSL
exit. No daemon, no push_signal, no create_position.

READ-GUARD: every ctx.senpi_mcp.call_tool is wrapped try/except -> degrade (skip
that whale / neutral tier / keep cached cohort / skip the tick), NEVER propagate.
Per the scan contract ANY exception rolls the whole tick back to [], so one bad
read would silently kill all emits — guarded so a single flaky read just drops
out.

FIDELITY NOTES:
  - The cohort engine (discovery_get_top_traders paging -> realized-PnL ranking
    -> ctx.state daily-refresh cache -> degrade-to-cached-cohort on a failed
    refresh) is reused VERBATIM from WhaleHunter's scan/scoring (functions
    _read, _build_cohort modelled on WhaleHunter._read / _build_cohorts, and
    scoring.realized / scoring.trader_address are WhaleHunter's accessors).
  - The mirror logic is the v2 Remora producer (remora-producer.py v1.0.1) — all
    scoring/aggregation thresholds verbatim (see scoring.py). v2 emitted exactly
    one `best`; this port emits up to `emitTopN` (default 2) using the SAME sort
    key (score, consensus count, max_notional) per the 1-2 emit allowance.
  - marginPct emitted as PERCENT in (0,100] at the top level; the runtime sizes
    (marginPct/100)*withdrawable. Default 15. A defensive "<=1.0 means a pasted
    fraction, x100" guard preserves either input form.
  - leverage clamped to [1, MAX_LEVERAGE].
  - v2 recent-signals.json race-window dedup -> ctx.state dedup map (TTL=240s,
    prune at 4x TTL). On-chain held-asset filter preserved (held names skipped
    before scoring).
  - v2 read-sanity guard (margin in use + empty positions -> skip tick) ported
    verbatim from cfg.get_positions.
  - No fixed universe to validate against HL meta: Remora has NO whitelist — the
    assets it mirrors are whatever the resolved whales hold, live each tick.
"""

import sys
import time

import scoring


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick. Returns None on failure so the degrade paths apply
    (cohort falls back to its daily cache; a whale just drops out). Modelled on
    WhaleHunter._read."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[remora.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


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


# ── auto-cohort engine (reused from WhaleHunter: discovery_get_top_traders paging
#    -> realized-PnL ranking -> ctx.state daily-refresh cache -> degrade-to-cached) ──

def _build_cohort(ctx, cached, inputs, now):
    """Auto-build the default whale set from the ALL_TIME realized-PnL ranking
    when no whales are hand-picked. Top `cohortSize` proven traders, paged by
    offset. Cached daily in ctx.state and refreshed every `cohortRefreshHours`;
    a failed refresh DEGRADES to the cached cohort. Returns a cohort dict
    {refreshed_at, cache_version, whales:[addr,...]}.

    Reuses WhaleHunter's _build_cohorts pattern + scoring.realized /
    scoring.trader_address accessors so the auto-cohort is bucketed identically.
    """
    refresh_h = float(inputs.get("cohortRefreshHours", scoring.DEFAULT_COHORT_REFRESH_HOURS))
    cohort_size = int(inputs.get("cohortSize", scoring.DEFAULT_COHORT_SIZE))
    min_realized = float(inputs.get("cohortMinRealizedUsd", 0))   # 0 = no floor; rank by realized
    page_size = int(inputs.get("cohortFetchLimit", 1000))
    max_pages = int(inputs.get("cohortMaxPages", 6))

    # fresh cache (still has members, same build version, within TTL) — no fetch.
    if (cached.get("whales") and cached.get("cache_version") == scoring.COHORT_CACHE_VERSION
            and (now - cached.get("refreshed_at", 0)) / 3600 < refresh_h):
        return cached

    ranked, seen = [], set()   # ranked = [(realized, addr), ...] kept sorted desc, capped
    for page in range(max_pages):
        resp = _read(ctx, "discovery_get_top_traders", {
            "time_frame": "ALL_TIME", "sort_by": "PROFIT_AND_LOSS_REALIZED",
            "open_position_filter": False, "limit": page_size, "offset": page * page_size})
        if not resp:
            break
        raw = resp.get("data", resp) if isinstance(resp, dict) else resp
        if isinstance(raw, dict):
            raw = raw.get("traders", raw.get("data", []))
        if not isinstance(raw, list) or not raw:
            break
        for t in raw:
            if not isinstance(t, dict):
                continue
            addr = scoring.trader_address(t)
            if not addr or addr in seen:
                continue
            rp = scoring.realized(t)
            if rp < min_realized:
                continue
            seen.add(addr)
            ranked.append((rp, addr))
        # already have enough proven traders well above the deep band — stop paging.
        if len(seen) >= cohort_size * 3:
            break

    if not ranked:
        # failed refresh (read errors / empty pages) — DEGRADE to the cached cohort
        # so Remora always has whales to mirror. Empty {} only on a true cold start.
        if cached.get("whales"):
            print("[remora.scan] cohort refresh failed — degrading to cached cohort "
                  f"({len(cached['whales'])} whales)", file=sys.stderr)
        else:
            print("[remora.scan] cohort refresh failed and no cache yet — cohort empty this tick",
                  file=sys.stderr)
        return cached

    ranked.sort(key=lambda r: r[0], reverse=True)
    whales = [addr for _rp, addr in ranked[:cohort_size]]
    print(f"[remora.scan] auto-cohort built: top {len(whales)} traders by ALL_TIME realized PnL "
          f"(refresh every {refresh_h:.0f}h)", file=sys.stderr)
    return {"refreshed_at": now, "cache_version": scoring.COHORT_CACHE_VERSION, "whales": whales}


def _fetch_whale_positions(ctx, trader_id):
    """List of position dicts for one whale (leaderboard_get_trader_positions),
    unwrapping the nested data.positions.positions shape. READ-GUARDED -> []
    on any failure (the whale just drops out of this tick). Verbatim unwrap from
    v2 fetch_whale_positions."""
    raw = _read(ctx, "leaderboard_get_trader_positions", {"trader_id": trader_id})
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
    raw = _read(ctx, "discovery_get_trader_state", {"trader_id": trader_id})
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


def _load_cohort(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    c = last.get("cohort", {})
    return dict(c) if isinstance(c, dict) else {}


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
    gather_candidates accepted both; auto-cohort members are bare address strings)."""
    if isinstance(whale, dict):
        return whale.get("trader_id") or whale.get("wallet") or ""
    return whale or ""


def scan(inputs, ctx):
    now = time.time()
    whales_cfg = inputs.get("whales", [])
    use_tier = bool(inputs.get("useWhaleQuality", True))
    min_notional = float(inputs.get("minNotionalUsd", scoring.DEFAULT_MIN_NOTIONAL_USD))
    min_score = int(inputs.get("minScore", scoring.DEFAULT_MIN_SCORE))
    leverage = min(max(int(inputs.get("leverage", scoring.DEFAULT_LEVERAGE)), 1), scoring.MAX_LEVERAGE)
    ttl = float(inputs.get("recentSignalTtlSeconds", 240))
    emit_top_n = max(1, min(2, int(inputs.get("emitTopN", 2))))   # 1-2 emit allowance

    # marginPct intent (PERCENT in (0,100]). v2 stored a FRACTION (0.15); the
    # defensive guard converts a pasted fraction (<=1.0) to a percent (x100).
    margin_pct = float(inputs.get("marginPct", 15))
    if margin_pct <= 1.0:
        margin_pct *= 100.0

    # ── WHALE-SOURCE RESOLUTION ──
    cohort = _load_cohort(ctx)
    if whales_cfg:
        # OVERRIDE: hand-picked whales (Remora's named-whale identity).
        whales = list(whales_cfg)
        whale_source = "named"
    else:
        # DEFAULT: auto-build a smart-money cohort (never WAITING-on-config).
        cohort = _build_cohort(ctx, cohort, inputs, now)
        whales = list(cohort.get("whales", []))
        whale_source = "cohort"

    if not whales:
        # only reachable on a true cold start where the very first cohort fetch
        # failed AND there is no cache yet — next tick retries the fetch.
        print("[remora.scan] no whales available yet (cohort cold start / fetch failed) — "
              "will retry next tick", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"signaled": {}, "cohort": cohort,
                                  "result": {"ts": now, "emitted": False,
                                             "note": "cohort_cold_start"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[remora.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        # persist cohort cache so the cold-start build isn't lost on a flat tick.
        if ctx.state is not None:
            try:
                ctx.state.append({"signaled": _prune_signaled(_load_signaled(ctx), ttl, now),
                                  "cohort": cohort,
                                  "result": {"ts": now, "emitted": False, "note": "no_account_value"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[remora.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── per-whale: fetch positions, take the top, validate tier (READ-GUARDED) ──
    whale_tops = []
    for whale in whales:
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
        result = {"ts": now, "emitted": False, "whaleSource": whale_source,
                  "whales_tracked": len(whales), "candidates_seen": len(candidates),
                  "held": held_assets, "note": f"WAITING (min score {min_score})"}
        print(f"[remora.scan] WAITING — no qualifying whale position to mirror "
              f"(min score {min_score}); source={whale_source} whales={len(whales)} "
              f"candidates={len(candidates)} held={held_assets}", file=sys.stderr)
    else:
        # v2 sort: highest score, tie-break by consensus count then max_notional.
        scored.sort(key=lambda t: (t[0], t[2]["count"], t[2]["max_notional"]), reverse=True)
        result = {"ts": now, "emitted": True, "whaleSource": whale_source,
                  "emitCount": 0, "picks": [], "held": held_assets}
        for score_val, reasons, cand in scored[:emit_top_n]:
            signaled[cand["asset"].upper()] = now
            result["picks"].append({"coin": cand["asset"], "direction": cand["direction"],
                                    "score": score_val, "whaleCount": cand["count"]})
            print(f"[remora.scan] EMIT {cand['asset']} {cand['direction']} score={score_val} "
                  f"whales={cand['count']} maxNotional=${cand['max_notional']:,.0f} "
                  f"{leverage}x marginPct={margin_pct:.2f}% src={whale_source} | {reasons[:5]}",
                  file=sys.stderr)
            out.append({
                "asset": cand["asset"],
                "direction": cand["direction"],
                "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
                "leverage": leverage,             # clamped to [1, MAX_LEVERAGE]; runtime applies it
                "data": {
                    "score": score_val,
                    "leverage": leverage,
                    "direction": cand["direction"],
                    "reasons": reasons,
                    "whaleCount": cand["count"],
                    "maxNotionalUsd": round(cand["max_notional"], 2),
                    "eliteTier": bool(cand.get("quality")),
                    "whaleSource": whale_source,
                    "whales": cand.get("whales", []),
                    "heldAssets": held_assets,
                },
            })
        result["emitCount"] = len(out)

    # ── persist dedup map + cohort cache + this tick's result every tick; bounded
    #    by state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "cohort": cohort, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[remora.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal or rebuild the cohort: {exc!r}", file=sys.stderr)
    return out
