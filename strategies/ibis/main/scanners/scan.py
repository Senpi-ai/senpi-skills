"""IBIS — supervised scanner: trend/range regime switch with regime-specific entry logic.

Per tick:
  - reads account + held positions (dual-DEX equity via max(), never sum; corrupt-read guard),
  - derives the universe from LIVE `market_list_instruments` (notional-volume floor + top-N),
    routing by name prefix because dex="" returns BOTH sub-DEXes,
  - snapshots per-coin open interest in USD into ctx.state so the NEXT tick can measure OI
    VELOCITY (the trend-entry confirmation the user asked for),
  - fetches 4h candles per candidate and scores via pure `scoring.build_thesis`, which
    classifies the regime and then applies that regime's entry rule,
  - emits every candidate at/above `minScore`, best-first, up to the open slots.

Read-only + single-pass. Emits a `marginPct` INTENT (PERCENT in (0,100]) + `leverage`.
Candle values are strings keyed o/h/l/c/v — scoring._close/_f coerce.

OI velocity needs a PRIOR snapshot, so the first tick after a (re)start takes no TREND entry
by design — `oi_velocity_pct` returns None and `trend_thesis` refuses to guess. Range entries
are unaffected. This is the correct trade-off: a missed entry costs nothing, a fabricated
confirmation costs money.
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
        print(f"[ibis.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        print("[ibis.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _instruments(ctx, dex):
    """Raw instrument rows for one sub-DEX, or None on a read failure."""
    try:
        raw = ctx.senpi_mcp.call_tool("market_list_instruments", {"dex": dex})
    except Exception as exc:  # noqa: BLE001
        print(f"[ibis.scan] market_list_instruments(dex={dex!r}) failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(d, dict):
        d = d.get("instruments", d.get("universe", d.get("markets", [])))
    return d if isinstance(d, list) else None


def _derive_universe(ctx, inputs):
    """[{name, oi_usd, funding_apr}] over the notional-volume floor, top-N by volume.

    `market_list_instruments` with dex="" returns BOTH sub-DEXes and carries NO dex field — the
    `xyz:` name prefix is the only discriminator — so names are routed by prefix and deduped on
    the bare ticker across both pools.
    """
    vol_floor = float(inputs.get("universeVolFloorUsd", 25_000_000))
    max_names = int(inputs.get("maxUniverseNames", 18))
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
        is_xyz = name.lower().startswith("xyz:")
        if is_xyz and not include_xyz:
            continue
        bare = name.split(":", 1)[-1].upper()
        if bare in seen or bare in exclude:
            continue
        seen.add(bare)
        c = r.get("context", {}) if isinstance(r.get("context"), dict) else {}
        vol = scoring._f(c.get("dayNtlVlm"))
        if vol < vol_floor:
            continue
        out.append({"name": name, "vol": vol, "oi_usd": scoring.oi_usd(c),
                    "funding_apr": scoring.annualized_funding_pct(c.get("funding"))})
    out.sort(key=lambda x: x["vol"], reverse=True)
    return out[:max_names]


def _candles_4h(ctx, coin):
    """4h candle list for `coin`, or None. Read-guarded."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin, "candle_intervals": ["4h"],
            "include_funding": False, "include_order_book": False, "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[ibis.scan] market_get_asset_data({coin}) failed: {exc!r}", file=sys.stderr)
        return None
    if not md or (isinstance(md, dict) and md.get("success") is False):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    c = d.get("candles", {}) if isinstance(d, dict) else {}
    return c.get("4h", []) if isinstance(c, dict) else None


def _load_oi(ctx):
    """{BARE_TICKER: oi_usd} from the previous tick — the OI-velocity baseline."""
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    prev = (ctx.state.last() or {}).get("oi", {})
    return dict(prev) if isinstance(prev, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    min_score = float(inputs.get("minScore", 6))
    base_margin_pct = float(inputs.get("marginPctBase", 10))   # PERCENT in (0,100]
    max_slots = int(inputs.get("maxSlots", 3))
    lev_trend = int(inputs.get("leverageTrend", 4))
    lev_range = int(inputs.get("leverageRange", 3))
    lev_min = int(inputs.get("leverageMin", 2))
    lev_max = int(inputs.get("leverageMax", 5))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[ibis.scan] WAITING — no account value / corrupt read", file=sys.stderr)
        return []
    held = {p["coin"].split(":", 1)[-1].upper() for p in positions if p.get("coin")}
    open_slots = max_slots - len(held)

    universe = _derive_universe(ctx, inputs)
    if universe is None:
        print("[ibis.scan] instruments unreadable — skipping tick (keeping prior OI baseline)",
              file=sys.stderr)
        return []

    # OI snapshot: written EVERY tick (even when slots are full) so the velocity baseline
    # never goes stale just because the book happened to be busy.
    prev_oi = _load_oi(ctx)
    oi_now = {u["name"].split(":", 1)[-1].upper(): u["oi_usd"] for u in universe if u["oi_usd"] > 0}

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"oi": oi_now, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[ibis.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if open_slots <= 0:
        print(f"[ibis.scan] slots full ({len(held)}/{max_slots}) — DSL manages exits", file=sys.stderr)
        _persist({"ts": now, "scanned": 0, "emitted": 0, "note": "slots full"})
        return []

    candidates, scanned, regimes = [], 0, {"TREND": 0, "RANGE": 0, "UNCLEAR": 0}
    for u in universe:
        coin = u["name"]
        bare = coin.split(":", 1)[-1].upper()
        if bare in held:
            continue
        scanned += 1
        candles = _candles_4h(ctx, coin)
        if not candles:
            continue
        regime, _er, _s, _st = scoring.classify_regime(candles, inputs)
        regimes[regime] = regimes.get(regime, 0) + 1
        oi_vel = scoring.oi_velocity_pct(u["oi_usd"], prev_oi.get(bare))
        th = scoring.build_thesis(coin, candles, oi_vel, u["funding_apr"], inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    out = []
    for th in candidates[:open_slots]:
        lev = lev_trend if th["regime"] == "TREND" else lev_range
        leverage = max(lev_min, min(lev, lev_max))
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,      # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,
            "data": {
                "score": th["score"], "leverage": leverage, "direction": th["direction"],
                "regime": th["regime"], "er": th["er"], "reasons": th["reasons"][:8],
                "structure": th["structure"], "oiVelocityPct": th["oi_velocity_pct"],
                "pullbackPct": th["pullback_pct"], "rangePos": th["range_pos"],
                "fundingApr": th["funding_apr"], "heldAssets": sorted(held),
            },
        })

    baseline = "yes" if prev_oi else "NO (first tick — trend entries wait for it)"
    print(f"[ibis.scan] {'EMIT' if out else 'WAITING'} universe={len(universe)} scanned={scanned} "
          f"regimes={regimes} emitted={len(out)} oi_baseline={baseline} held={sorted(held)}",
          file=sys.stderr)
    _persist({"ts": now, "scanned": scanned, "emitted": len(out), "regimes": regimes,
              "universe": len(universe)})
    return out
