"""OX — supervised scanner (Runtime 3.0 port of the v2 Ox risk-parity / all-weather fund).

Shared VERBATIM by both instances (core + ballast). The book is parametrized entirely via
`inputs` (sleeve basket, portfolio budget, risk-off scaler) — the `core` instance passes the
all-weather basket; the `ballast` instance passes the defensive basket + the risk-off probes.

Faithful port of ox-producer.py main():
  PASS 1 — realized vol over the FULL basket (held + un-held), intersected with the LIVE
           instrument board (delisted sleeves silently dropped — e.g. xyz:DXY is delisted).
  WEIGHTS — inverse-vol over the whole basket (so a re-entering sleeve gets its correct
            fractional weight, never the full budget).
  PASS 2 — emit each un-held sleeve LONG sized at marginUsd = budget * weight, capped at
           maxWeightPct * equity; knife guard (decline to ADD in a hard 4h downtrend);
           free-margin guard (never emit a sleeve the wallet can't fund -> no insufficient
           -funds spam); largest-weight (lowest-vol) sleeves first. That marginUsd is emitted
           as a top-level `marginPct` (= marginUsd/account_value*100) because Runtime 3.0 sizes
           off marginPct and silently drops a top-level marginUsd.
  BALLAST — a light cross-asset risk-off lean (equities soft + gold/dollar bid) SCALES the
            defensive budget up x riskOffMultiplier (capped at 0.6 gross).

Read-only + single-pass — emits the per-sleeve inverse-vol risk-parity weight as a top-level
`marginPct` (PERCENT of equity) + per-signal clamped `leverage`; the runtime sizes the dollars, owns slots/cooldowns/
risk gates, and trails the DSL exit. No daemon, no push_signal. EVERY ctx.senpi_mcp.call_tool
is read-guarded: a bad read degrades (skip that sleeve / neutral lean), never crashes the tick.
"""

import sys
import time

import scoring

_DEFAULT_TTL = 21600          # 6h — mirror the v2 per-asset cooldown (per_asset_cooldown_minutes 360)

# v2-quirk: default risk-off probes from ox-producer.py _RISKOFF_PROBES (ballast budget scaler).
_DEFAULT_RISKOFF_PROBES = [
    {"asset": "xyz:XYZ100", "fallback": "xyz:SP500", "risk_off_when": "BEARISH", "label": "equities"},
    {"asset": "xyz:GOLD", "fallback": None, "risk_off_when": "BULLISH", "label": "gold"},
]


# ── MCP fetchers (route every producer call through ctx.senpi_mcp, read-guarded) ──

def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_universe_meta(ctx):
    """{name: {"max_leverage": int|None, "ctx": {...}}} for every LIVE (non-delisted)
    instrument on both dexes. Ported from v2 get_universe_meta(): skips is_delisted so a
    delisted sleeve (e.g. xyz:DXY) is silently dropped from the basket — degrade, never crash."""
    try:
        data = ctx.senpi_mcp.call_tool("market_list_instruments", {})
    except Exception as exc:  # noqa: BLE001 — universe read failed; basket empties this tick, no crash
        print(f"[ox.scan] market_list_instruments read failed (basket empty this tick): {exc!r}", file=sys.stderr)
        return {}
    out = {}
    if not data:
        return out
    insts = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):          # v2-quirk: delisted filter -> drops xyz:DXY etc.
            continue
        name = inst.get("name") or inst.get("context", {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
    return out


def _fetch_candles(ctx, asset, intervals):
    """{"candles": {iv: [...]}, "ctx": {...}} or None. Ported from v2 fetch_candles()."""
    try:
        data = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "dex": _dex_for(asset),
            "include_funding": False,
            "include_order_book": False,
        })
    except Exception as exc:  # noqa: BLE001 — one bad/illiquid sleeve must not roll back the whole tick
        print(f"[ox.scan] market_get_asset_data({asset}) read failed, skipping sleeve: {exc!r}", file=sys.stderr)
        return None
    if not data or (isinstance(data, dict) and not data.get("success", True)):
        return None
    d = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(d, dict):
        return None
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    v2-quirk (dual-DEX equity collapse): account_value is taken via max() across the
    main/xyz sections — they are TWO VIEWS of ONE cross-margined wallet, NEVER summed.
    assetPositions ARE per-sub-DEX, so those are enumerated across both sections.
    Ported from v2 ox_config.get_positions()."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — no account read -> emit nothing (account_value 0), no crash
        print(f"[ox.scan] strategy_get_clearinghouse_state read failed: {exc!r}", file=sys.stderr)
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
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))   # max(), not sum()
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": float(pos.get("marginUsed", pos.get("margin", 0)) or 0)})
    return account_value, positions


