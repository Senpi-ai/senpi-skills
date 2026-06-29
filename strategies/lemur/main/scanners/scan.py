"""LEMUR — supervised scanner (Runtime 3.0 port of the v2 LEMUR IPOP producer).

A faithful port of the v2 "Pre-IPO Perpetual (IPOP) trend follower" on
Hyperliquid XYZ (lemur-producer.py v1.0.1 / SKILL.md v1.0.0). Per tick it:

  1. Reads account state + held positions (dual-DEX equity via max(), never
     sum()).
  2. Auto-discovers the IPOP universe from the live xyz: instrument list via the
     structural funding signature (market_list_instruments, dex="xyz"):
     abs(funding) <= 1e-7 AND max_leverage <= 5 AND dayNtlVlm >= $100k AND not
     delisted. Today that returns [xyz:SPCX]; it auto-expands/auto-drops as
     trade.xyz lists/IPOs names.
  3. Scores every non-held, non-recently-signaled IPOP through the pure
     `scoring.build_thesis` (hard gate: 4h trend non-neutral + SM agreement;
     4-component score, max ~9). Per-asset market data via market_get_asset_data
     (1h/4h candles) and SM lean via leaderboard_get_markets.
  4. Emits the SINGLE highest-scoring candidate at/above `minScore` (v2 main()
     emitted only `best`), sized by a flat marginPct intent at IPOP-capped
     leverage.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the
runtime sizes the dollars, owns cooldowns/risk gates/slots, and trails the DSL
exit. Held-asset suppression + per-tick signal dedup live in ctx.state
(belt-and-suspenders alongside the runtime's per-asset cooldown gate).

FIDELITY NOTES vs lemur-producer.py v1.0.1:
  - v2 stored `marginPct` as a FRACTION (0.15) and computed marginUsd =
    account_value * 0.15, emitting a USD figure. This port emits `marginPct` as
    a PERCENT (15) at the top level and lets the runtime size
    (marginPct/100)*withdrawable. The defensive `<=1.0 means a pasted fraction
    -> x100` guard converts a fraction supplied via inputs. Sizing is otherwise
    identical (15% of equity at default config).
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL semantics:
    240s race-window, pruned at 4x TTL).
  - v2 leverage = min(config_leverage, instrument max_leverage_cap, MAX_LEVERAGE=5).
    Preserved verbatim in scoring.leverage_for.
  - DROPPED (read-only scan cannot mutate): the v2 producer had no explicit
    order-lifecycle management (no cancel_order / stale-order purge), so nothing
    is dropped on that front. The v2 push_signal / record_signal mutations are
    replaced by returning plain dicts + ctx.state, per the scan() contract.
  - The v2 sub-DEX-aware account read (cfg.get_positions) is ported verbatim
    including the read-sanity guard (margin in use + empty positions -> skip tick).
"""

import sys
import time

import scoring

