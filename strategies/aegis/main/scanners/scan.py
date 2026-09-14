"""AEGIS — supervised scanner (regime-adaptive dynamic hedge, Runtime 3.0).

THESIS — read the TAPE, not the crowd. Where Phalanx follows the proven
cohort's headcount (a people signal), Aegis reads market structure:
market-wide funding crowding, haven-asset trends (gold/JPY), per-asset
OI velocity, and trend structure. The signals are uncorrelated by design.

Per tick:
  1. Read account state + held positions (dual-DEX, read-sanity guard).
  2. Read market-wide funding regime (market_get_funding_regime).
  3. Read haven trends: xyz:GOLD + xyz:JPY 4h candles (xyz:JPY is USD/JPY).
  4. Compute graduated regime score (-2.0 .. +2.0).
  5. If |regime_score| < 0.5 -> return [] (cash in neutral — don't bleed).
  6. For each universe asset:
     - Read 4h candles + OI velocity + funding rate.
     - Determine direction from regime score + asset role.
     - Compute conviction = regime intensity * trend alignment * OI * funding.
     - Filter by minScore; scale marginPct by conviction + regime intensity.
  7. Rank, fill only the free slots (maxSlots minus positions on either dex), emit.

Read-only + single-pass. marginPct is a PERCENT in (0,100]. No daemon,
no push_signal, no create_position. EVERY MCP call is read-guarded.
"""

import sys
import time

import scoring

# ── defaults (overridable via runtime.yaml inputs) ───────────────────────────
_DEFAULT_MARGIN_PCT = 12.0       # baseline % of withdrawable
_DEFAULT_MARGIN_MAX = 15.0       # cap per position
_DEFAULT_LEVERAGE = 3
_MIN_LEVERAGE = 1
_MAX_LEVERAGE = 5
_DEFAULT_MAX_SLOTS = 4
_DEFAULT_MIN_SCORE = 25.0        # conviction floor
_DEFAULT_RECENT_TTL = 300        # signal-dedup TTL (s)
_NEUTRAL_THRESHOLD = 0.5         # |regime_score| below this = cash

# Static universe — risk assets + defensives, verified live instruments.
_UNIVERSE = [
    "BTC", "ETH", "SOL", "HYPE",          # crypto risk
    "xyz:SP500", "xyz:XYZ100",            # index risk
    "xyz:GOLD",                           # core defensive
    "xyz:JPY",                            # USD/JPY — shorted in risk-off (buys the yen)
    "xyz:BRENTOIL", "xyz:NATGAS",         # energy defensives
]

# Haven assets used for regime computation.
_HAVEN_ASSETS = ["xyz:GOLD", "xyz:JPY"]

def _read(ctx, name, args, label):
    """Guarded MCP read — a transient error degrades, never crashes the tick."""
    try:
        raw = ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[aegis.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw

def _dex_for(asset):
    """XYZ (HIP-3) assets need dex='xyz' for market reads."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""

# ── ACCOUNT + HELD ASSETS ─────────────────────────────────────────────────────

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts).
    """
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = d.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
            })

    # read-sanity guard: margin in use but empty positions -> skip tick
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = d.get(_sec, {}) if isinstance(d, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[aegis.scan] read-sanity guard: margin in use but empty positions — skipping",
              file=sys.stderr)
        return 0.0, []

    return account_value, positions

# ── MARKET READS ──────────────────────────────────────────────────────────────

def _read_funding_regime(ctx):
    """Read market-wide funding regime."""
    d = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime")
    if not isinstance(d, dict):
        return "NEUTRAL"
    label = d.get("regime", d.get("label", ""))
    if not label:
        # try nested
        for k in ("data", "result"):
            v = d.get(k)
            if isinstance(v, dict):
                label = v.get("regime", v.get("label", ""))
                if label:
                    break
    return str(label or "NEUTRAL").upper()

def _read_candles(ctx, asset, intervals=None):
    """Read candles for an asset. Returns dict {interval: [candles]} or {}."""
    if intervals is None:
        intervals = ["4h"]
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": intervals,
        "include_funding": False,
        "include_order_book": False,
        "dex": _dex_for(asset),
    }, f"market_get_asset_data({asset})")
    if not isinstance(md, dict):
        return {}
    candles = md.get("candles", {}) or {}
    if not isinstance(candles, dict):
        return {}
    return candles

