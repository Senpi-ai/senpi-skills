"""VULTURE — supervised scanner (Runtime 3.0 port of the v2 Long-Tail Momentum Rider; 2.0.0 adds the chart).

Multi-asset. Reads smart-money positioning across the whole market via `leaderboard_get_markets`,
scores each whitelist row through the pure `scoring.score_market` (LONG_TAIL_MOMENTUM: SM-concentration
tier + 4h momentum + 15m velocity + 1h acceleration + 4h continuation + trader depth + regime), then —
2.0.0 — asks the chart to confirm every board-gated name through `scoring.confirm_candidate`: the
50-day trend from closed daily candles, the 15-minute price moving with the trade, and the trailing
24-hour range. Emits every confirmed candidate — the runtime applies `slots`, owns the cooldowns/risk
gates, and trails the DSL exit. No daemon, no push_signal.

Reads per tick: the board, the funding regime, the instrument list, the clearinghouse, and ONE
`market_get_asset_data` (1d + 1h) per board-gated candidate — typically none to three. The per-name
funding-history read of 1.x is gone (0 of 571 live emits ever carried a persistence value), which is
what lets the cadence sit at 60 seconds: measured on the live signals, every minute between the signal
and the fill cost about 0.05% of the entry (0.20% at five minutes).

Every MCP read is read-guarded: a transient or permission error on ONE read degrades that one asset (or
the whole-tick shared context) to neutral; unreadable candles fail CLOSED for that name (its trend cannot
be read, so it is not traded this tick); nothing propagates and rolls back the whole tick
(scan-contract.md: any unhandled exception in a tick rolls the WHOLE return back to [])."""

import sys
import time

import scoring

