"""BLOODHOUND — supervised scanner: chart-pattern recognition across a dynamic universe.

Per tick:
  - reads account + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - derives the universe from LIVE `market_list_instruments` (notional-volume floor + top-N),
    routing by name prefix because dex="" returns BOTH sub-DEXes,
  - fetches 1h + 4h candles per candidate and runs pure `scoring.build_thesis` — double
    bottom / double top / higher-high / lower-low, scored on confirmation, volume and depth,
  - DEDUPES: one signal per coin per pattern per TTL, so a pattern that stays on the chart for
    a day does not re-fire every tick. (Explicitly requested; also what stops a pattern scanner
    from turning into a fee pump.)

Read-only + single-pass. Emits a `marginPct` INTENT (PERCENT in (0,100]) + `leverage`.
Candle values are strings keyed o/h/l/c/v — scoring._close/_f coerce.

Direction comes from the pattern, never from a preference — a double top shorts, a double
bottom longs, and the scanner has no view of its own.
"""

import sys
import time

import scoring

_DEFAULT_EXCLUDE = ["USDC", "USDT"]


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position dicts]) — dual-DEX equity via max() (never sum: two views of ONE
    cross-margined wallet), positions enumerated across BOTH sub-DEX sections. Read-guarded."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the tick
        print(f"[bloodhound.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        print("[bloodhound.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _instruments(ctx, dex):
    try:
        raw = ctx.senpi_mcp.call_tool("market_list_instruments", {"dex": dex})
    except Exception as exc:  # noqa: BLE001
        print(f"[bloodhound.scan] market_list_instruments(dex={dex!r}) failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(d, dict):
        d = d.get("instruments", d.get("universe", d.get("markets", [])))
    return d if isinstance(d, list) else None


def _derive_universe(ctx, inputs):
    """[name] over the notional-volume floor, top-N by volume.

    `market_list_instruments` with dex="" returns BOTH sub-DEXes and carries NO dex field — the
    `xyz:` name prefix is the only discriminator — so names are routed by prefix and deduped on
    the bare ticker across both pools.
    """
    vol_floor = float(inputs.get("universeVolFloorUsd", 20_000_000))
    max_names = int(inputs.get("maxUniverseNames", 25))
    include_xyz = bool(inputs.get("includeXyz", False))
    exclude = {str(x).upper() for x in (inputs.get("excludeAssets") or _DEFAULT_EXCLUDE)}

    rows = _instruments(ctx, "")
    if rows is None:
        return None
    seen, out = set(), []
    for r in rows:
        if not isinstance(r, dict) or r.get("is_delisted"):
            continue
        name = str(r.get("name") or r.get("coin") or "").strip()
        if not name:
            continue
        if name.lower().startswith("xyz:") and not include_xyz:
            continue
        bare = name.split(":", 1)[-1].upper()
        if bare in seen or bare in exclude:
            continue
        seen.add(bare)
        c = r.get("context", {}) if isinstance(r.get("context"), dict) else {}
        vol = scoring._f(c.get("dayNtlVlm"))
        if vol < vol_floor:
            continue
        out.append((name, vol))
    out.sort(key=lambda t: t[1], reverse=True)
    return [n for n, _ in out[:max_names]]


def _candles(ctx, coin, primary):
    """{primary:[...], '4h':[...]} for `coin` or None. Read-guarded."""
    intervals = [primary] if primary == "4h" else [primary, "4h"]
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": intervals,
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[bloodhound.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not md or (isinstance(md, dict) and md.get("success") is False):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return d.get("candles", {}) if isinstance(d, dict) else None


def _load_signaled(ctx):
    """{'COIN:pattern': ts} — the dedup ledger."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    sig = (ctx.state.last() or {}).get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    primary = str(inputs.get("primaryInterval", "1h"))
    min_score = float(inputs.get("minScore", 6))
    base_margin_pct = float(inputs.get("marginPctBase", 10))   # PERCENT in (0,100]
    max_slots = int(inputs.get("maxSlots", 3))
    lev_default = int(inputs.get("leverageDefault", 3))
    lev_min = int(inputs.get("leverageMin", 2))
    lev_max = int(inputs.get("leverageMax", 5))
    ttl = float(inputs.get("signalTtlSeconds", 21600))         # 6h: a pattern persists on the chart

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[bloodhound.scan] WAITING — no account value / corrupt read", file=sys.stderr)
        return []
    held = {p["coin"].split(":", 1)[-1].upper() for p in positions if p.get("coin")}
    open_slots = max_slots - len(held)

    signaled = {k: v for k, v in _load_signaled(ctx).items() if (now - v) < ttl * 4}

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[bloodhound.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if open_slots <= 0:
        print(f"[bloodhound.scan] slots full ({len(held)}/{max_slots}) — DSL manages exits",
              file=sys.stderr)
        _persist({"ts": now, "scanned": 0, "emitted": 0, "note": "slots full"})
        return []

    universe = _derive_universe(ctx, inputs)
    if universe is None:
        print("[bloodhound.scan] instruments unreadable — skipping tick", file=sys.stderr)
        return []

    candidates, scanned, patterns = [], 0, {}
    for coin in universe:
        bare = coin.split(":", 1)[-1].upper()
        if bare in held:
            continue
        scanned += 1
        candles = _candles(ctx, coin, primary)
        if not candles:
            continue
        th = scoring.build_thesis(coin, candles.get(primary, []), candles.get("4h", []), inputs)
        if not th or th["score"] < min_score:
            continue
        patterns[th["pattern"]] = patterns.get(th["pattern"], 0) + 1
        if (now - signaled.get(f"{bare}:{th['pattern']}", 0)) < ttl:
            continue                                  # same pattern, same coin, still inside TTL
        candidates.append(th)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    leverage = max(lev_min, min(lev_default, lev_max))
    out = []
    for th in candidates[:open_slots]:
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)
        signaled[f"{th['coin'].split(':', 1)[-1].upper()}:{th['pattern']}"] = now
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,      # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "pattern": th["pattern"], "confirmed": th["confirmed"],
                "depthPct": th["depth_pct"], "neckline": th["neckline"],
                "volRatio": th["vol_ratio"], "trend4h": th["trend_4h"],
                "reasons": th["reasons"][:8], "heldAssets": sorted(held),
            },
        })

    print(f"[bloodhound.scan] {'EMIT' if out else 'WAITING'} universe={len(universe)} "
          f"scanned={scanned} patterns={patterns or '{}'} emitted={len(out)} "
          f"min_score={min_score:.0f} held={sorted(held)}", file=sys.stderr)
    _persist({"ts": now, "scanned": scanned, "emitted": len(out), "patterns": patterns,
              "universe": len(universe)})
    return out
