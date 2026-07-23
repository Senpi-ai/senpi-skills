"""HEDGEHOG — supervised scanner (Runtime 3.0 port of the v2 HEDGEHOG basket).

Equal-weight BTC + ETH + SOL basket; each asset evaluated INDEPENDENTLY (long OR
short). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - iterates the universe, skipping held + recently-signaled assets,
  - scores each remaining asset via the pure, HARD-GATED `scoring.build_thesis`
    (4h trend non-neutral + SM direction agrees + SM tilt >= floor),
  - emits the SINGLE highest-scoring candidate at/above `minScore`
    (v2 main() emitted only `best`); over several ticks the basket fills to 3 slots.

Read-only + single-pass — emits a `marginPct` intent (PERCENT, flat per leg) plus
a `leverage`; the runtime sizes the dollars, owns cooldowns/risk gates, and trails
the per-position DSL exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs hedgehog-producer.py v1.0.1:
  - v2 computed marginUsd = account_value * marginPct (marginPct stored as a
    FRACTION, 0.1) and emitted marginUsd. Runtime 3.0 sizes from a PERCENT in
    (0,100], so this port emits `marginPct` top-level and converts the v2 fraction
    x100 (0.1 -> 10). The defensive "<=1.0 means a pasted fraction, x100" guard is
    applied so an operator who pastes 0.10 still gets 10%. The PER-LEG flat sizing
    (no conviction tiers — every leg is the same %) is preserved verbatim.
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick;
    the runtime fills the 3-slot basket across ticks.
  - v2 SM direction (fetch_sm_direction): long_ratio >= 50 -> LONG (tilt=long_ratio),
    else SHORT (tilt=100-long_ratio); a not-found token -> (None, 0.0); a found token
    with zero total -> ("NEUTRAL", 50.0). Reproduced VERBATIM (these thresholds DIFFER
    from Bison's 58/42 band — do not borrow Bison's).
  - v2 fetched candle_intervals ["1h","4h"], include_funding=False,
    include_order_book=False. Preserved exactly (HEDGEHOG scores no funding/RSI/
    volume factors — simpler than Bison).
  - v2 recent-signals JSON cache -> ctx.state dedup map (same 240s TTL, same 4x-TTL
    prune semantics).
  - v2 read-sanity guard (margin in use + empty positions -> skip tick) ported verbatim.
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



# v2 defaults (hedgehog-producer.py / hedgehog-config.json)
_UNIVERSE_DEFAULT = ["BTC", "ETH", "SOL"]
_DEFAULT_RECENT_TTL = 240        # v1.0.1 RECENT_SIGNAL_TTL_SEC — race-window dedup
_MAX_LEVERAGE = 5                # v1.0.1 MAX_LEVERAGE
_DEFAULT_LEVERAGE = 5            # v1.0.1 DEFAULT_LEVERAGE


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    HEDGEHOG's universe is crypto majors only, so this returns '' in practice."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[hedgehog.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
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
        account_value = max(account_value, scoring._f(ms, "accountValue", default=0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos, "szi", default=0)
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
        _use = max(_use, scoring._f(_ms, "totalMarginUsed", default=0),
                   abs(scoring._f(_ms, "totalNtlPos", default=0)))
    if _use > 1.0 and not positions:
        print("[hedgehog.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt_pct). READ-GUARDED.

    Verbatim thresholds (DIFFER from Bison): long_ratio >= 50 -> ("LONG", long_ratio);
    else -> ("SHORT", 100 - long_ratio). Token not found -> (None, 0.0). Token found
    but total tilt == 0 -> ("NEUTRAL", 50.0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — SM is a hard gate; a read failure -> None (asset skipped, tick survives)
        print(f"[hedgehog.scan] leaderboard_get_markets read failed (smart-money -> None): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw or (isinstance(raw, dict) and raw.get("success") is False):
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
        if not _sm_row_matches(m, token, coin):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m, "pct_of_top_traders_gain", "longPct", default=0)
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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def _asset_data(ctx, coin):
    """{candles_1h, candles_4h} for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h, no funding, no order book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[hedgehog.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    return {"candles_1h": candles.get("1h", []), "candles_4h": candles.get("4h", [])}


# ── ctx.state: recent-signal dedup (port of v1.0.1 recent-signals.json) ──

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


def scan(inputs, ctx):
    now = time.time()
    universe = inputs.get("universe", _UNIVERSE_DEFAULT)
    min_score = float(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 10))     # PERCENT in (0,100]
    leverage = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # Defensive: a pasted FRACTION (<=1.0, e.g. v2's 0.1) -> PERCENT (x100).
    if margin_pct <= 1.0:
        margin_pct = margin_pct * 100.0

    # leverage clamp: v1.0.1 min(int(leverage), MAX_LEVERAGE)
    leverage = max(1, min(leverage, _MAX_LEVERAGE))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible universe candidate; held + recently-signaled are
    #    filtered BEFORE the per-asset MCP fetch, as in v2 main() ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin:
            continue
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin)
        if not md:
            continue
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(coin, md["candles_1h"], md["candles_4h"], sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[hedgehog.scan] WAITING — no basket leg with 4h trend + SM agreement "
              f"(min score {min_score:.0f}); scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[hedgehog.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons']}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — flat per leg; runtime sizes the dollars
            "leverage": leverage,             # 1..5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "trend4h": best["trend_4h"],
                "trend4hStrength": best["trend_4h_strength"],
                "trend1h": best["trend_1h"],
                "smDirection": best["sm_direction"] or "NEUTRAL",
                "smTiltPct": best["sm_tilt_pct"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[hedgehog.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