_DEFAULT_TTL = 14400          # 240m — mirror the v2 per-asset cooldown (anti re-fire)
# Leverage tiers — kept for configuration compatibility; both 5x since 1.3.0 (the 7x apex tier was the
# worst live bucket: score 11 ran a profit factor of 0.31 against 0.90 for score 10).
_DEFAULT_TIERS = [
    {"min_score": 11, "leverage": 5, "label": "apex"},
    {"min_score": 10, "leverage": 5, "label": "conviction"},
]
_DEFAULT_MAX_LEVERAGE = 5     # package cap; the venue's own max per name clamps below it
_DEFAULT_VOL_FLOOR = 1_000_000  # USD/day — a name thinner than this is skipped
_CANDLE_INTERVALS = ["1d", "1h"]   # the trend (50 closed days of 91) and the 24h range (of 7 days)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply (regime ->
    None, held -> [], candles -> None = that name is skipped this tick)."""
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


_DEFAULT_MAX_UNIVERSE = 60          # 87 listed perps cleared the $1M floor on 2026-10-12; 60 is
                                    # the liquid part of that without a hard-coded tail
_DEFAULT_MAX_CHART_READS = 6        # slots is 2; the chart only decides the top few
_DEFAULT_EXCLUDE = ["USDC", "USDT", "DAI", "USDE", "FDUSD", "TUSD", "BUSD"]


def _instruments(ctx):
    """market_list_instruments (main DEX) -> {NAME_UPPER: {max_leverage, day_ntl_vlm, delisted}},
    or None on FAILURE. One read per tick feeds the derived universe, the venue leverage clamp
    and the daily-volume floor.

    2.1.0 returns None rather than {} on a failed read, and the distinction now matters: the
    universe is DERIVED from this call, so "unreadable" and "the venue lists nothing" have
    opposite correct responses. Collapsing both to {} was safe while a hard-coded whitelist was
    the universe; it would now either trade an unvetted board or silently trade nothing.
    `max_leverage` is a top-level instrument field; `dayNtlVlm` sits under `context`."""
    raw = _read(ctx, "market_list_instruments", {})
    if not isinstance(raw, dict):
        return None
    data = raw.get("data", raw)
    rows = data.get("instruments", []) if isinstance(data, dict) else data
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        ctxb = r.get("context") if isinstance(r.get("context"), dict) else {}
        max_lev = scoring.safe_float(r.get("max_leverage"), 0.0)
        out[str(r["name"]).upper()] = {
            # `venue_name` is the venue's OWN spelling and the only form safe to send back to it.
            # The dict is keyed upper for the membership joins, but kBONK/kPEPE and any `xyz:`
            # name do not survive upper() — and `asset` is what the order carries, so a derived
            # universe that returned the KEY would emit KBONK and be refused as an unknown coin.
            "venue_name": str(r["name"]),
            "max_leverage": max_lev if max_lev > 0 else None,
            "day_ntl_vlm": scoring.safe_float(ctxb.get("dayNtlVlm"), 0.0),
            "delisted": bool(r.get("is_delisted", False)),
        }
    return out


def derive_universe(instruments, vol_floor, max_names, banned, exclude):
    """The tradeable long tail, read off the venue instead of hand-listed.

    Every listed perp that is not delisted, not an `xyz:` name, not a banned major and not under
    the daily-volume floor, ranked by volume and capped at `max_names`. Pure — the caller owns
    the read — so the band is unit-testable without MCP.

    WHY THIS REPLACED A 27-NAME WHITELIST (2.1.0). The list rotted in both directions, and both
    were measurable on the day it was removed: 4 of its 27 names (AIXBT, DYDX, HEMI, POLYX) had
    already fallen under the $1M/day floor, while 87 listed perps cleared it — so the list was
    carrying 4 names the floor rejects and missing 64 it accepts, among them XRP at $147M/day,
    PUMP at $152M and STRK at $108M. A long-tail momentum screener is not an identity basket;
    its universe is "what is liquid on the venue today", which is a reading, not a decision.

    The ban list stays explicit. BTC/ETH/SOL are excluded by NAME, not by volume rank, because
    rank is unstable and "the top three by volume" is not the same set week to week — the
    strategy's own revealed preference already keeps HYPE at $522M and NEAR at $414M, so this is
    a specific exclusion of the three reference majors, not a size cut."""
    out = []
    for name, info in (instruments or {}).items():
        if info.get("delisted"):
            continue
        if name.startswith("XYZ:") or ":" in name:        # equities/commodities are a different book
            continue
        if name in banned or name in exclude:
            continue
        if vol_floor > 0 and info.get("day_ntl_vlm", 0.0) < vol_floor:
            continue
        # Return the VENUE's spelling, never the uppercased key — this list becomes the emitted
        # `asset`. Falls back to the key only if an instrument row somehow carried no name.
        out.append((info.get("venue_name") or name, info.get("day_ntl_vlm", 0.0)))
    out.sort(key=lambda kv: -kv[1])
    return [n for n, _ in out[:max_names]] if max_names > 0 else [n for n, _ in out]


def _candles(ctx, asset):
    """market_get_asset_data (1d + 1h) for one board-gated candidate -> {"1d": [...], "1h": [...]} or None.
    Read-guarded and type-guarded; None means the chart could not be read and the name is skipped."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset, "candle_intervals": list(_CANDLE_INTERVALS),
        "include_funding": False, "include_order_book": False,
    })
    if not isinstance(md, dict) or md.get("success") is False:
        return None
    d = md.get("data", md)
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {})
    return candles if isinstance(candles, dict) and candles else None


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
    # 2.1.0 — an OPTIONAL override, default EMPTY. Unset means the universe is read off the
    # venue (derive_universe). `scoring.DEFAULT_WHITELIST` is kept as the documented historical
    # basket for an operator who wants exactly it, and is no longer the default: a screener
    # reads the market; a fixed list is only right for an identity basket.
    whitelist = list(inputs.get("whitelist") or [])
    max_universe_names = int(float(inputs.get("maxUniverseNames", _DEFAULT_MAX_UNIVERSE)))
    exclude_assets = list(inputs.get("excludeAssets") or _DEFAULT_EXCLUDE)
    max_chart_reads = int(float(inputs.get("maxChartReads", _DEFAULT_MAX_CHART_READS)))
    tiers = inputs.get("leverageTiers", _DEFAULT_TIERS)
    margin_pct = float(inputs.get("marginPct", 40))    # PERCENT of withdrawable (0,100], not a fraction
    if 0 < margin_pct < 1.0:                           # a pasted fraction (0.40 meant 40)
        margin_pct *= 100.0
    lev_cap = float(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    vol_floor = float(inputs.get("minDayNtlVlmUsd", _DEFAULT_VOL_FLOOR))
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
    instruments = _instruments(ctx)

    # 2.1.0 — FAIL CLOSED on an unreadable instrument list. It is now the universe, the volume
    # floor AND the venue leverage clamp, so a failed read means we do not know what is listed,
    # how liquid it is, or what leverage the venue will accept. Trading the raw board on that
    # basis is the one outcome worse than missing a tick, and on a 60s cadence a missed tick
    # costs nothing. An explicit `whitelist` override is the documented escape hatch.
    if instruments is None:
        if whitelist:
            print("[vulture.scan] WARNING: market_list_instruments unreadable — falling back to the "
                  f"configured {len(whitelist)}-name override, with no volume floor or venue clamp "
                  "this tick (package cap still applies)", file=sys.stderr)
            instruments = {}
        else:
            print("[vulture.scan] HOLD: market_list_instruments unreadable and no whitelist override "
                  "— the universe, the volume floor and the venue clamp all come from that read, so "
                  "this tick is skipped rather than guessed", file=sys.stderr)
            _persist({"ts": now, "gate": "instruments_unreadable", "emitted": 0, "regime": regime})
            return []

    # The universe: the operator's explicit basket if they set one, else READ OFF THE VENUE.
    if whitelist:
        universe = list(whitelist)
        universe_src = f"override({len(universe)})"
    else:
        universe = derive_universe(instruments, vol_floor, max_universe_names,
                                   scoring.BANNED_ASSETS, {e.upper() for e in exclude_assets})
        universe_src = f"derived({len(universe)})"
        if not universe:
            print("[vulture.scan] HOLD: no listed perp clears the "
                  f"${vol_floor:,.0f}/day floor outside the banned majors", file=sys.stderr)
            _persist({"ts": now, "gate": "empty_universe", "emitted": 0, "regime": regime})
            return []

    held_assets = _held_assets(ctx)
    held_upper = {h.upper() for h in held_assets}
    wl_upper = {t.upper() for t in universe}

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
        for tracked in universe:
            if token_raw == tracked.upper():
                matched = tracked
                break
        if matched is None:
            continue
        info = instruments.get(token_raw)
        if instruments and info is None:                  # the venue does not list it (today) — skip
            continue
        if info and info.get("delisted"):
            continue
        if info and vol_floor > 0 and info["day_ntl_vlm"] < vol_floor:
            continue                                      # thinner than the daily-volume floor

        try:
            # persistence_map is empty by design (2.0.0): the funding-history read never carried a value.
            c = scoring.score_market(m, regime, {}, universe, tiers, inputs)
        except Exception as exc:  # noqa: BLE001 — one malformed board row must not end the tick
            print(f"[vulture.scan] WARNING: score_market({token_raw}) failed: {exc!r}", file=sys.stderr)
            continue
        if c is not None:
            c["leverage"] = scoring.clamp_leverage(c["leverage"], lev_cap, info.get("max_leverage") if info else None)
            c["venue_max_leverage"] = info.get("max_leverage") if info else None
            c["day_ntl_vlm"] = info.get("day_ntl_vlm") if info else None
            candidates.append(c)

    if not candidates:
        print(f"[vulture.scan] HOLD: scanned={len(markets)} universe={universe_src} "
              f"no candidates >= floor | regime={regime}",
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
    rejected = {}
    reads = 0
    for c in candidates:
        cu = c["asset"].upper()
        if cu in held_upper:                              # already holding — skip
            continue
        if recent.get(cu) is not None and (now - recent[cu]) < ttl:   # signal-dedup
            continue
        # 2.1.0 — BOUND THE READS. Candidates are score-sorted and `slots` is 2, so the chart only
        # ever decides the top few; before this the loop read candles for EVERY name above the
        # score floor, which was ~3 under a 27-name whitelist but scales with the derived
        # universe. `timeout_seconds` is 45 against a 60s tick and a timeout is a live hazard,
        # not a safety net (strategies/tests/test_scanner_timeout_headroom.py): SIGALRM leaves the
        # MCP client latched closed for the life of the process while ticks still report ok. So
        # the read count is capped rather than left to grow with the universe.
        if reads >= max_chart_reads:
            rejected[c["asset"]] = "CHART_READ_CAP"
            print(f"[vulture.scan] SKIP {c['asset']} score={c['score']}: chart-read cap "
                  f"{max_chart_reads} reached this tick (lower-scored than the names already read)",
                  file=sys.stderr)
            continue
        # 2.0.0 — the chart confirms the board: one candle read per board-gated name, never per whitelist name.
        reads += 1
        candles = _candles(ctx, c["asset"])
        if candles is None:
            rejected[c["asset"]] = "CANDLES_UNREADABLE"
            print(f"[vulture.scan] SKIP {c['asset']} {c['direction']}: candles unreadable — the trend "
                  f"cannot be read, so the name is not traded this tick", file=sys.stderr)
            continue
        ok, why, chart = scoring.confirm_candidate(c, candles, inputs, now)
        if not ok:
            rejected[c["asset"]] = why.split(" ", 1)[0]
            print(f"[vulture.scan] SKIP {c['asset']} {c['direction']} score={c['score']}: {why}", file=sys.stderr)
            continue
        reasons = list(c["reasons"]) + [why]
        out.append({
            "asset": c["asset"],
            "direction": c["direction"],
            "marginPct": margin_pct,          # SIZING INTENT (PERCENT) — runtime sizes the dollars
            "leverage": c["leverage"],        # 5x, clamped to the venue cap for this name; runtime applies it
            # `required: false` in signal_data_schema permits an ABSENT key, never a present-and-null:
            # the intake discards the WHOLE candidate on a null optional. Drop them here rather than
            # coercing to 0/"" — a coerced value would assert a measurement that was never taken.
            "data": {k: v for k, v in {
                "score": c["score"],
                "tier": c["tier_label"],
                "leverage": c["leverage"],
                "direction": c["direction"],
                "reasons": reasons,
                "smPct": c["sm_pct"],
                "smTraders": c["sm_traders"],
                "priceChange4hPct": c["p4h"],
                "priceChange1hPct": c["p1h"],
                "priceChange15mPct": c["p15m"],
                "contribChange15m": c["c15m"],
                "contribChange1h": c["c1h"],
                "contribChange4h": c["c4h"],
                "fundingRegime": c["regime"],
                "venueMaxLeverage": c.get("venue_max_leverage"),
                "dayVolumeUsd": c.get("day_ntl_vlm"),
                "trendRegime": chart.get("trend_regime"),
                "trendSma": chart.get("sma"),
                "rangePos24h": chart.get("range_pos"),
            }.items() if v is not None},
        })
        recent[cu] = now

    print(f"[vulture.scan] EMIT {len(out)}/{len(candidates)} | scanned={len(markets)} "
          f"best={best_score} regime={regime} rejected={rejected} | "
          f"{[(o['asset'], o['direction'], o['data']['score'], o['leverage']) for o in out]}",
          file=sys.stderr)
    _persist({"ts": now, "scanned": len(markets), "candidates": len(candidates),
              "emitted": len(out), "gate": "emit" if out else "chart_rejected", "best_score": best_score,
              "regime": regime, "rejected": rejected, "assets": [o["asset"] for o in out]})
    return out
