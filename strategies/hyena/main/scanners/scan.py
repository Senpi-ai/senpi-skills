"""HYENA — supervised scanner (NET-NEW Runtime 3.0 risk-off SHORT hedge sleeve).

The fleet's dedicated SHORT-biased crypto/crypto-proxy agent. SHORT_ONLY by
design: it shorts the crypto block ONLY when risk-off conditions confirm, and it
EMITS NOTHING (idles, WAITING) the rest of the time. It NEVER goes long.

Per tick it:
  1. reads the account + held set (clearinghouse, dual-DEX equity via max(),
     XYZ enumerated; bison/bobcat read-sanity guard),
  2. fetches the MASTER risk-off gate: market_get_funding_regime (READ-GUARDED,
     dog's parser). New shorts proceed ONLY when the regime is LONG_CROWDED.
     If the market-wide regime read is unavailable/NEUTRAL, a per-asset
     crowded-long funding fallback can still confirm a short on a name; a
     SHORT_CROWDED regime blocks ALL new shorts this tick (WAITING),
  3. for each universe name: net smart-money direction (leaderboard_get_markets,
     READ-GUARDED, dog/bison parser) + 4h candles (market_get_asset_data,
     dex-aware per bobcat, READ-GUARDED) + per-asset funding rate,
  4. scores SHORT-eligible names (regime crowding + SM short tilt + 4h downtrend
     strength + funding squeeze) and emits the top 1-2 SHORTs (held +
     recent-signal dedup).

Read-only + single-pass — emits a `marginPct` intent (PERCENT) + `leverage`
(clamped [1,5] then to the venue cap). The runtime sizes the dollars, owns
cooldowns/risk gates, and trails the DSL exit. No daemon, no push_signal, no
create_position.

PARSING PROVENANCE: market_get_funding_regime + leaderboard_get_markets parsing
is COPIED from the Dog gold template (dog/main/scanners/scan.py); the account
read-guard + dual-DEX collapse from Bison/Bobcat; the dex-aware asset fetch from
Bobcat. None of the MCP response shapes are invented.
"""

import sys
import time

import scoring


# Defaults (overridable via runtime.yaml inputs)
_UNIVERSE_DEFAULT = ["BTC", "ETH", "SOL", "HYPE", "xyz:MSTR", "xyz:COIN", "xyz:CRCL"]
_DEFAULT_MIN_SCORE = 5            # SHORT-eligibility floor (regime+SM+downtrend baseline = 5)
_DEFAULT_MARGIN_PCT = 15.0       # PERCENT of withdrawable (0,100]
_DEFAULT_LEVERAGE = 4            # clamped to [leverageMin,leverageMax] then venue cap
_DEFAULT_LEVERAGE_MIN = 1
_DEFAULT_LEVERAGE_MAX = 5
_DEFAULT_LEADERBOARD_LIMIT = 100  # leaderboard_get_markets aggregation depth
_DEFAULT_MAX_EMIT = 2            # emit top 1-2 shorts
_DEFAULT_RECENT_TTL = 240        # race-window dedup (seconds)
_DEFAULT_CROWDED_FUNDING = 0.0002  # per-asset crowded-long funding fallback/bonus


def _dex_for(asset, inputs):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass '' (bobcat)."""
    dex = inputs.get("dex")
    if dex is not None and not str(asset).lower().startswith("xyz:"):
        # An explicit non-xyz dex override only applies to non-prefixed names.
        return dex
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll the
    whole tick back (the contract rolls ANY exception back to []). Returns None on
    failure so the degrade paths apply (regime None -> funding fallback; markets
    empty -> SM None per name; candles None -> name skipped)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[hyena.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── ACCOUNT + HELD ASSETS (bison/bobcat dual-DEX read-guard, verbatim pattern) ──

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions enumerated across both
    sub-DEX sections. Includes the read-sanity guard (margin in use + empty
    positions -> skip tick)."""
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
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring.safe_float(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring.safe_float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring.safe_float(pos.get("marginUsed", 0))})

    # read-sanity guard: a corrupt clearinghouse read can report margin/notional
    # IN USE while returning an EMPTY positions list; running held-asset dedup off
    # that re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[hyena.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── FUNDING REGIME (master risk-off gate — dog's fetch_funding_regime, verbatim) ──

