"""RHINO — supervised scanner (shared VERBATIM by both instances).

Runtime 3.0 port of the v2 Rhino producer (rhino-producer.py). Rhino carries CHEAP
CONVEXITY: it bleeds a little in calm and pays big in shocks. ONE shared scan.py serves
both books; the `book` input selects which (mirrors the v2 RHINO_LEG env var):

  book=hedge       Always-on Hedge book. Scores the defensive whitelist (gold / oil /
                   dollar / yen) and goes LONG any that is clearly trending up — the
                   standing insurance. NOT stress-gated (runs the stress read for
                   telemetry only). Small marginPct, wide let-it-run DSL.

  book=escalation  Stress-gated Escalation book. DORMANT in calm; the shared STRESS
                   detector must confirm a shock before it deploys. It then goes LONG
                   the spiking crisis complex and SHORT the cratering risk complex —
                   the convex add. Larger marginPct, moderate-tight DSL.

Read-only, single-pass, no daemon. Emits a `marginPct` SIZING INTENT (percent of
withdrawable; the runtime sizes the dollars) plus a flat per-signal `leverage` (v2 is
NOT tiered — maxLeverage clamped to each asset's HL venue max). The runtime owns the
LLM gate (pass-through), the cooldowns / risk gates, and the DSL exit.

EVERY ctx.senpi_mcp.call_tool is READ-GUARDED: a failed read degrades (the stress probe
counts as calm, a candidate is skipped, the account read returns no value) — it never
crashes the tick. On any unexpected failure the whole scan returns []."""

import sys
import time

import scoring

_DEFAULT_TTL = 180          # 3m — mirror the v2 RECENT_SIGNAL_TTL_SEC race-dedup window

# Cross-asset STRESS probes (ported verbatim from v2 _STRESS_PROBES). Each fires when
# its asset confirms the stress direction, via 4h trend OR a 1h range break + ATR surge.
_STRESS_PROBES = [
    {"asset": "xyz:BRENTOIL", "fallback": None, "want": "up", "label": "oil"},
    {"asset": "xyz:XYZ100", "fallback": "xyz:SP500", "want": "down", "label": "equities"},
    {"asset": "xyz:GOLD", "fallback": None, "want": "up", "label": "gold"},
    {"asset": "BTC", "fallback": None, "want": "down", "label": "btc"},
]


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _read(ctx, tool, args, label):
    """Read-guarded MCP call: returns the unwrapped JSON doc, or None on any failure.
    A failed read must DEGRADE (skip a candidate / count a probe as calm), never crash."""
    try:
        md = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — one bad read must not roll back the whole tick
        print(f"[rhino.scan] {label} read failed, degrading: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else md


def _fetch_candles(ctx, asset, intervals):
    d = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": intervals,
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    }, f"candles({asset})")
    if not d:
        return None
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


# ── live HL universe meta (auth-free, read-guarded) — validate every traded asset ──

def _build_universe_meta(ctx):
    """Return {name -> {max_leverage}} from the LIVE board via market_list_instruments.
    Every asset Rhino trades or probes is intersected with this map, so a fake / delisted
    ticker is silently dropped (never an un-fillable order). Read-guarded: returns {} on
    failure, which makes build_targets emit nothing this tick (degrade, never crash)."""
    d = _read(ctx, "market_list_instruments", {}, "market_list_instruments")
    out = {}
    if not d:
        return out
    insts = d.get("instruments", d) if isinstance(d, dict) else d
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        entry = {"max_leverage": inst.get("max_leverage", inst.get("maxLeverage"))}
        out[name] = entry
        out[name.upper()] = entry
    return out


def _venue_max(meta_map, name):
    m = meta_map.get(name) or meta_map.get(name.upper()) or {}
    return m.get("max_leverage")


# ── account / positions (read-guarded; two views = one cross-margined wallet) ──

