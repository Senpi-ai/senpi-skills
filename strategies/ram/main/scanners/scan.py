"""RAM — supervised scanner. Single-asset GOLD (xyz:GOLD) specialist.

The precious-metals analogue of DIRE (BRENTOIL). One instrument, one position, no
universe scan. Each tick:
  1) Single-position guard — if we already hold GOLD, emit nothing (the DSL owns the
     exit; RAM never opens a second slot).
  2) Signal dedup — refuse to re-emit within recentSignalTtlSeconds (defence-in-depth
     alongside the runtime's per-asset cooldown).
  3) Read GOLD candles (1h/4h) + funding; derive a risk-off (safe-haven) flag from
     market_get_funding_regime; score the pure `scoring.build_thesis`; emit ONE
     conviction-banded LONG/SHORT signal iff it clears minScore. NEVER closes.

Read-only + single-pass; never raises (every MCP read is guarded and degrades to
"no action this tick"). marginPct is a PERCENT in (0,100]. No daemon, no push_signal.

XYZ / gold notes (fleet rules — do NOT redesign):
  - asset = xyz:GOLD, dex = "xyz" — the xyz: prefix is mandatory (HIP-3 DEX) and is
    emitted verbatim in the signal.
  - xyz:GOLD trades 24/7 incl. weekends — NO market-hours gating anywhere.
  - xyz:GOLD uses ISOLATED margin; the runtime defaults xyz -> ISOLATED (not overridden).

PAYLOAD-SHAPE ASSUMPTIONS (auth token was invalid — not live-verified; tolerant like
raven/dire):
  - market_get_asset_data -> {"data": {"candles": {"1h":[...],"4h":[...]},
    "funding": <rate|dict>, ...}} — candles are dicts {open/high/low/close/volume} or
    [t,o,h,l,c,v] lists; funding via `_funding_of` (every spelling the corpus uses).
  - strategy_get_clearinghouse_state -> handled for BOTH the flat `assetPositions`
    shape (raven) AND the main/xyz sub-DEX sections shape (dire/HIP-3).
  - market_get_funding_regime -> parsed by scoring.risk_off_from_regime (categorical/
    boolean only; unknown -> neutral). Verify all three against real payloads.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import sys
import time

import scoring


def _read(ctx, tool, args, label):
    try:
        raw = ctx.senpi_mcp.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the tick
        print(f"[ram.scan] {label} read failed: {exc!r}", file=sys.stderr)
        return None
    if not raw:
        return None
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


def _asset_data(ctx, asset, dex):
    return _read(ctx, "market_get_asset_data", {
        "asset": asset, "candle_intervals": ["1h", "4h"],
        "include_funding": True, "include_order_book": False, "dex": dex,
    }, f"market_get_asset_data({asset})")


def _funding_of(md):
    """Current funding rate as a float (tolerant; 0.0 if absent -> funding factor
    neutralised). XYZ funding presence is not guaranteed; 0.0 is the safe default."""
    for k in ("funding", "funding_rate", "fundingRate", "current_funding"):
        v = scoring._num((md or {}).get(k))
        if v is not None:
            return v
    fh = (md or {}).get("funding") if isinstance((md or {}).get("funding"), dict) else None
    if isinstance(fh, dict):
        return scoring._f(fh.get("rate", fh.get("current")))
    return 0.0


def _held(ctx):
    """Bare-name set of currently-held coins, or None if the read fails / is
    unusable. Robust to BOTH clearinghouse shapes in the corpus: the flat top-level
    `assetPositions` (raven) AND the main/xyz sub-DEX sections (dire, HIP-3). Returns
    None (not an empty set) on a failed read so the caller defers to the next tick
    rather than risk a double-open."""
    d = _read(ctx, "strategy_get_clearinghouse_state",
              {"strategy_wallet": ctx.wallet}, "strategy_get_clearinghouse_state")
    if not isinstance(d, dict):
        return None
    out = set()

    def _collect(container):
        for e in (container or []):
            pos = e.get("position", e) if isinstance(e, dict) else {}
            coin = str(pos.get("coin", "")).strip()
            if coin and scoring._f(pos.get("szi")) != 0:
                out.add(coin.split(":", 1)[-1].upper())

    _collect(d.get("assetPositions", d.get("asset_positions", [])))
    for section in ("main", "xyz"):
        s = d.get(section)
        if isinstance(s, dict):
            _collect(s.get("assetPositions", s.get("asset_positions", [])))
    return out


def _risk_off(ctx, inputs):
    """Safe-haven (risk-off) flag from market_get_funding_regime. Tolerant + safe:
    True ONLY on an explicit risk-off label/flag; any read failure / unknown shape ->
    False (neutral, tilt off). Disable the read entirely with useFundingRegime:false."""
    if not bool(inputs.get("useFundingRegime", True)):
        return False
    data = _read(ctx, "market_get_funding_regime", {}, "market_get_funding_regime")
    if data is None:
        return False
    try:
        return scoring.risk_off_from_regime(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[ram.scan] funding-regime parse failed (neutral): {exc!r}", file=sys.stderr)
        return False


def scan(inputs, ctx):
    asset = inputs.get("asset", "xyz:GOLD") or "xyz:GOLD"
    dex = _dex_for(asset, inputs)
    min_score = scoring._f(inputs.get("minScore"), 5)
    ttl = scoring._f(inputs.get("recentSignalTtlSeconds"), 3600)
    now = time.time()
    bare = str(asset).split(":", 1)[-1].upper()

    st = (ctx.state.last() or {}) if ctx.state else {}
    recent = dict(st.get("recent", {}) or {})

    def _persist(result):
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": recent, "result": result})
            except Exception as exc:  # noqa: BLE001
                print(f"[ram.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    # ── 1) single-position guard: already holding gold? ──
    held = _held(ctx)
    if held is None:
        print("[ram.scan] clearinghouse unreadable — no action this tick", file=sys.stderr)
        return []                                     # do NOT persist a bogus result; act next tick
    if bare in held:
        print(f"[ram.scan] {asset} HOLD: already holding {bare} (single slot; DSL owns exit)",
              file=sys.stderr)
        _persist({"ts": now, "asset": asset, "emitted": False, "gate": "already_held"})
        return []

    # ── 2) signal dedup (defence-in-depth vs the runtime per-asset cooldown) ──
    last = recent.get(bare)
    if last is not None and (now - last) < ttl:
        print(f"[ram.scan] {asset} HOLD: recent signal {int(now - last)}s < ttl {int(ttl)}s",
              file=sys.stderr)
        _persist({"ts": now, "asset": asset, "emitted": False, "gate": "recent_signal"})
        return []

    # ── 3) fetch + thesis ──
    md = _asset_data(ctx, asset, dex)
    if not md:
        print(f"[ram.scan] {asset} asset_data unreadable — no action this tick", file=sys.stderr)
        _persist({"ts": now, "asset": asset, "emitted": False, "gate": "no_data"})
        return []
    candles = md.get("candles", {}) or {}
    funding = _funding_of(md)
    risk_off = _risk_off(ctx, inputs)

    th = scoring.build_thesis(asset, candles.get("1h", []), candles.get("4h", []),
                              funding, (None, 0), risk_off, inputs)
    if not th:
        print(f"[ram.scan] {asset} HOLD: no thesis (insufficient candles / no direction)",
              file=sys.stderr)
        _persist({"ts": now, "asset": asset, "emitted": False, "gate": "no_thesis"})
        return []
    if th["score"] < min_score:
        print(f"[ram.scan] {asset} HOLD: score={th['score']}<{min_score:g} {th['direction']} "
              f"risk_off={risk_off} | {th['reasons']}", file=sys.stderr)
        _persist({"ts": now, "asset": asset, "emitted": False, "gate": "score_low",
                  "score": th["score"], "direction": th["direction"]})
        return []

    band = scoring.band_for(th["score"], inputs)
    lev, mgn = scoring.sizing_for(band, inputs)
    recent[bare] = now
    sig = {
        "asset": asset,                     # WITH xyz: prefix — mandatory for the HIP-3 DEX
        "direction": th["direction"],
        "marginPct": mgn,                   # PERCENT of withdrawable — runtime sizes (marginPct/100)*withdrawable
        "leverage": lev,                    # conviction-banded 3/4/5; runtime applies it (ISOLATED on xyz)
        "data": {
            "score": float(th["score"]),
            "leverage": float(lev),
            "direction": th["direction"],
            "band": band,
            "reasons": th["reasons"],       # safe_haven_bid appears here when the risk-off tilt fired
        },
    }
    print(f"[ram.scan] {asset} EMIT {th['direction']} score={th['score']} band={band} "
          f"{lev}x {mgn}% risk_off={risk_off} | {th['reasons']}", file=sys.stderr)
    _persist({"ts": now, "asset": asset, "emitted": True, "gate": "pass", "score": th["score"],
              "direction": th["direction"], "band": band, "leverage": lev, "marginPct": mgn})
    return [sig]