def _fetch_regime(ctx):
    """Market-wide funding regime string (LONG_CROWDED / SHORT_CROWDED / NEUTRAL)
    or None. READ-GUARDED. Parser COPIED from dog/main/scanners/scan.py."""
    r = _read(ctx, "market_get_funding_regime", {})
    if not r:
        return None
    if isinstance(r, dict) and r.get("success") is False:
        return None
    data = r.get("data", r) if isinstance(r, dict) else r
    if isinstance(data, dict):
        return data.get("regime")
    return None


# ── SMART-MONEY MARKETS (dog/bison leaderboard parser, verbatim shape) ──

def _fetch_markets(ctx, limit):
    """Top-N smart-money leaderboard markets list. READ-GUARDED. Parser COPIED
    from dog/main/scanners/scan.py."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return []
    if isinstance(raw, dict) and raw.get("success") is False:
        return []
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return []
    return markets


def _sm_for(markets, coin):
    """Net smart-money lean for `coin` from the leaderboard markets list. Returns
    (direction, tilt_pct) or (None, 0.0). Aggregates the long/short rows for the
    matched token (dog/bison parse: pct_of_top_traders_gain by direction), then
    bands via scoring.sm_short_tilt. Token match is case-insensitive; XYZ rows
    carry a `dex` so the bare crypto name and the xyz proxy don't collide."""
    want = str(coin).upper()
    is_xyz = want.startswith("XYZ:")
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        dex = str(m.get("dex", "") or "")
        # match the bare token; require the dex tag to agree (xyz row for xyz name)
        row_is_xyz = bool(dex) or token.startswith("XYZ:")
        bare = token.split(":", 1)[-1] if ":" in token else token
        want_bare = want.split(":", 1)[-1] if ":" in want else want
        if bare != want_bare or row_is_xyz != is_xyz:
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
    return scoring.sm_short_tilt(long_pct, short_pct)


# ── 4h CANDLES + per-asset funding (bobcat dex-aware + dog funding, read-guarded) ──

