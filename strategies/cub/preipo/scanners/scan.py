"""CUB · PREIPO satellite — supervised scanner (Runtime 3.0 port of cub-producer.py CUB_LEG=preipo).

The ~10% pre-IPO ramp sleeve (Lemur IPOP-discovery method). Each tick it auto-DISCOVERS pre-IPO
perpetuals (IPOPs) from the live Hyperliquid XYZ board by their structural funding signature
(|funding| <= ipopFundingMaxAbs AND venue max_leverage <= ipopMaxLeverageCap — the throttled
pre-listing state), then LONGS the ones with a confirming absolute 4h/1h uptrend (rides the
pre-listing ramp; blow-off guard loosened since pre-IPO names run hot). There is NO static universe
— it is discovered every tick, so a new IPOP (the next SpaceX/Cerebras) auto-joins the moment it
lists. LONG-only. Episodic by design — most ticks may find 0-2 IPOPs. Read-only, single-pass.

The IPOP liquidity floor is BUDGET-RELATIVE (24h vol >= ipopLiqVolMultiple × standard position
notional) — the IPOP universe is too small for a median gate, and a $ floor is never hardcoded.

READ-GUARD: market_list_instruments + every per-asset market_get_asset_data is wrapped so one bad
read degrades (empty universe / skip name), never rolls back the whole tick.

FIDELITY NOTES vs cub-producer.py v1.0.0 (CUB_LEG=preipo):
  - v2 sized margin_usd = account_value * marginPct(FRACTION 0.15) * sizingWeight (USD). This port
    emits a top-level `marginPct` PERCENT × per-name conviction weight (preipo sizingWeights default
    is just {_default:1.0}, so weight is 1.0 unless overridden). The runtime sizes
    (marginPct/100)*withdrawable. The budget-relative IPOP liquidity floor uses the FRACTION form
    (base_margin_pct/100) so it reproduces v2's exact threshold:
      min_day_vol = ipopLiqVolMultiple × (account_value × margin_frac × max_lev).
    `<=1.0 means a pasted fraction -> x100` guard converts a fraction supplied via inputs.
  - v2 ranked the discovered IPOPs leaders-first (reverse=True for the LONG preipo leg); the
    too-thin guard is len(rs) < 1 (vs < 2 for long/short). Both preserved.
  - v2 funding cap (free margin, 1.1 headroom); ported.
  - v2 recent-signals JSON cache -> ctx.state dedup (180s race-window).
  - v2 SM was a noted bonus-only concern for IPOPs but score_thematic itself does NOT consume SM in
    any leg — nothing dropped.
  - DROPPED (read-only scan cannot mutate): v2 had no order-lifecycle mutations; push_signal /
    record_signal replaced by returning plain dicts + ctx.state per the scan() contract.
"""

import sys
import time

import scoring

# v2 _DEFAULTS["preipo"] / cub-preipo-config.json
_WEIGHTS_DEFAULT = {"_default": 1.0}
_DEFAULT_TTL = 180
_DIRECTION = "LONG"   # preipo is a long-only ramp book