_DEFAULT_TTL = 240               # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup
_DEFAULT_MIN_SCORE = 5           # v2 DEFAULT_MIN_SCORE
_DEFAULT_LEVERAGE = 3            # v2 DEFAULT_LEVERAGE (config default; capped per-instrument)
_DEFAULT_MARGIN_PCT = 15         # v2 marginPct 0.15 (FRACTION) -> 15 (PERCENT)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick (per the contract, ANY exception rolls the tick to []).
    Returns None on failure so the existing degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[lemur.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    if not getattr(ctx, "wallet", None):
        return 0.0, []
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
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
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
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[lemur.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _fetch_ipop_universe(ctx, inputs):
    """Auto-discover the IPOP universe from the live xyz: instrument list.
    READ-GUARDED (market_list_instruments). Returns the v2-shape list
    [{name, max_leverage, funding, vol_usd}] via the pure scoring filter."""
    max_funding = float(inputs.get("ipopFundingMaxAbs", scoring.DEFAULT_IPOP_FUNDING_MAX))
    max_lev = int(inputs.get("ipopMaxLeverageCap", scoring.DEFAULT_IPOP_LEV_CAP))
    min_vol = float(inputs.get("ipopMinDailyVolUsd", scoring.DEFAULT_IPOP_MIN_VOL))

    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw:
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    return scoring.filter_ipop_universe(instruments, max_funding, max_lev, min_vol)


def _asset_data(ctx, coin):
    """{candles_1h, candles_4h} for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h candles; no funding/order-book)."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["1h", "4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": "xyz" if coin.lower().startswith("xyz:") else "",
    })
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    return {
        "candles_1h": candles.get("1h", []) if isinstance(candles, dict) else [],
        "candles_4h": candles.get("4h", []) if isinstance(candles, dict) else [],
    }


def _get_sm_direction(ctx, asset):
    """Port of v2 fetch_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0.0) when
    SM data is unavailable for the asset. READ-GUARDED.

    Verbatim thresholds: long_ratio >= 50 -> LONG (ratio), else SHORT
    (100-ratio); total<=0 -> ("NEUTRAL", 50.0)."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw:
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
        if token != asset.upper():
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


def _resolve_margin_pct(inputs):
    """marginPct intent as a PERCENT in (0,100]. v2 stored a FRACTION (0.15);
    convert with the defensive `<=1.0 means a pasted fraction -> x100` guard."""
    mp = scoring._f(inputs.get("marginPct", _DEFAULT_MARGIN_PCT), _DEFAULT_MARGIN_PCT)
    if mp <= 1.0:                 # a fraction was pasted (e.g. 0.15) -> percent
        mp *= 100.0
    return mp


def scan(inputs, ctx):
    now = time.time()
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    config_leverage = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    margin_pct = _resolve_margin_pct(inputs)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[lemur.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    universe = _fetch_ipop_universe(ctx, inputs)
    if not universe:
        print("[lemur.scan] WAITING — no IPOPs in universe (no instruments match "
              "funding+leverage+volume signature)", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe", "held": held_assets})
        return []

    # ── score every eligible IPOP (held + recently-signaled filtered BEFORE the
    #    per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for inst in universe:
        coin = inst["name"]
        if coin.upper() in held_set:
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
            th["max_leverage_cap"] = inst["max_leverage"]
            candidates.append(th)

    if not candidates:
        print(f"[lemur.scan] WAITING — no IPOP setup with 4h trend + SM agreement "
              f"(min score {min_score:.0f}); scanned={scanned} "
              f"universe={[u['name'] for u in universe]} held={held_assets}", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidate", "scanned": scanned,
                  "universe": [u["name"] for u in universe], "held": held_assets})
        return []

    # v2 emitted exactly the single best (highest score).
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    # leverage: min(config_leverage, instrument cap, venue MAX_LEVERAGE=5) — verbatim.
    leverage = scoring.leverage_for(config_leverage,
                                    best.get("max_leverage_cap", scoring.MAX_LEVERAGE))

    signaled[best["coin"].upper()] = now
    result = {"ts": now, "emitted": True, "gate": "pass", "coin": best["coin"],
              "direction": best["direction"], "score": best["score"], "leverage": leverage,
              "marginPct": round(margin_pct, 4), "held": held_assets,
              "reasons": best["reasons"]}
    print(f"[lemur.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
          f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
    _persist(result)

    return [{
        "asset": best["coin"],
        "direction": best["direction"],
        "marginPct": margin_pct,             # SIZING INTENT — PERCENT (0,100]; runtime sizes USD
        "leverage": leverage,                # min(config, instrument cap, 5); runtime clamps to venue max
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": best["reasons"],
            "trend4h": best["trend_4h"],
            "trend4hStrength": best["trend_4h_strength"],
            "trend1h": best["trend_1h"],
            "smDirection": best["sm_direction"],
            "smTiltPct": best["sm_tilt_pct"],
            "heldAssets": held_assets,
            "ipopFlag": True,                # marker: this is a pre-IPO product
        },
    }]