def _asset_data(ctx, coin, dex):
    """{candles_4h, funding} for `coin` or None. READ-GUARDED. dex-aware (bobcat);
    pulls 4h candles for the downtrend gate + the 8h funding rate (dog)."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["4h"],
        "include_funding": True,
        "include_order_book": False,
        "dex": dex,
    })
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    ac = d.get("asset_context", d.get("assetContext", {})) or {}
    funding = scoring.safe_float(ac.get("funding", d.get("funding", 0)))
    return {"candles_4h": candles.get("4h", []) or [], "funding": funding}


# ── ctx.state: recent-signal dedup (bobcat/bison pattern) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(str(coin).upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    universe = inputs.get("universe", _UNIVERSE_DEFAULT)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    leaderboard_limit = int(inputs.get("leaderboardLimit", _DEFAULT_LEADERBOARD_LIMIT))
    max_emit = int(inputs.get("maxEmit", _DEFAULT_MAX_EMIT))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    crowded_funding = float(inputs.get("crowdedLongFundingThreshold", _DEFAULT_CROWDED_FUNDING))

    # marginPct: PERCENT in (0,100]. Defensive fraction guard (dire/koala/bobcat
    # pattern): a value <= 1.0 is a pasted FRACTION (e.g. 0.15) -> *100 -> 15.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0

    # ── account + held ──
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        _persist(ctx, now, {"emitted": False, "gate": "no_account"})
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    # ── MASTER RISK-OFF GATE: market-wide funding regime ──
    regime = _fetch_regime(ctx)
    if regime == "SHORT_CROWDED":
        # Crowd already short — no squeeze fuel for a new short. Idle.
        print(f"[hyena.scan] WAITING — regime SHORT_CROWDED; no new shorts. "
              f"held={sorted(held_set)}", file=sys.stderr)
        _persist(ctx, now, {"emitted": False, "gate": "regime_short_crowded",
                            "regime": regime, "held": sorted(held_set)})
        return []
    # LONG_CROWDED -> full go. NEUTRAL/None -> proceed but each name needs the
    # per-asset crowded-long funding fallback to confirm (handled below + scoring).
    regime_permits = (regime == "LONG_CROWDED")

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── smart-money markets (one read, reused per name) ──
    markets = _fetch_markets(ctx, leaderboard_limit)

    # ── score every SHORT-eligible name (held + recently-signaled filtered BEFORE
    #    the per-asset MCP fetch) ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin:
            continue
        cu = str(coin).upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin, _dex_for(coin, inputs))
        if not md:
            continue
        asset_funding = md["funding"]

        # Per-asset regime fallback: when the market-wide regime doesn't permit
        # (NEUTRAL/None), a name can still short if its OWN funding is crowded-long
        # (positive beyond threshold). A SHORT_CROWDED market is already excluded.
        eff_regime = regime
        if not regime_permits:
            if asset_funding > crowded_funding:
                eff_regime = None   # signals "fallback path" to scoring (+1, not +3)
            else:
                continue            # no market-wide AND no per-asset confirmation

        sm = _sm_for(markets, coin)
        th = scoring.score_short(coin, md["candles_4h"], sm, eff_regime,
                                 asset_funding, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        note = (f"WAITING (regime={regime}, no SHORT-eligible name >= min score "
                f"{min_score:.0f})")
        print(f"[hyena.scan] {note}; scanned={scanned} held={sorted(held_set)}",
              file=sys.stderr)
        _persist(ctx, now, {"emitted": False, "gate": "no_candidate",
                            "regime": regime, "scanned": scanned,
                            "held": sorted(held_set), "note": note})
        return []

    # ── emit the top 1-2 SHORTs (highest score) ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    emitted = []
    for c in candidates[:max(1, max_emit)]:
        signaled[c["asset"].upper()] = now
        out.append({
            "asset": c["asset"],
            "direction": "SHORT",             # SHORT_ONLY — never long
            "marginPct": margin_pct,          # PERCENT (0,100] — runtime sizes the dollars
            "leverage": c["leverage"],        # clamped [1,5] then venue cap
            "data": {
                "score": c["score"],
                "direction": "SHORT",
                "leverage": c["leverage"],
                "reasons": c["reasons"],
                "smDirection": c["smDirection"],
                "smTiltPct": c["smTiltPct"],
                "downStrength": c["downStrength"],
                "priceChange4hPct": c["priceChange4hPct"],
                "fundingRegime": c["fundingRegime"],
                "assetFunding": c["assetFunding"],
                "heldAssets": sorted(held_set),
            },
        })
        emitted.append({"asset": c["asset"], "score": c["score"],
                        "leverage": c["leverage"]})

    top = emitted[0]
    print(f"[hyena.scan] EMIT {len(out)} short(s); top {top['asset']} SHORT "
          f"score={top['score']} {top['leverage']}x regime={regime} | "
          f"{candidates[0]['reasons'][:5]}", file=sys.stderr)
    _persist(ctx, now, {"emitted": True, "count": len(out), "signals": emitted,
                        "candidates": len(candidates), "regime": regime,
                        "held": sorted(held_set)})

    # ── persist dedup map + this tick's result EVERY tick (observability) ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled,
                              "result": {"emitted": True, "count": len(out),
                                         "signals": emitted, "regime": regime,
                                         "ts": now}})
        except Exception as exc:  # noqa: BLE001
            print(f"[hyena.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out


def _persist(ctx, now, result):
    """Append a concise per-tick result to ctx.state (observability). Guarded —
    a disabled history store or append failure must not crash the tick. (The EMIT
    path persists the full signaled map inline; this is the non-emit path.)"""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": result.get("emitted", False),
                          "result": dict(result, ts=now)})
    except Exception as exc:  # noqa: BLE001
        print(f"[hyena.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
