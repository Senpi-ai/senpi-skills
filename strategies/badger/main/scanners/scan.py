"""BADGER — supervised scanner (Runtime 3.0 port of the v2 Badger OI-divergence
breakout anticipator).

Multi-asset, whitelist-gated (BTC/ETH/SOL/HYPE by default). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - for each non-held, non-recently-signaled whitelist coin applies the THREE hard
    gates verbatim from v2 build_thesis:
      GATE 1  price breaks the prior 24h range edge (breakout_signal),
      GATE 2  open interest RISING >= oiRisingMinPct over 1h (conviction gate — the
              core edge; uses market_get_asset_data's oi_velocity object, else a
              self-computed delta from the per-asset OI baseline in ctx.state),
      GATE 3  smart money (leaderboard_get_markets) agrees with the break + tilt >= floor,
  - scores survivors via the pure `scoring.build_thesis`,
  - emits the SINGLE highest-scoring candidate at/above `minScore` (v2 main()
    emitted only `best`), sized by a flat margin PERCENT + leverage clamp.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (badger-producer.py v1.0.1):
  - v2 persisted the per-asset last-seen openInterest to state/oi-state.json and
    self-computed the OI delta on the next tick when market_get_asset_data's
    oi_velocity object was null. This port stores that OI baseline map in
    ctx.state instead (same warm-up semantics: a freshly-started Badger has no
    prior OI for an asset on its first tick, so the OI gate returns 'unavailable'
    and the asset is skipped until the baseline is seeded). v2 refreshed the OI
    baseline on EVERY tick an asset was fetched (even when it bailed early on
    insufficient candles or no breakout); this port preserves that — the baseline
    is updated for every asset whose market_get_asset_data read succeeds.
  - v2 sizing used marginPct (a FRACTION, 0.20 in badger-config.json) * account_value
    -> marginUsd. This port uses marginPct=20 (a PERCENT) and emits `marginPct`;
    the runtime sizes (marginPct/100)*withdrawable. Value preserved (0.20 -> 20%).
    Defensive koala-pattern guard: a value <= 1.0 is treated as a pasted v2
    fraction and x100'd.
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup map
    (same TTL semantics, same 4x-TTL prune horizon).
  - leverage clamp min(leverage, MAX_LEVERAGE=5) preserved verbatim.
  - FLAG: the v2 runtime.yaml template said strategy.margin_pct: 25, which
    CONTRADICTS the producer + config (marginPct 0.20 = 20%). Per the spec
    source-of-truth order (producer + config + SKILL over a stale runtime template)
    this port uses 20%.
"""

import sys
import time

import scoring