def _read_asset_context(ctx, asset):
    """Read 4h candles + OI velocity + funding for an asset.

    Returns (candles_4h, oi_velocity, funding_rate) or (None, None, None).
    """
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["4h"],
        "include_funding": True,
        "include_order_book": False,
        "dex": _dex_for(asset),
    }, f"market_get_asset_data({asset})")
    if not isinstance(md, dict):
        return None, None, None

    candles = md.get("candles", {}) or {}
    c4h = candles.get("4h", []) if isinstance(candles, dict) else []

    # OI velocity (may be null)
    oi_vel = md.get("oi_velocity")
    if not isinstance(oi_vel, dict):
        oi_vel = None

    # Funding rate from asset context
    funding = None
    ctx_data = md.get("asset_context") or {}
    if isinstance(ctx_data, dict):
        funding = ctx_data.get("funding")

    return c4h, oi_vel, funding

# ── SIGNAL EMIT ───────────────────────────────────────────────────────────────

def _emit_signals(candidates, max_slots, held_set, signaled, ttl, now):
    """Filter + emit signals. Returns (emits, updated_signaled)."""
    out = []
    for c in candidates:
        if len(out) >= max_slots:
            break
        asset = c["asset"]
        sig_key = f"{asset}_{c['direction']}"
        if scoring.asset_role(asset) is not None:
            # Check held — skip if we already hold this asset in any direction
            bare = asset.upper()
            if bare in held_set:
                continue
        if sig_key in signaled and (now - signaled[sig_key]) < ttl:
            continue
        out.append({
            "asset": asset,
            "direction": c["direction"],
            "marginPct": c["marginPct"],
            "leverage": c["leverage"],
            "data": {
                "score": c["conviction"],
                "direction": c["direction"],
                "regimeScore": c.get("regime_score"),
                "regimeLabel": c.get("regime_label"),
                "reasons": c.get("reasons", []),
            },
        })
        signaled[sig_key] = now
    return out, signaled

# ── MAIN SCAN ─────────────────────────────────────────────────────────────────