def _get_positions(ctx):
    """Returns (account_value, [position_dicts]). The 'main' and 'xyz' clearinghouse
    sections are TWO VIEWS of ONE cross-margined wallet — accountValue is taken ONCE via
    max() across sections, NEVER summed (summing double-counts and 2x-oversizes). Ported
    verbatim from v2 cfg.get_positions, including the read-sanity guard (margin in use +
    empty positions => corrupt read => skip the tick)."""
    d = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet},
              "clearinghouse")
    if not d:
        return 0.0, []
    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = d.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": float(pos.get("marginUsed", 0) or 0),
            })
    # read-sanity guard (v2): a corrupt read can report margin/notional IN USE while
    # returning an EMPTY positions list; sizing/dedup off that re-enters held names. Skip.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = d.get(_sec, {}) if isinstance(d, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, float(_ms.get("totalMarginUsed", 0) or 0),
                   abs(float(_ms.get("totalNtlPos", 0) or 0)))
    if _use > 1.0 and not positions:
        return 0.0, []
    return account_value, positions


# ── STRESS detector — fetch + run the shared probes (escalation gate + hedge telemetry) ──

def _detect_stress(ctx, inputs):
    """Tally cross-asset stress probes + a BTC vol-expansion flag. STRESS is declared when
    the count clears `stressThreshold`. Ported verbatim from v2 detect_stress; every read
    is guarded so a missing probe simply doesn't fire (degrade, never crash)."""
    threshold = int(inputs.get("stressThreshold", 2))
    vol_surge = float(inputs.get("volSurge", 1.5))
    fired, detail = 0, {}
    for p in _STRESS_PROBES:
        asset = p["asset"]
        md = _fetch_candles(ctx, asset, ["1h", "4h"])
        if (not md or len(md["candles"].get("4h", [])) < 6) and p.get("fallback"):
            asset = p["fallback"]
            md = _fetch_candles(ctx, asset, ["1h", "4h"])
        if not md:
            detail[p["label"]] = "no_data"
            continue
        c1 = md["candles"].get("1h", [])
        c4 = md["candles"].get("4h", [])
        ok, reason = scoring.stress_probe(c1, c4, p["want"], inputs)
        detail[p["label"]] = reason
        if ok:
            fired += 1
    # BTC vol-expansion flag
    volr = 0.0
    bmd = _fetch_candles(ctx, "BTC", ["1h"])
    if bmd:
        volr = scoring.vol_ratio(bmd["candles"].get("1h", []), inputs)
    if volr >= vol_surge:
        fired += 1
        detail["vol"] = f"expanding_{volr:.2f}x"
    else:
        detail["vol"] = f"calm_{volr:.2f}x"
    return {"stress": fired >= threshold, "fired": fired, "threshold": threshold, "detail": detail}


# ── universe — (name, wanted_direction) pairs for this book, intersected with the board ──

def _build_targets(inputs, meta_map):
    """hedge: LONG the defensives. escalation: LONG the crisis complex + SHORT the risk
    complex. Each name is intersected with the LIVE board (meta_map) — a name absent from
    the live universe is dropped. Ported verbatim from v2 build_targets."""
    book = (inputs.get("book", "hedge") or "hedge").lower()
    defensives = inputs.get("defensives", [])
    crisis = inputs.get("crisisLongs", [])
    risk = inputs.get("riskAssets", [])
    pairs = []
    if book == "hedge":
        for n in defensives:
            pairs.append((n, "LONG"))
    else:
        for n in crisis:
            pairs.append((n, "LONG"))
        for n in risk:
            pairs.append((n, "SHORT"))
    out = []
    for name, want in pairs:
        if not isinstance(name, str):
            continue
        if meta_map.get(name) or meta_map.get(name.upper()):   # LIVE-universe validation
            out.append((name, want))
    return out