# v2 defaults (badger-producer.py / badger-config.json)
_DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_MIN_SCORE = 5                 # v2 DEFAULT_MIN_SCORE / config minScore
_DEFAULT_BREAKOUT_LOOKBACK = 24       # v2 DEFAULT_BREAKOUT_LOOKBACK (prior 24h, 1h bars)
_DEFAULT_OI_RISING_MIN_PCT = 2.0     # v2 DEFAULT_OI_RISING_MIN_PCT — the conviction gate
_DEFAULT_OI_STRONG_PCT = 5.0         # v2 DEFAULT_OI_STRONG_PCT — strongly-building bonus
_DEFAULT_SM_TILT_MIN = 55            # v2 DEFAULT_SM_TILT_MIN
_DEFAULT_SM_STRONG = 70              # v2 DEFAULT_SM_STRONG
_DEFAULT_MARGIN_PCT = 20.0           # v2 config marginPct 0.20 -> 20 PERCENT
_DEFAULT_LEVERAGE = 5                # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 5                    # v2 MAX_LEVERAGE (hardcoded clamp)
_DEFAULT_RECENT_TTL = 240           # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Badger's universe is all main-DEX majors, so this only ever returns ''."""
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
        print(f"[badger.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[badger.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _asset_data(ctx, coin):
    """Raw market_get_asset_data doc for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h candles, no funding, no order book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[badger.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    return md


def _oi_velocity_1h(asset_data, coin, oi_baseline):
    """Return (oi_change_pct_1h, source, cur_oi). Prefers the runtime oi_velocity
    object; falls back to a self-computed delta vs the per-asset OI baseline in
    ctx.state. Returns (None, 'unavailable', cur_oi) when neither is usable yet.

    Ported verbatim from v2 oi_velocity_1h (the JSON last-OI cache is replaced by
    the oi_baseline dict passed in from ctx.state)."""
    data = asset_data.get("data", {}) if isinstance(asset_data, dict) else {}
    asset_ctx = data.get("asset_context", {}) or {}
    cur_oi = scoring._f(asset_ctx.get("openInterest"))
    oiv = data.get("oi_velocity")
    if isinstance(oiv, dict):
        ch = oiv.get("oi_change_pct")
        if isinstance(ch, dict) and ch.get("1h") is not None:
            try:
                return float(ch["1h"]), "oi_velocity", cur_oi
            except (TypeError, ValueError):
                pass
    if cur_oi > 0:
        prev = oi_baseline.get(coin.upper())
        if prev and scoring._f(prev.get("oi", 0)) > 0:
            pct = ((cur_oi - scoring._f(prev["oi"])) / scoring._f(prev["oi"])) * 100
            return pct, "computed", cur_oi
    return None, "unavailable", cur_oi


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0.0).
    READ-GUARDED. Verbatim thresholds: long_ratio >= 50 -> LONG, else SHORT."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is GATE 3; a read fail just fails the gate
        print(f"[badger.scan] leaderboard_get_markets read failed (SM gate -> skip): {exc!r}",
              file=sys.stderr)
        return None, 0.0
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
        if token != coin.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
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


# ── ctx.state: recent-signal dedup + OI baseline (port of v2 JSON caches) ──

def _load_state(ctx):
    """Returns (signaled_map, oi_baseline_map) from the last clean tick."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    oi = last.get("oi_baseline", {})
    return (dict(sig) if isinstance(sig, dict) else {},
            dict(oi) if isinstance(oi, dict) else {})


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
    universe = [a.upper() for a in inputs.get("universe", _DEFAULT_UNIVERSE)]
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    lookback = int(inputs.get("breakoutLookbackHours", _DEFAULT_BREAKOUT_LOOKBACK))
    oi_min = float(inputs.get("oiRisingMinPct", _DEFAULT_OI_RISING_MIN_PCT))
    sm_min = float(inputs.get("smTiltMinPct", _DEFAULT_SM_TILT_MIN))
    lev_default = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # marginPct is a PERCENT in (0,100]. Defensive koala-pattern guard: a value
    # <= 1.0 (operator pasted the v2 FRACTION 0.20) is treated as a fraction and
    # x100'd so it never silently sizes ~100x small.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[badger.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled, oi_baseline = _load_state(ctx)
    signaled = _prune_signaled(signaled, ttl, now)

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
        d = md.get("data", {}) if isinstance(md, dict) else {}
        candles_1h = d.get("candles", {}).get("1h", []) if isinstance(d, dict) else []
        candles_4h = d.get("candles", {}).get("4h", []) if isinstance(d, dict) else []

        # Always refresh the OI baseline for this asset (warms the fallback cache),
        # exactly as v2 did on every successful market read.
        oi_now = scoring._f((d.get("asset_context", {}) or {}).get("openInterest")) if isinstance(d, dict) else 0.0

        if len(candles_1h) < lookback + 1 or len(candles_4h) < 6:
            if oi_now > 0:
                oi_baseline[cu] = {"oi": oi_now, "ts": now}
            continue

        # GATE 1 — price breakout
        bo_dir, bo_mag = scoring.breakout_signal(candles_1h, lookback)
        if bo_dir is None:
            if oi_now > 0:
                oi_baseline[cu] = {"oi": oi_now, "ts": now}
            continue
        direction = "LONG" if bo_dir == "UP" else "SHORT"

        # GATE 2 — OI rising (the conviction gate, Badger's core edge)
        oi_pct, oi_src, _cur = _oi_velocity_1h(md, coin, oi_baseline)
        if oi_now > 0:                                       # refresh AFTER the read (v2 order)
            oi_baseline[cu] = {"oi": oi_now, "ts": now}
        if oi_pct is None:
            continue                                         # OI unknown (cache warming) — skip
        if oi_pct < oi_min:
            continue                                         # breakout without rising OI = fakeout

        # GATE 3 — Smart-Money agreement
        sm_dir, sm_tilt = _get_sm_direction(ctx, coin)
        if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
            continue
        if sm_tilt < sm_min:
            continue

        th = scoring.build_thesis(coin, direction, bo_dir, bo_mag, oi_pct, oi_src,
                                  candles_1h, candles_4h, sm_dir, sm_tilt, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[badger.scan] WAITING — no OI-confirmed breakout w/ SM agreement "
              f"(min score {min_score:.0f}); scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]

        leverage = min(lev_default, _MAX_LEVERAGE)          # v2 clamp verbatim

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[badger.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:6]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "breakoutDir": best["breakout_dir"],
                "breakoutMagPct": best.get("breakout_mag_pct") or 0.0,
                "oiChangePct": best.get("oi_change_pct") or 0.0,
                "oiSource": best.get("oi_source") or "unavailable",
                "trend4h": best["trend_4h"],
                "smDirection": best["sm_direction"],
                "smTiltPct": best.get("sm_tilt_pct") or 0.0,
                "volumeTrendPct": best.get("volume_trend_pct") or 0.0,
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + OI baseline + this tick's result every tick; bounded
    #    by state_history_max_count. Read back via ctx.state.last(). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "oi_baseline": oi_baseline, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[badger.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal or lose an OI baseline: {exc!r}", file=sys.stderr)
    return out