# ── Risk-off lean (ballast budget SCALER only — NOT a hard gate). Ported from v2 risk_off_lean(). ──

def _risk_off_lean(ctx, inputs):
    """v2-quirk: verbatim port of ox-producer.py risk_off_lean() — light cross-asset read used
    to SCALE the ballast budget. Each probe votes risk-off on its 4h trend; risk_off = votes
    >= riskOffThreshold. Read-guarded (a failed probe just doesn't vote)."""
    threshold = int(inputs.get("riskOffThreshold", 1))
    probes = inputs.get("riskOffProbes", _DEFAULT_RISKOFF_PROBES)
    votes, detail = 0, {}
    for p in probes:
        asset = p.get("asset")
        md = _fetch_candles(ctx, asset, ["4h"])
        if (not md or len(md["candles"].get("4h", [])) < 6) and p.get("fallback"):
            asset = p["fallback"]
            md = _fetch_candles(ctx, asset, ["4h"])
        c4 = md["candles"].get("4h", []) if md else []
        if len(c4) < 6:
            detail[p["label"]] = "no_data"
            continue
        trend4, _ = scoring.trend_structure(c4)
        if trend4 == p.get("risk_off_when", "BEARISH"):
            votes += 1
            detail[p["label"]] = "risk_off"
        else:
            detail[p["label"]] = "calm"
    return {"risk_off": votes >= threshold, "votes": votes, "threshold": threshold, "detail": detail}


# ── Basket — the sleeve list for this book, intersected with the live board ──

def _build_basket(inputs, meta_map):
    """v2-quirk: verbatim from ox-producer.py build_basket() — keep only configured sleeves that
    are present (and thus live + non-delisted) on the instrument board."""
    sleeves = inputs.get("sleeves", [])
    out = []
    for name in sleeves:
        if isinstance(name, str) and (meta_map.get(name) or meta_map.get(name.upper())):
            out.append(name)
    return out


# ── Entry point ──