def scan(inputs, ctx):
    book = (inputs.get("book", "hedge") or "hedge").lower()
    min_score = float(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 10))   # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 5))       # flat clamp; v2 is NOT tiered
    max_slots = int(inputs.get("maxSlots", 3))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    account_value, positions = _get_positions(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        _persist(ctx, recent, {"ts": now, "book": book, "emitted": 0, "note": "no_account_value"})
        return []

    stress = _detect_stress(ctx, inputs)

    # ── ESCALATION GATE — the escalation book only fires under confirmed stress ──
    if book == "escalation" and not stress["stress"]:
        print(f"[rhino.scan] escalation DORMANT — no stress (fired {stress['fired']}/"
              f"{stress['threshold']}) {stress['detail']}", file=sys.stderr)
        _persist(ctx, recent, {"ts": now, "book": book, "emitted": 0, "stress": False,
                               "stress_fired": stress["fired"], "stress_detail": stress["detail"]})
        return []

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        _persist(ctx, recent, {"ts": now, "book": book, "emitted": 0, "note": "slots_full",
                               "held": held_assets})
        return []

    meta_map = _build_universe_meta(ctx)
    targets = _build_targets(inputs, meta_map)

    candidates = []
    for name, want in targets:
        au = name.upper()
        if au in held_set:
            continue
        last = recent.get(au)
        if last is not None and (now - last) < ttl:        # race-dedup
            continue
        md = _fetch_candles(ctx, name, ["1h", "4h"])
        if not md:
            continue
        c1 = md["candles"].get("1h", [])
        c4 = md["candles"].get("4h", [])
        th = scoring.score_directional(c1, c4, md["ctx"], want, inputs)
        if not th or th["score"] < min_score:
            continue
        th["coin"] = name
        th["_venue_max"] = _venue_max(meta_map, name)
        candidates.append(th)

    if not candidates:
        note = ("STRESS confirmed but no crisis/risk name cleared min score"
                if book == "escalation" else "no defensive trending up cleared min score")
        print(f"[rhino.scan] {book} WAITING — {note} ({min_score:.0f}); "
              f"scanned {len(targets)} stress={stress['stress']}", file=sys.stderr)
        _persist(ctx, recent, {"ts": now, "book": book, "emitted": 0, "scanned": len(targets),
                               "candidates": 0, "stress": stress["stress"],
                               "stress_fired": stress["fired"], "stress_detail": stress["detail"]})
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)
    to_emit = candidates[:open_slots]

    out = []
    emitted = []
    for th in to_emit:
        leverage = scoring.clamp_leverage(max_lev, th.get("_venue_max"))
        if leverage <= 0:
            continue
        recent[th["coin"].upper()] = now
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — the runtime sizes the dollars
            "leverage": leverage,             # flat 5x clamped to venue max (v2 not tiered)
            "data": {
                "score": th["score"],
                "direction": th["direction"],
                "reasons": th["reasons"],
                "trend4h": th["trend4h"],
                "own24h": th["own24h"],
                "rsi": th["rsi"],
                "stress": stress["stress"],
                "stressFired": stress["fired"],
            },
        })
        emitted.append({"coin": th["coin"], "direction": th["direction"], "score": th["score"],
                        "leverage": leverage})

    print(f"[rhino.scan] {book} EMIT {len(out)}: {emitted} | stress={stress['stress']} "
          f"fired={stress['fired']}/{stress['threshold']}", file=sys.stderr)
    _persist(ctx, recent, {"ts": now, "book": book, "emitted": len(out), "emitted_detail": emitted,
                           "scanned": len(targets), "candidates": len(candidates),
                           "stress": stress["stress"], "stress_fired": stress["fired"],
                           "stress_detail": stress["detail"], "held": held_assets})
    return out


def _persist(ctx, recent, result):
    """Persist the dedup map + this tick's result record (read back via ctx.state.recent(n))."""
    # prune the dedup map so it stays bounded (4x TTL — mirror v2 _prune_recent_signals)
    if ctx.state is None:
        return
    try:
        ctx.state.append({"recent": recent, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[rhino.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
