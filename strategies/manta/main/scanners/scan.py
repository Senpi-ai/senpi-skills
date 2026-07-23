"""MANTA — supervised scanner: top-down multi-timeframe structure on FX pairs.

Per tick (the tick IS the 15m execution frame):
  - reads account + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - for each configured FX pair fetches Daily / 4h / 1h / 15m candles in one call,
  - runs the pure `scoring.build_thesis` cascade: bias alignment -> 4h AOI -> 15m trigger,
  - emits at most one signal per pair per trigger (dedup keyed on the trigger level in ctx.state)
    so the same break does not re-fire every 15 minutes while price sits above it.

Read-only + single-pass. Emits a `marginPct` INTENT (PERCENT in (0,100]) + `leverage`.
Candle values are strings keyed o/h/l/c/v — scoring._close/_f coerce.

FX on Hyperliquid lives on the XYZ sub-DEX: pairs carry the `xyz:` prefix, the asset read
passes dex="xyz", and positions come back coined `xyz:EUR` — all handled below. FX trades
24/7 on Hyperliquid (no market-hours gate needed).
"""

import sys
import time

import scoring

_DEFAULT_PAIRS = ["xyz:EUR", "xyz:GBP", "xyz:JPY"]


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position dicts]) — dual-DEX equity via max() (never sum: two views of ONE
    cross-margined wallet), positions enumerated across BOTH sub-DEX sections. Read-guarded.

    FX positions live in the `xyz` section coined `xyz:EUR`; the main section is enumerated too
    so a crypto position from another strategy on the same wallet is still seen as held."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the tick
        print(f"[manta.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []
    positions, account_value, used = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        used = max(used, scoring._f(ms.get("totalMarginUsed", 0)), abs(scoring._f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            if scoring._f(pos.get("szi", 0)) == 0:
                continue
            positions.append({"coin": pos.get("coin", ""), "margin": scoring._f(pos.get("marginUsed", 0))})
    if used > 1.0 and not positions:
        print("[manta.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _candles(ctx, coin):
    """{'1d':[], '4h':[], '1h':[], '15m':[]} for `coin` or None. Read-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": ["1d", "4h", "1h", "15m"],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[manta.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not md or (isinstance(md, dict) and md.get("success") is False):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return d.get("candles", {}) if isinstance(d, dict) else None


def _load_fired(ctx):
    """{'xyz:EUR': trigger_level} — last trigger we acted on per pair (level-based dedup)."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    f = (ctx.state.last() or {}).get("fired", {})
    return dict(f) if isinstance(f, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    pairs = inputs.get("pairs", _DEFAULT_PAIRS)
    min_score = float(inputs.get("minScore", 9))
    base_margin_pct = float(inputs.get("marginPctBase", 12))   # PERCENT in (0,100]
    max_slots = int(inputs.get("maxSlots", 2))
    lev_default = int(inputs.get("leverageDefault", 5))
    lev_min = int(inputs.get("leverageMin", 3))
    lev_max = int(inputs.get("leverageMax", 8))
    dedup_tol = float(inputs.get("triggerDedupTolerancePct", 0.15))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[manta.scan] WAITING — no account value / corrupt read", file=sys.stderr)
        return []
    held = {p["coin"].split(":", 1)[-1].upper() for p in positions if p.get("coin")}
    open_slots = max_slots - len(held)

    fired = _load_fired(ctx)

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"fired": fired, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[manta.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if open_slots <= 0:
        print(f"[manta.scan] slots full ({len(held)}/{max_slots}) — DSL manages exits", file=sys.stderr)
        _persist({"ts": now, "scanned": 0, "emitted": 0, "note": "slots full"})
        return []

    candidates, scanned = [], 0
    for coin in pairs:
        if not coin:
            continue
        bare = coin.split(":", 1)[-1].upper()
        if bare in held:
            continue
        scanned += 1
        cd = _candles(ctx, coin)
        if not cd:
            continue
        th = scoring.build_thesis(coin, cd.get("1d", []), cd.get("4h", []),
                                  cd.get("1h", []), cd.get("15m", []), inputs)
        if not th or th["score"] < min_score:
            continue
        # level-based dedup: the same trigger level (within tolerance) fires once, not every tick
        prev = fired.get(coin)
        if prev and scoring._f(prev) > 0 and \
                abs(th["trigger_level"] - prev) / prev * 100.0 < dedup_tol:
            continue
        candidates.append(th)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    leverage = max(lev_min, min(lev_default, lev_max))
    out = []
    for th in candidates[:open_slots]:
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)
        fired[th["coin"]] = th["trigger_level"]
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,      # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "bias": th["bias"], "biases": th["biases"], "reasons": th["reasons"][:8],
                "aoiLow": round(th["aoi_low"], 8), "aoiHigh": round(th["aoi_high"], 8),
                "triggerLevel": round(th["trigger_level"], 8),
                "displacementPct": th["displacement_pct"], "heldAssets": sorted(held),
            },
        })

    print(f"[manta.scan] {'EMIT' if out else 'WAITING'} pairs={len(pairs)} scanned={scanned} "
          f"emitted={len(out)} min_score={min_score:.0f} held={sorted(held)}", file=sys.stderr)
    _persist({"ts": now, "scanned": scanned, "emitted": len(out)})
    return out