def _read(ctx, name, args):
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad read must not kill the discovery tick
        print(f"[cub.preipo.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _resolve_margin_pct(inputs, default_pct):
    mp = scoring._f(inputs.get("marginPct", default_pct), default_pct)
    if mp <= 1.0:                 # a fraction was pasted (e.g. 0.15) -> percent
        mp *= 100.0
    return mp


def _get_positions(ctx):
    """(account_value, [positions], free_margin). accountValue via max() across main/xyz; v2
    read-sanity guard (margin in use but empty positions -> skip tick)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = _unwrap(ch)
    if not isinstance(data, dict):
        return 0.0, [], 0.0
    positions, account_value, used = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        used = max(used, scoring._f(ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "margin": scoring._f(pos.get("marginUsed", 0)),
            })
    if used > 1.0 and not positions:
        print("[cub.preipo.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


def _get_universe_meta(ctx):
    """(meta_map name -> {max_leverage, ctx{...}}, canonical [names in board order]). Skips delisted.
    Verbatim v2 get_universe_meta() — keeps the raw instrument context for the IPOP signature."""
    resp = _read(ctx, "market_list_instruments", {})
    out, canonical = {}, []
    if not resp:
        return out, canonical
    insts = _unwrap(resp)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
        canonical.append(name)
    return out, canonical


def _ipop_universe(canonical, meta_map, max_funding, lev_cap, min_day_vol):
    """Lemur-method pre-IPO discovery (verbatim v2 ipop_universe). Returns the live xyz IPOP names
    matching the funding+leverage signature AND clearing the budget-relative liquidity floor."""
    out = []
    for name in canonical:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if scoring.is_ipop(name, meta, max_funding=max_funding, lev_cap=lev_cap,
                           min_day_vol=min_day_vol):
            out.append(name)
    return out


def _fetch_candles(ctx, asset):
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    })
    if not resp:
        return [], []
    d = _unwrap(resp)
    if isinstance(d, dict) and d.get("success") is False:
        return [], []
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def scan(inputs, ctx):
    run_start = time.time()
    weights = inputs.get("sizingWeights", _WEIGHTS_DEFAULT)
    min_score = int(inputs.get("minScore", 4))
    base_margin_pct = _resolve_margin_pct(inputs, 15)        # PERCENT of withdrawable (0,100]
    margin_frac = base_margin_pct / 100.0                    # FRACTION form for the budget-relative floor
    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 3))
    rank_pool = int(inputs.get("rankPoolSize", 16))
    max_funding = float(inputs.get("ipopFundingMaxAbs", scoring.DEFAULT_IPOP_FUNDING_MAX))
    lev_cap = int(inputs.get("ipopMaxLeverageCap", scoring.DEFAULT_IPOP_LEV_CAP))
    liq_mult = float(inputs.get("ipopLiqVolMultiple", 30))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    last = (ctx.state.last() or {}) if ctx.state else {}
    recent = {k: v for k, v in (last.get("recent") or {}).items() if (now - v) < ttl}

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent})
        except Exception as exc:  # noqa: BLE001
            print(f"[cub.preipo.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        return []
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        return []

    meta_map, canonical = _get_universe_meta(ctx)
    # budget-relative IPOP liquidity floor (NO hardcoded $); verbatim v2 min_day_vol math.
    min_day_vol = liq_mult * (account_value * margin_frac * float(max_lev))
    universe = _ipop_universe(canonical, meta_map, max_funding, lev_cap, min_day_vol)

    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = scoring.ret_24h(meta) if meta else None
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 1:                                          # v2-quirk: preipo too-thin guard (< 1)
        _persist()
        print("[cub.preipo.scan] WAITING — no live IPOPs ramping", file=sys.stderr)
        return []

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=True)                # leaders first (long ramp leg)
    pool = rs[:rank_pool]

    candidates = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue
        excess = own - mean_rs
        c1, c4 = _fetch_candles(ctx, name)
        thesis = scoring.score_thematic(name, c1, c4, excess, own, _DIRECTION, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist()
        print(f"[cub.preipo.scan] WAITING — no IPOP cleared min score {min_score}; "
              f"universe={universe} held={held}", file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    out = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = scoring.sizing_weight(th["coin"], weights)
        margin_pct = round(base_margin_pct * weight, 4)
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)
        if margin_pct <= 0 or leverage <= 0:
            continue
        margin_usd = (margin_pct / 100.0) * account_value
        if margin_usd * 1.1 > free_margin:                   # 1.1 = fee/slippage headroom (v2)
            continue
        out.append({
            "asset": th["coin"],
            "direction": _DIRECTION,
            "marginPct": margin_pct,
            "leverage": leverage,
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": _DIRECTION,
                "reasons": th["reasons"][:6],
                "trend4h": th.get("trend4h"),
                "excess": round(th.get("excess", 0), 2),
                "own24h": round(th.get("own24h", 0), 2),
                "weight": weight,
                "heldAssets": held,
            },
        })
        recent[th["coin"].upper()] = now
        open_slots -= 1
        free_margin -= margin_usd * 1.1

    _persist()
    print(f"[cub.preipo.scan] universe={len(universe)} pool={len(pool)} candidates={len(candidates)} "
          f"emitted={len(out)} mean_rs={mean_rs:.2f} elapsed={time.time() - run_start:.2f}s",
          file=sys.stderr)
    return out
