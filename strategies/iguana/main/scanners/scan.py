"""IGUANA — supervised scanner (Runtime 3.0 port of the v2 IGUANA index trend producer).

Two-asset broad-index trend-follower on the Hyperliquid XYZ (HIP-3) DEX: xyz:SP500 +
xyz:XYZ100. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum();
    read-sanity guard for the funding/$0 corrupt-clearinghouse glitch),
  - skips held + recently-signaled assets BEFORE fetching their candles (v2 main()),
  - computes each remaining index's 4-day trend strength (pure scoring.trend_strength),
  - picks the single strongest move past minTrendPct (scoring.pick_strongest_trend),
  - scores it via the pure scoring.build_thesis and emits ONE signal at/above minScore.

Read-only + single-pass. Emits a flat `marginPct` intent (PERCENT) plus a clamped
`leverage`; the runtime sizes the dollars, owns cooldowns / daily caps / drawdown halt,
and trails the DSL exit. No daemon, no push_signal, no create_position. The v2 LLM gate
was pass-through, so the entry action runs in rule mode — the scan already applied every
filter.

FIDELITY NOTES vs the v2 producer (iguana-producer.py v1.0.1):
  - v2 sized via `marginUsd = account_value * marginPct(0.20)` and emitted a top-level
    `marginUsd`. Runtime 3.0 sizes off a top-level `marginPct` PERCENT, so this port
    converts the v2 marginPct FRACTION (0.20) to a PERCENT (20) and emits `marginPct`;
    the runtime computes (marginPct/100)*withdrawable. The fraction was the only
    sizing input, so behaviour is preserved.
  - v2 used config marginPct directly (0.20). The runtime.yaml `marginPct` input is the
    PERCENT (20). If an operator passes a value <= 1.0 (i.e. a fraction was left in by
    mistake) this scan *100-normalizes it* so a 0.20 still becomes 20% — defensive,
    matches dire/polar fraction->percent handling.
  - v2 read positions via cfg.get_positions (clearinghouse, dual-DEX equity via max(),
    read-sanity guard). Ported VERBATIM into _get_account here.
  - v2 recent-signals JSON cache + 240s TTL (pruned at 4x TTL) -> ctx.state dedup map
    with identical TTL semantics.
  - v2 emitted exactly one signal (the strongest trend `best`). Preserved: scan() emits
    <= 1 signal/tick (slots: 1).
  - v2 LLM gate was pass-through (decision_mode llm, min_confidence 7 but honor-signal
    prompt). Ported to decision_mode: rule — the scan owns the full gate stack.
  - DSL time cuts (48h hard_timeout + 8h weak_peak_cut) are KEPT from v2 (XYZ weekend
    pricing-gap thesis), NOT disabled. This DIFFERS from the single-asset Kodiak family
    (time cuts off) — preserved verbatim per the port directive; FLAG for review if the
    fleet decides index ports should also drop time cuts.
"""

import sys
import time

import scoring

