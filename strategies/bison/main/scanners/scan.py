"""BISON — supervised scanner (Runtime 3.0 port of the v2 Bison conviction holder).

Multi-asset, whitelist-gated (BTC/ETH/SOL by default). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - resolves the whitelist universe (filtered to live, XYZ banned),
  - scores every non-held, non-recently-signaled candidate via the pure
    `scoring.build_thesis`,
  - emits the SINGLE highest-scoring candidate at/above `minScore`
    (v2 main() emitted only `best`), sized by the conviction margin tier.

Read-only + single-pass — emits a `marginPct` intent (PERCENT, conviction-tiered)
plus a `leverage`; the runtime sizes the dollars, owns cooldowns/risk gates, and
trails the DSL exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (bison-producer.py v3.0.2):
  - v2 fetched candidates via `get_top_assets(top_n)` = market_list_instruments
    sorted by 24h notional volume then filtered to the whitelist. Since the
    whitelist is only 3 liquid majors, the sort is cosmetic; this port iterates
    the whitelist directly (no market_list_instruments call), saving one MCP read
    and removing a dependency on the (currently flaky) instrument board. The SET
    of assets scored is identical. FLAGGED: if an operator widens `allowedAssets`
    to a large list, this port scores all of them rather than the top-N-by-volume
    — but v2's top_n default (10) >= whitelist size (3), so for the default config
    the behaviour is identical.
  - v2 conviction-scaled margin used marginPctBase=0.25 (a FRACTION) * account_value
    -> marginUsd. This port uses marginPctBase=25 (a PERCENT) and emits `marginPct`;
    the runtime sizes (marginPct/100)*withdrawable. The TIER MULTIPLIERS and CUTOFFS
    are verbatim (scoring.margin_tier_pct).
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL semantics).
"""

import sys
import time

import scoring


# v2.1 defaults (bison-producer.py / bison-config.json)
_ALLOWED_ASSETS_DEFAULT = ["BTC", "ETH", "SOL"]
_DEFAULT_RECENT_TTL = 180        # v3.0.1 RECENT_SIGNAL_TTL_SEC — race-window dedup
_DEFAULT_TIERS = [[12, 1.5], [10, 1.25]]   # informational; tiering lives in scoring.margin_tier_pct
_MAX_LEVERAGE = 10               # v2.1 MAX_LEVERAGE (hardcoded, not configurable)
_MIN_LEVERAGE = 7                # v2.1 MIN_LEVERAGE (hardcoded, not configurable)


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Bison bans XYZ at scan level, so this only ever returns '' in practice."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[bison.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[bison.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, coin):
    """Port of v2 get_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, pct) or (None, 0). READ-GUARDED.

    Verbatim thresholds: long_ratio > 58 -> LONG, < 42 -> SHORT, else NEUTRAL."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is a score contributor; never crash the tick
        print(f"[bison.scan] leaderboard_get_markets read failed (smart-money -> neutral): {exc!r}",
              file=sys.stderr)
        return None, 0
    if not raw:
        return None, 0
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0

    coin_long_pct = 0
    coin_short_pct = 0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if token != coin:
            continue
        found = True
        direction = str(m.get("direction", "")).lower()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if direction == "long":
            coin_long_pct = pct
        elif direction == "short":
            coin_short_pct = pct

    if not found:
        return None, 0
    total = coin_long_pct + coin_short_pct
    if total == 0:
        return "NEUTRAL", 50
    long_ratio = (coin_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return "LONG", long_ratio
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio
    return "NEUTRAL", 50


def _asset_data(ctx, coin):
    """{candles{}, funding} for `coin` or None. READ-GUARDED.
    Ported from v2 build_thesis's market_get_asset_data call (15m/1h/4h + funding)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["15m", "1h", "4h"],
            "include_funding": True,
            "include_order_book": False,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[bison.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    asset_ctx = d.get("asset_context", d) if isinstance(d, dict) else {}
    funding = scoring._f(asset_ctx.get("funding", d.get("funding", 0)))
    return {"candles": candles, "funding": funding}


# ── ctx.state: recent-signal dedup (port of v3.0.1 recent-signals.json) ──

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
    allowed = inputs.get("allowedAssets", _ALLOWED_ASSETS_DEFAULT)
    min_score = float(inputs.get("minScore", 11))
    base_margin_pct = float(inputs.get("marginPctBase", 25))   # PERCENT in (0,100]
    lev_default = int(inputs.get("leverageDefault", 10))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible whitelist candidate (XYZ banned; held + recently
    #    signaled filtered BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for coin in allowed:
        if not coin or coin.lower().startswith("xyz:"):   # XYZ banned at scan level (v2.1)
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
        candles = md["candles"]
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(
            coin,
            candles.get("15m", []), candles.get("1h", []), candles.get("4h", []),
            md["funding"], sm, inputs,
        )
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[bison.scan] WAITING — no conviction thesis (min score {min_score:.0f}); "
              f"scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        # conviction-scaled margin PERCENT (verbatim tier multipliers/cutoffs)
        margin_pct = scoring.margin_tier_pct(best["score"], base_margin_pct)

        # leverage: clamp v2.1 default into [MIN_LEVERAGE, MAX_LEVERAGE] (verbatim)
        leverage = min(lev_default, _MAX_LEVERAGE)
        leverage = max(leverage, _MIN_LEVERAGE)

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[bison.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:6]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 7..10; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "directionSource": best["directionSource"],
                "reasons": best["reasons"],
                "trend4h": best["trend_4h"],
                "trend1h": best["trend_1h"],
                "momentum1hPct": best["momentum_1h"],
                "momentum4hPct": best["momentum_4h"],
                "smDirection": best["sm_direction"] or "NEUTRAL",
                "smPct": best["sm_pct"],
                "funding": best["funding"],
                "rsi": best["rsi"],
                "volumeTrendPct": best["volume_trend"],
                "oiProxyPct": best["oi_proxy"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[bison.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
