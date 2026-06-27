"""THESIS FUND — supervised scanner (Runtime 3.0 port of the v2 Thesis Fund producer).

One wallet expresses a chosen macro VIEW as a long/short basket. Each tick this reads, for
every name in the active preset's basket (inputs.longBasket / inputs.shortBasket), 1h+4h
candles + the 24h return, scores how strongly the market is CONFIRMING the thesis direction
for that name (pure `scoring.score_thesis`), and emits a `marginPct` intent + flat 5x
(venue-clamped) for every name that clears `minScore`. The runtime sizes the dollars, owns
the slots/cooldowns/risk gates, and trails the DSL exit. Read-only + single-pass — no daemon,
no push_signal.

The direction of each name is FIXED by the preset (a long-basket name comes as LONG, a
short-basket name as SHORT); the score only measures confirmation. A name whose 4h structure
OPPOSES its thesis direction is skipped entirely (scoring returns None) — the fund waits for
the view to start working rather than fighting the tape.

Held-asset dedup is defence-in-depth: an on-chain held set (from the clearinghouse) PLUS a
cross-tick recent-signal map in ctx.state, mirroring the v2 producer's two-layer dedup."""

import sys
import time

import scoring

_DEFAULT_TTL = 180          # 3x typical ALO fill window — race-dedup (v2 RECENT_SIGNAL_TTL_SEC)


def _dex_for(asset):
    return "xyz" if isinstance(asset, str) and asset.lower().startswith("xyz:") else ""


def _get_universe_meta(ctx):
    """name -> max_leverage, from market_list_instruments (both DEXes). Read-guarded:
    on failure returns {} and the per-name clamp falls back to the desired leverage."""
    try:
        raw = ctx.senpi_mcp.call_tool("market_list_instruments", {"dex": ""})
    except Exception as exc:  # noqa: BLE001 — universe read must not roll back the tick
        print(f"[thesis.scan] market_list_instruments read failed (venue clamp -> desired): {exc!r}", file=sys.stderr)
        return {}
    if not raw:
        return {}
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    insts = data.get("instruments", data) if isinstance(data, dict) else data
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    out = {}
    for inst in insts or []:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        lev = inst.get("max_leverage", inst.get("maxLeverage"))
        out[name] = lev
        out[name.upper()] = lev
    return out


def _asset_market(ctx, asset):
    """Returns ({candles}, own24h) for `asset`, or (None, 0.0). Read-guarded.

    own24h = (markPx - prevDayPx)/prevDayPx*100, ported verbatim from v2 ret_24h."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(asset),
        })
    except Exception as exc:  # noqa: BLE001 — one bad/illiquid name must not roll back the whole basket tick
        print(f"[thesis.scan] market_get_asset_data({asset}) read failed, skipping: {exc!r}", file=sys.stderr)
        return None, 0.0
    if not md:
        return None, 0.0
    d = md.get("data", md) if isinstance(md, dict) else {}
    candles = d.get("candles", {}) or {}
    ctxb = d.get("asset_context", {}) or {}
    mark = scoring._f(ctxb.get("markPx", 0))
    prev = scoring._f(ctxb.get("prevDayPx", 0))
    own24h = ((mark - prev) / prev * 100.0) if (prev > 0 and mark > 0) else 0.0
    return candles, own24h


def _held_assets(ctx, inputs):
    """On-chain held set across the main + xyz clearinghouse sections (two VIEWS of ONE
    cross-margined wallet). Read-guarded — on failure returns an empty set (the cross-tick
    recent-signal map is the dedup floor underneath). Returns a set of UPPER coin names."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — held-asset read must not roll back the tick
        print(f"[thesis.scan] clearinghouse read failed (held-asset dedup -> recent-map only): {exc!r}", file=sys.stderr)
        return set()
    if not ch:
        return set()
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    held = set()
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring._f(pos.get("szi", 0)) != 0:
                coin = pos.get("coin", "")
                if coin:
                    held.add(coin.upper())
    return held