def scan(inputs, ctx):
    now = time.time()

    # ── tunables ──
    base_margin = scoring._f(inputs.get("marginPctBase"), _DEFAULT_MARGIN_PCT)
    margin_max = scoring._f(inputs.get("marginPctMax"), _DEFAULT_MARGIN_MAX)
    leverage = int(scoring._f(inputs.get("leverageDefault"), _DEFAULT_LEVERAGE))
    leverage = max(_MIN_LEVERAGE, min(_MAX_LEVERAGE, leverage))
    max_slots = int(scoring._f(inputs.get("maxSlots"), _DEFAULT_MAX_SLOTS))
    min_score = scoring._f(inputs.get("minScore"), _DEFAULT_MIN_SCORE)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), _DEFAULT_RECENT_TTL)
    neutral_threshold = scoring._f(inputs.get("neutralThreshold"), _NEUTRAL_THRESHOLD)

    # ── account + held ──
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[aegis.scan] cannot read account value; skip tick", file=sys.stderr)
        _persist_state(ctx, None, {}, {"ts": now, "emitted": False, "gate": "no_account"})
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {str(h).upper() for h in held_assets}

    # ── load previous state ──
    prev = (ctx.state.last() or {}) if ctx.state else {}
    signaled = prev.get("signaled", {})
    if not isinstance(signaled, dict):
        signaled = {}
    # prune expired dedup entries
    signaled = {k: v for k, v in signaled.items() if (now - v) < ttl * 3}

    # ── slots count positions on BOTH dexes: a full book emits nothing ──
    free_slots = max(0, max_slots - len(held_assets))
    if free_slots == 0:
        print(f"[aegis.scan] FULL — {len(held_assets)} held, {max_slots} slots | held={held_assets}",
              file=sys.stderr)
        _persist_state(ctx, prev, signaled, {"ts": now, "emitted": False, "gate": "slots_full",
                                              "held": held_assets})
        return []

    # ── read market-wide funding regime ──
    funding_label = _read_funding_regime(ctx)
    funding_component = scoring.funding_regime_score(funding_label)

    # ── read haven trends (gold + JPY) ──
    gold_candles = _read_candles(ctx, "xyz:GOLD", ["4h"]).get("4h", [])
    jpy_candles = _read_candles(ctx, "xyz:JPY", ["4h"]).get("4h", [])
    gold_trend = scoring.trend_structure(gold_candles)
    jpy_trend = scoring.trend_structure(jpy_candles)

    # ── compute regime score ──
    regime_score = scoring.compute_regime_score(funding_label, gold_trend, jpy_trend)

    reasons = [f"funding {funding_label} ({funding_component:+.1f})"]
    if gold_trend[0] != "NEUTRAL":
        reasons.append(f"gold {gold_trend[0].lower()} ({gold_trend[1]:.0%})")
    if jpy_trend[0] != "NEUTRAL":
        reasons.append(f"JPY {jpy_trend[0].lower()} ({jpy_trend[1]:.0%})")
    reasons.append(f"regime score {regime_score:+.2f}")

    # ── neutral regime -> cash ──
    if abs(regime_score) < neutral_threshold:
        print(f"[aegis.scan] NEUTRAL (score {regime_score:+.2f}) — cash | "
              f"held={held_assets}", file=sys.stderr)
        _persist_state(ctx, prev, signaled, {
            "ts": now, "emitted": False, "gate": "neutral_regime",
            "regime_score": regime_score, "regime_label": funding_label,
            "held": held_assets})
        return []

    print(f"[aegis.scan] REGIME {'RISK_OFF' if regime_score < 0 else 'RISK_ON'} "
          f"(score {regime_score:+.2f}) | funding={funding_label} "
          f"gold={gold_trend[0]} JPY={jpy_trend[0]} | held={held_assets}",
          file=sys.stderr)

    # ── score each universe asset ──
    candidates = []
    for asset in _UNIVERSE:
        direction = scoring.regime_to_direction(asset, regime_score)
        if direction is None:
            continue

        role = scoring.asset_role(asset)
        c4h, oi_vel, funding_rate = _read_asset_context(ctx, asset)
        if not c4h:
            continue

        trend_label, trend_strength = scoring.trend_structure(c4h)
        conviction = scoring.conviction_for(
            regime_score, trend_label, direction, oi_vel, funding_rate)

        if conviction < min_score:
            continue

        margin_pct = scoring.margin_pct_for(
            conviction, base_margin, regime_score, margin_max)

        asset_reasons = list(reasons)
        asset_reasons.append(f"4h trend {trend_label.lower()} ({trend_strength:.0%})")
        if oi_vel and isinstance(oi_vel, dict):
            oi_trend = oi_vel.get("oi_trend", "")
            if oi_trend:
                asset_reasons.append(f"OI {str(oi_trend).lower()}")
        if funding_rate is not None:
            fr = scoring._f(funding_rate)
            if fr != 0:
                edge = "collecting" if (
                    (direction == "SHORT" and fr > 0) or
                    (direction == "LONG" and fr < 0)) else "paying"
                asset_reasons.append(f"funding {edge}")

        candidates.append({
            "asset": asset,
            "direction": direction,
            "conviction": round(conviction, 2),
            "marginPct": round(margin_pct, 2),
            "leverage": leverage,
            "regime_score": round(regime_score, 2),
            "regime_label": funding_label,
            "reasons": asset_reasons,
        })

    # ── rank + emit ──
    candidates = scoring.rank_signals(candidates)
    out, signaled = _emit_signals(candidates, free_slots, held_set, signaled, ttl, now)

    # ── log + persist state ──
    if out:
        print(f"[aegis.scan] EMIT {len(out)} signals | "
              f"{'RISK_OFF' if regime_score < 0 else 'RISK_ON'} (score {regime_score:+.2f}) "
              f"| {[ (e['asset'], e['direction'], e['data'].get('score')) for e in out ]}",
              file=sys.stderr)
        result = {"ts": now, "emitted": True, "regime_score": regime_score,
                  "regime_label": funding_label,
                  "emittedAssets": [(e["asset"], e["direction"]) for e in out],
                  "held": held_assets}
    else:
        print(f"[aegis.scan] WAITING — no emit | regime {regime_score:+.2f} "
              f"cands={len(candidates)} held={held_assets} (min_score {min_score:.0f})",
              file=sys.stderr)
        result = {"ts": now, "emitted": False, "gate": "no_signal",
                  "regime_score": regime_score, "regime_label": funding_label,
                  "cands": len(candidates), "held": held_assets}

    _persist_state(ctx, prev, signaled, result)
    return out

def _persist_state(ctx, prev, signaled, result):
    """Persist the full state record for next tick."""
    if ctx.state is None:
        return
    record = {
        "ts": result.get("ts", time.time()),
        "signaled": signaled,
        "result": result,
    }
    try:
        ctx.state.append(record)
    except Exception as exc:  # noqa: BLE001
        print(f"[aegis.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