def scan(inputs, ctx):
    run_start = time.time()
    now = run_start
    book = (inputs.get("book", "core") or "core").lower()
    budget_pct = float(inputs.get("portfolioBudgetPct", 0.60))
    max_weight = float(inputs.get("maxWeightPct", 0.22))
    max_lev = int(inputs.get("maxLeverage", 3))
    max_slots = int(inputs.get("maxSlots", 10))
    vol_bars = int(inputs.get("volBars", 30))
    min_score = int(inputs.get("minScore", 5))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    account_value, positions = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        print("[ox.scan] no account value -> emit nothing", file=sys.stderr)
        return []

    # ── BALLAST: scale the defensive budget up on a confirmed risk-off lean ──
    lean = None
    if book == "ballast":
        lean = _risk_off_lean(ctx, inputs)
        if lean["risk_off"]:
            # v2-quirk: budget *= riskOffMultiplier, capped at 0.6 gross.
            budget_pct = min(budget_pct * float(inputs.get("riskOffMultiplier", 2.0)), 0.6)
    budget_usd = account_value * budget_pct

    # v2-quirk: min notional scales with equity, floored at the HL venue minimum order value.
    min_notional = max(account_value * min_notional_pct, venue_min_notional)

    meta_map = _get_universe_meta(ctx)
    basket = _build_basket(inputs, meta_map)

    # dedup map (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    # ── PASS 1: realized vol over the FULL basket (held + un-held). Weights MUST be computed
    #    over the whole basket — otherwise a single re-entry gets weight ~ 1.0 and is sized to
    #    the entire budget. ──
    vols, metas, trends = {}, {}, {}
    for name in basket:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        md = _fetch_candles(ctx, name, ["1h", "4h"])
        if not md:
            continue
        c1 = md["candles"].get("1h", [])
        c4 = md["candles"].get("4h", [])
        closes = [scoring._close(c) for c in c1]
        if len(closes) < vol_bars + 1 or len(c4) < 6:
            continue
        v = scoring.realized_vol(closes, vol_bars)
        if v <= 0:
            continue
        vols[name] = v
        metas[name] = meta
        trends[name] = scoring.trend_structure(c4)

    if not vols:
        print(f"[ox.scan] {book} WAITING — no sleeve returned usable vol data "
              f"(scanned {len(basket)})", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": recent,
                                  "result": {"ts": now, "book": book, "emitted": 0,
                                             "scanned": len(basket), "sized": 0,
                                             "note": "no_vol_data"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[ox.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    weights = scoring.inverse_vol_weights(vols)
    open_slots = max_slots - len(held_assets)

    if open_slots <= 0:
        print(f"[ox.scan] {book} basket full ({len(held_assets)}/{max_slots})", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": recent,
                                  "result": {"ts": now, "book": book, "emitted": 0,
                                             "note": "basket_full", "held": held_assets}})
            except Exception as exc:  # noqa: BLE001
                print(f"[ox.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    # ── PASS 2: emit the un-held sleeves at their full-basket inverse-vol weight ──
    # v2-quirk: track free margin so we never emit a sleeve the wallet can't FUND
    # (free margin = equity minus committed margin; 1.1 = fee/slippage headroom).
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    out, emitted, recently_skipped = [], [], []
    pushed = 0

    # enter the largest-weight (lowest-vol) sleeves first
    for name in sorted(vols, key=lambda n: weights.get(n, 0), reverse=True):
        if pushed >= open_slots:
            break
        au = name.upper()
        if au in held_set:
            continue
        last = recent.get(au)
        if last is not None and (now - last) < ttl:        # signal-dedup
            recently_skipped.append(name)
            continue
        trend4, s4 = trends[name]
        # v2-quirk knife guard: don't ADD a sleeve in a hard 4h downtrend (>=0.8 strength).
        # It stays in the basket and is added once it stabilizes; the DSL holds existing ones.
        if trend4 == "BEARISH" and s4 >= 0.8:
            continue
        w = weights.get(name, 0)
        margin_usd = round(min(budget_usd * w, account_value * max_weight), 2)
        # Runtime 3.0 sizes off a top-level marginPct (PERCENT of equity in (0,100]), NOT a
        # top-level marginUsd (silently dropped). budget_usd = account_value*budget_pct and the
        # cap is account_value*max_weight, so base=account_value and marginPct-of-equity =
        # margin_usd/account_value*100 reproduces the inverse-vol weight exactly.
        # account_value > 0 here (guarded above).
        margin_pct_emit = round(min(max(margin_usd / account_value * 100.0, 0.01), 100.0), 4)
        leverage = scoring.clamp_leverage(max_lev, metas[name].get("max_leverage"))
        if margin_usd <= 0 or leverage <= 0 or margin_usd * leverage < min_notional:
            continue
        if margin_usd * 1.1 > free_margin:
            continue   # can't fund this sleeve — try a smaller-weight one, don't spam
        score, ok = scoring.score_sleeve(trend4, s4, min_score)
        if not ok:
            continue
        reasons = [f"riskparity_w_{w:.0%}", f"vol_{vols[name]:.4f}", f"4h_{trend4.lower()}"]
        data_block = {
            "score": score,
            "leverage": leverage,
            "direction": "LONG",
            "reasons": reasons,
            "weightPct": round(w * 100, 2),
            "vol": round(vols[name], 5),
            "heldAssets": held_assets,
        }
        if book == "ballast":
            data_block["riskOff"] = bool((lean or {}).get("risk_off"))
        out.append({
            "asset": name,
            "direction": "LONG",
            "marginPct": margin_pct_emit,      # PER-SLEEVE inverse-vol weight as PERCENT of equity (was marginUsd)
            "leverage": leverage,              # clamped to the sleeve's HL venue max
            "data": data_block,
        })
        pushed += 1
        free_margin -= margin_usd
        recent[au] = now
        emitted.append({"coin": name, "score": score, "leverage": leverage,
                        "margin_usd": margin_usd, "weight_pct": round(w * 100, 1)})

    print(f"[ox.scan] {book} scanned={len(basket)} sized={len(vols)} open_slots={open_slots} "
          f"emitted={pushed} budget_pct={budget_pct:.3f} risk_off={(lean or {}).get('risk_off')} "
          f"skipped_recent={recently_skipped}", file=sys.stderr)

    if ctx.state is not None:
        result = {"ts": now, "book": book, "scanned": len(basket), "sized": len(vols),
                  "open_slots": open_slots, "emitted": pushed, "picks": emitted,
                  "budget_pct": round(budget_pct, 3), "account_value": round(account_value, 2),
                  "elapsed_sec": round(time.time() - run_start, 2)}
        if lean is not None:
            result["risk_off"] = lean["risk_off"]
            result["risk_off_detail"] = lean["detail"]
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[ox.scan] WARNING: state append failed; next tick may re-emit: {exc!r}", file=sys.stderr)
    return out