def scan(inputs, ctx):
    long_basket = inputs.get("longBasket", []) or []
    short_basket = inputs.get("shortBasket", []) or []
    min_score = float(inputs.get("minScore", 4))
    margin_pct = float(inputs.get("marginPct", 12))     # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 5))         # strict clamp, then venue max
    max_slots = int(inputs.get("maxSlots", 6))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    thesis_key = inputs.get("thesis", "")
    now = time.time()

    # (asset, target_dir) basket — direction is FIXED by the preset leg.
    basket = [(a, "LONG") for a in long_basket if isinstance(a, str)] + \
             [(a, "SHORT") for a in short_basket if isinstance(a, str)]

    # cross-tick recent-signal dedup (defence-in-depth alongside the on-chain held set
    # and the runtime's per-asset cooldown gate).
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    held = _held_assets(ctx, inputs)
    open_slots = max(0, max_slots - len(held))
    if open_slots <= 0:
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": recent, "result": {"ts": now, "thesis": thesis_key,
                                                                "emitted": 0, "note": "slots_full",
                                                                "held": sorted(held)}})
            except Exception as exc:  # noqa: BLE001
                print(f"[thesis.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        print(f"[thesis.scan] HOLD: slots full ({len(held)}/{max_slots}) held={sorted(held)}", file=sys.stderr)
        return []

    meta = _get_universe_meta(ctx)

    candidates = []
    skipped_recent = []
    for asset, target_dir in basket:
        au = asset.upper()
        if au in held:
            continue
        last = recent.get(au)
        if last is not None and (now - last) < ttl:
            skipped_recent.append(asset)
            continue
        # only score names live on the instrument board (validate against the live universe)
        if meta and (meta.get(asset) is None and meta.get(au) is None):
            print(f"[thesis.scan] {asset} not on live instrument board, skipping", file=sys.stderr)
            continue
        candles, own24h = _asset_market(ctx, asset)
        if candles is None:
            continue
        th = scoring.score_thesis(target_dir, candles.get("1h", []), candles.get("4h", []), own24h, inputs)
        if th and th["score"] >= min_score:
            th["coin"] = asset
            candidates.append(th)

    # v2 ranks by score desc and caps emissions to open slots.
    candidates.sort(key=lambda x: x["score"], reverse=True)
    to_emit = candidates[:open_slots]

    out = []
    emitted_log = []
    for th in to_emit:
        venue_max = meta.get(th["coin"]) if meta else None
        if venue_max is None and meta:
            venue_max = meta.get(th["coin"].upper())
        leverage = scoring.clamp_leverage(max_lev, venue_max)
        if leverage <= 0:
            continue
        recent[th["coin"].upper()] = now
        emitted_log.append({"coin": th["coin"], "dir": th["direction"], "score": th["score"], "lev": leverage})
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,           # SIZING INTENT — the runtime sizes the dollars
            "leverage": leverage,              # flat 5x (venue-clamped); runtime applies it
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": th["direction"],
                "thesis": thesis_key,
                "trend4h": th["trend4h"],
                "own24h": th["own24h"],
                "rsi": th["rsi"],
                "reasons": th["reasons"],
            },
        })

    # per-tick result record → scan-results history (bounded by state_history_max_count;
    # read back with ctx.state.recent(n)).
    if ctx.state is not None:
        try:
            ctx.state.append({
                "recent": recent,
                "result": {"ts": now, "thesis": thesis_key, "basket_size": len(basket),
                           "candidates": len(candidates), "open_slots": open_slots,
                           "emitted": len(out), "emitted_detail": emitted_log,
                           "skipped_recent": skipped_recent, "held": sorted(held)},
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[thesis.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    if out:
        print(f"[thesis.scan] EMIT {len(out)}/{open_slots} ({thesis_key}): {emitted_log}", file=sys.stderr)
    else:
        print(f"[thesis.scan] HOLD ({thesis_key}): no basket name confirmed (min score {min_score:.0f}), "
              f"basket={len(basket)} candidates={len(candidates)}", file=sys.stderr)
    return out