# v2 defaults (iguana-producer.py — preferred over config.json per the port directive)
_DEFAULT_WHITELIST = ["xyz:SP500", "xyz:XYZ100"]   # v2 DEFAULT_WHITELIST
_DEFAULT_TREND_LOOKBACK = 24          # 24 x 4h bars = 4 days (v2 DEFAULT_TREND_LOOKBACK)
_DEFAULT_MIN_TREND_PCT = 1.5          # v2 DEFAULT_MIN_TREND_PCT
_DEFAULT_MIN_SCORE = 4                # v2 DEFAULT_MIN_SCORE
_DEFAULT_LEVERAGE = 3                 # v2 DEFAULT_LEVERAGE
_DEFAULT_MAX_LEVERAGE = 5            # v2 MAX_LEVERAGE
_DEFAULT_MARGIN_PCT = 20             # PERCENT (v2 config marginPct 0.20 fraction -> 20 percent)
_DEFAULT_TTL = 240                   # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset, inputs):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''. Honor an
    explicit inputs.dex if set, else derive from the xyz: prefix."""
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [coin,...]) from strategy_get_clearinghouse_state.

    READ-GUARDED — a read error must never roll back the whole tick. Dual-DEX equity
    collapse: account_value via max() across main/xyz (two views of ONE cross-margined
    wallet — summing double-counts the shared free balance -> 2x sizing). assetPositions
    are per-sub-DEX, enumerated across both sections. Ported VERBATIM from v2
    cfg.get_positions, including the read-sanity guard (margin in use + empty positions
    -> skip tick, returns (0.0, []))."""
    if not ctx.wallet:
        return 0.0, []
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — read error must not roll back the whole tick
        print(f"[iguana.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    if not isinstance(data, dict):
        return 0.0, []

    held = []
    account_value = 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        try:
            # one wallet, two sub-DEX views -> count equity ONCE (max, not sum;
            # summing double-counts the shared free balance -> 2x sizing).
            account_value = max(account_value, float(ms.get("accountValue", 0)))
        except (TypeError, ValueError):
            pass
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                continue
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held.append(coin)

    # read-sanity guard (funding/$0 glitch 2026-06, ported VERBATIM from v2): a corrupt
    # clearinghouse read can report margin/notional IN USE while returning an EMPTY
    # positions list; sizing or the held-asset dedup off that re-enters held names
    # (pyramiding) and mis-sizes. Skip the tick (return 0.0 account -> caller WAITs).
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        try:
            _use = max(_use,
                       float(_ms.get("totalMarginUsed", 0) or 0),
                       abs(float(_ms.get("totalNtlPos", 0) or 0)))
        except (TypeError, ValueError):
            pass
    if _use > 1.0 and not held:
        return 0.0, []
    return account_value, held


def _fetch_4h_candles(ctx, asset, dex):
    """READ-GUARD: fetch the asset's 4h candles. A read error must never roll back
    the whole tick — returns [] (degrade — skip this asset rather than crash)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["4h"],
            "include_funding": False,        # XYZ DEX — no funding needed
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[iguana.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return []
    if not md:
        return []
    data = md.get("data", md) if isinstance(md, dict) else {}
    if not isinstance(data, dict):
        return []
    candles = data.get("candles", {}) or {}
    return candles.get("4h", []) or []


def _load_recent(ctx):
    """Read the dedup map from the last clean tick's state record."""
    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = last.get("recent", {})
    return dict(recent) if isinstance(recent, dict) else {}


def _prune_recent(recent, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in recent.items() if v >= cutoff}


def _was_recently_signaled(recent, coin, ttl, now):
    last = recent.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    whitelist = inputs.get("whitelist", _DEFAULT_WHITELIST)
    lookback = int(inputs.get("trendLookbackBars", _DEFAULT_TREND_LOOKBACK))
    min_pct = float(inputs.get("minTrendPct", _DEFAULT_MIN_TREND_PCT))
    min_score = int(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    leverage_cfg = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    max_leverage = int(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct: PERCENT in (0,100]. Defensive fraction->percent (a stray 0.20 -> 20).
    raw_margin = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    margin_pct = round(raw_margin * 100, 2) if 0 < raw_margin <= 1.0 else round(raw_margin, 2)

    account_value, held_assets = _get_account(ctx)
    held_set = {h.upper() for h in held_assets}

    recent = _prune_recent(_load_recent(ctx), ttl, now)

    out = []
    result = None

    if account_value <= 0:
        result = {"ts": now, "emitted": False, "gate": "no_account_value",
                  "held": held_assets, "note": "WAITING — no account value (or read-sanity skip)"}
        print("[iguana.scan] WAITING — no account value (or clearinghouse read-sanity skip)",
              file=sys.stderr)
    else:
        # ── per-asset 4-day trend strength; skip held + recently-signaled BEFORE the
        #    per-asset MCP fetch (exactly as v2 main()) ──
        candles_by_asset = {}
        strength_by_asset = {}
        for asset in whitelist:
            if asset.upper() in held_set or _was_recently_signaled(recent, asset, ttl, now):
                continue
            dex = _dex_for(asset, inputs)
            candles = _fetch_4h_candles(ctx, asset, dex)
            if len(candles) <= lookback:
                continue
            closes = [scoring._f(c, "close", "c") for c in candles]
            candles_by_asset[asset] = candles
            strength_by_asset[asset] = scoring.trend_strength(closes, lookback)

        picked = scoring.pick_strongest_trend(strength_by_asset, min_pct)
        if picked is None:
            strengths = {k: round(v, 2) if v is not None else None
                         for k, v in strength_by_asset.items()}
            result = {"ts": now, "emitted": False, "gate": "no_trend",
                      "strength": strengths, "held": held_assets,
                      "note": f"WAITING — neither index has a 4d move past {min_pct}%"}
            print(f"[iguana.scan] WAITING — no index past {min_pct}% 4d move; "
                  f"strength={strengths} held={held_assets}", file=sys.stderr)
        else:
            asset, strength = picked
            th = scoring.build_thesis(asset, strength, candles_by_asset[asset], inputs)
            if th is None or th["score"] < min_score:
                sc = th["score"] if th else None
                result = {"ts": now, "emitted": False, "gate": "score_low",
                          "coin": asset, "score": sc, "held": held_assets,
                          "note": f"WAITING — pick cleared trend gate but missed minScore {min_score}"}
                print(f"[iguana.scan] WAITING — {asset} score={sc}/{min_score} "
                      f"(cleared trend, missed minScore)", file=sys.stderr)
            else:
                leverage = min(leverage_cfg, max_leverage)
                if leverage <= 0 or margin_pct <= 0:
                    result = {"ts": now, "emitted": False, "gate": "sizing_unresolved",
                              "coin": asset, "score": th["score"], "held": held_assets,
                              "note": f"sizing unresolved lev={leverage} marginPct={margin_pct}"}
                    print(f"[iguana.scan] HOLD — {asset} sizing unresolved "
                          f"lev={leverage} marginPct={margin_pct}", file=sys.stderr)
                else:
                    recent[asset.upper()] = now
                    result = {"ts": now, "emitted": True, "gate": "pass",
                              "coin": asset, "direction": th["direction"],
                              "score": th["score"], "leverage": leverage,
                              "marginPct": margin_pct, "trendPct": th["trend_pct"],
                              "held": held_assets, "reasons": th["reasons"]}
                    print(f"[iguana.scan] EMIT {asset} {th['direction']} score={th['score']} "
                          f"{leverage}x marginPct={margin_pct}% 4d={th['trend_pct']:+.1f}% "
                          f"| {th['reasons']}", file=sys.stderr)
                    out = [{
                        "asset": asset,
                        "direction": th["direction"],
                        "marginPct": margin_pct,      # PERCENT in (0,100] — runtime sizes (marginPct/100)*withdrawable
                        "leverage": leverage,         # v2 leverage clamped to maxLeverage
                        "data": {
                            "score": float(th["score"]),
                            "leverage": float(leverage),
                            "marginPct": margin_pct,
                            "direction": th["direction"],
                            "trendPct": th.get("trend_pct") or 0.0,
                            "volumeTrendPct": th.get("volume_trend_pct") or 0.0,
                            "reasons": th["reasons"],
                            "heldAssets": held_assets,
                        },
                    }]

    # ── persist dedup map + this tick's result EVERY tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[iguana.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
