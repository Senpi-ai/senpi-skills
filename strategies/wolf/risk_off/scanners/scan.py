"""WOLF — supervised scanner (Runtime 3.0 port of the v2 wolf regime-rotation fund).

Shared VERBATIM by both instances. The book's identity is driven entirely by `inputs`
(the runtime.yaml `inputs:` map): `myRegime` (RISK_ON|RISK_OFF), `riskAssets`,
`defensives`, and the shared regime-probe spec. So one scan.py serves both books, exactly
as the v2 producer's one script served both legs via WOLF_LEG.

Each tick:
  1) READ the cross-asset REGIME probes (4h trend of equities / oil / gold / BTC / dollar),
     each read independently guarded — a bad probe read degrades that vote to no_data,
     it never rolls back the tick.
  2) TALLY the regime (scoring.tally_regime). ROTATION GATE: if regime != myRegime the
     book STANDS DOWN and emits nothing (this IS the rotation).
  3) When the regime is in force, score each candidate in its mandated direction
     (risk_on: LONG the risk complex; risk_off: LONG defensives + SHORT risk), each read
     guarded; emit a `marginPct` sizing intent + a per-signal `leverage` clamped to the
     asset's live HL venue max. The runtime sizes the dollars, owns cooldowns/risk gates,
     and trails the DSL exit. No daemon, no push_signal.

marginPct is a PERCENT in (0,100] — the runtime sizes (marginPct/100)*withdrawable."""

import sys
import time

import scoring

_DEFAULT_TTL = 180          # 180s — mirror the v2 RECENT_SIGNAL_TTL_SEC (anti re-fire race)

# Cross-asset regime probes — ported VERBATIM from the v2 producer's _REGIME_PROBES.
# Each maps a probe asset to the 4h-trend reading that votes RISK-ON. The opposite
# reading votes RISK-OFF; NEUTRAL abstains. Overridable via inputs.regimeProbes. The v2
# "dollar" probe (xyz:DXY) is dropped — delisted on HL with no live equivalent, so it only
# ever abstained.
_DEFAULT_PROBES = [
    {"asset": "xyz:XYZ100", "fallback": "xyz:SP500", "risk_on_when": "BULLISH", "label": "equities"},
    {"asset": "xyz:BRENTOIL", "fallback": None, "risk_on_when": "BEARISH", "label": "oil"},
    {"asset": "xyz:GOLD", "fallback": None, "risk_on_when": "BEARISH", "label": "gold"},
    {"asset": "BTC", "fallback": None, "risk_on_when": "BULLISH", "label": "btc"},
]


def _dex_for(asset):
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _fetch_candles(ctx, asset, intervals):
    """Guarded asset-data read. Returns {"candles": {...}, "ctx": {...}} or None.
    A read error degrades to None (caller skips that asset) — never rolls back the tick."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "dex": _dex_for(asset),
            "include_funding": False,
            "include_order_book": False,
        })
    except Exception as exc:  # noqa: BLE001 — a read error must NOT roll back the whole tick
        print(f"[wolf.scan] market_get_asset_data({asset}) read failed, skipping: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    d = md.get("data", md) if isinstance(md, dict) else {}
    if not isinstance(d, dict):
        return None
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def _detect_regime(ctx, probes, threshold):
    """Read each probe's 4h trend (guarded, with fallback) and tally the regime.
    A failed/short probe read votes no_data — degrade, never crash."""
    probe_trends = {}
    for p in probes:
        asset = p.get("asset")
        label = p.get("label", asset)
        md = _fetch_candles(ctx, asset, ["4h"])
        c4 = md["candles"].get("4h", []) if md else []
        if len(c4) < 6 and p.get("fallback"):
            md = _fetch_candles(ctx, p["fallback"], ["4h"])
            c4 = md["candles"].get("4h", []) if md else []
        if len(c4) < 6:
            probe_trends[label] = "no_data"
            continue
        trend4, _ = scoring.trend_structure(c4)
        probe_trends[label] = trend4
    return scoring.tally_regime(probe_trends, probes, threshold)


def _clamp_leverage(desired, venue_max):
    """Clamp desired leverage to [1, venue_max]. Ported verbatim from v2 clamp_leverage."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def _build_targets(my_regime, risk_assets, defensives):
    """[(name, want_direction)] for this book. risk_on: LONG the risk complex.
    risk_off: LONG defensives + SHORT the risk complex. Ported verbatim from v2 build_targets."""
    pairs = []
    if my_regime == "RISK_ON":
        for n in risk_assets:
            pairs.append((n, "LONG"))
    else:
        for n in defensives:
            pairs.append((n, "LONG"))
        for n in risk_assets:
            pairs.append((n, "SHORT"))
    return [(n, w) for (n, w) in pairs if isinstance(n, str)]


def scan(inputs, ctx):
    my_regime = (inputs.get("myRegime", "RISK_ON") or "RISK_ON").upper()
    risk_assets = inputs.get("riskAssets", [])
    defensives = inputs.get("defensives", [])
    probes = inputs.get("regimeProbes", _DEFAULT_PROBES)
    threshold = int(inputs.get("regimeThreshold", 2))
    min_score = float(inputs.get("minScore", 5))
    margin_pct = float(inputs.get("marginPct", 20))   # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 5))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    # signal-dedup map (defence-in-depth alongside the runtime's per-asset cooldown gate)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    # ── REGIME DETECTOR — the shared brain (guarded reads) ──
    regime = _detect_regime(ctx, probes, threshold)

    # ── ROTATION GATE — this book only trades in its regime (the rotation) ──
    if regime["regime"] != my_regime:
        result = {"ts": now, "regime": regime["regime"], "regime_net": regime["net"],
                  "emitted": 0, "gate": "standing_down", "detail": regime["detail"]}
        print(f"[wolf.scan] STANDING DOWN — regime={regime['regime']} net={regime['net']} "
              f"(this book trades only in {my_regime}) | {regime['detail']}", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": recent, "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"[wolf.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    # ── REGIME IN FORCE — score the book's universe in its mandated direction ──
    targets = _build_targets(my_regime, risk_assets, defensives)
    candidates = []
    for name, want in targets:
        au = name.upper()
        last = recent.get(au)
        if last is not None and (now - last) < ttl:        # signal-dedup
            continue
        md = _fetch_candles(ctx, name, ["1h", "4h"])
        if not md:
            continue
        c1 = md["candles"].get("1h", [])
        c4 = md["candles"].get("4h", [])
        th = scoring.score_directional(name, c1, c4, md["ctx"], want, inputs)
        if not th or th["score"] < min_score:
            continue
        # per-signal leverage: strict maxLeverage clamp, then this asset's live HL venue
        # max (ctx.max_leverage from asset_context). max_leverage may be absent → clamp uses desired.
        venue_max = (md["ctx"] or {}).get("max_leverage", (md["ctx"] or {}).get("maxLeverage"))
        leverage = _clamp_leverage(max_lev, venue_max)
        th["_leverage"] = leverage
        candidates.append(th)

    if not candidates:
        result = {"ts": now, "regime": regime["regime"], "regime_net": regime["net"],
                  "emitted": 0, "gate": "no_candidate", "scanned": len(targets),
                  "detail": regime["detail"]}
        print(f"[wolf.scan] WAITING — {my_regime} confirmed (net {regime['net']}) but no name "
              f"cleared min score {min_score:.0f} ({len(targets)} scanned)", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": recent, "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"[wolf.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    # highest conviction first — runtime's slots/maxSlots applies the ceiling
    candidates.sort(key=lambda x: x["score"], reverse=True)

    out = []
    emitted = []
    for th in candidates:
        au = th["coin"].upper()
        leverage = th["_leverage"]
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — the runtime sizes the dollars
            "leverage": leverage,             # per-signal venue-clamped; runtime applies it
            "data": {
                "score": min(th["score"] / scoring.NORM_DIV, 1.0),  # v2 wire score (normalized)
                "rawScore": th["score"],
                "leverage": leverage,
                "direction": th["direction"],
                "reasons": th["reasons"],
                "trend4h": th.get("trend4h"),
                "regime": regime["regime"],
                "regimeNet": regime["net"],
                "own24h": th.get("own24h", 0),
            },
        })
        recent[au] = now
        emitted.append({"coin": th["coin"], "direction": th["direction"],
                        "score": th["score"], "leverage": leverage})

    print(f"[wolf.scan] EMIT {len(out)} — regime={regime['regime']} net={regime['net']} | "
          f"{[(e['coin'], e['direction'], e['score'], str(e['leverage']) + 'x') for e in emitted]}",
          file=sys.stderr)

    result = {"ts": now, "regime": regime["regime"], "regime_net": regime["net"],
              "emitted": len(out), "gate": "pass", "scanned": len(targets),
              "candidates": len(candidates), "detail": regime["detail"], "picks": emitted}
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[wolf.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
