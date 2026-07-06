"""ELEPHANT FADE book — supervised scanner (Runtime 3.0 port of the v2 elephant-producer.py
FADE leg, ELEPHANT_LEG=fade).

Global-macro mean-reversion book over the cross-asset macro complex (XYZ equity indices /
metals / energy / FX + BTC). Per tick it:
  1. reads the wallet clearinghouse (account value + held names + free margin),
  2. builds the live universe: the curated macro whitelist intersected with the live
     instrument board (names not live are skipped — v2 build_universe),
  3. scores every non-held, non-recently-signaled name with the pure scoring.score_fade
     (picks the more-extreme side: oversold->LONG / overbought->SHORT from 1h RSI extreme +
     stretch from the 20-bar 1h MA, with a 4h regime knife guard so it never fades a strong
     macro trend),
  4. emits the top candidates up to open slots AND what free margin can FUND, as a top-level
     marginPct INTENT (PERCENT) + a per-name venue-clamped leverage.

Read-only + single-pass. No daemon, no push_signal, no create_position, no order-lifecycle
mutation — the runtime sizes the dollars, owns slots/cooldowns/risk gates, and trails the DSL.

FIDELITY NOTES vs elephant-producer.py v1.0.0 (ELEPHANT_LEG=fade):
  - SET of scored assets, scoring weights/gates, the more-extreme-side selection, the RSI/
    stretch tiers, the 4h knife guard (+1 with-bias / -2 against a strong trend), minScore (4),
    marginPct (0.15 fraction -> 15 PERCENT), maxLeverage (5), maxSlots (3), venue leverage
    clamp, and the 180s recent-signals race-dedup TTL are all preserved EXACTLY.
  - v2 marginPct was a FRACTION (0.15) used as account_value * marginPct -> marginUsd. This
    port emits `marginPct` PERCENT (15) at the top level; the runtime sizes
    (marginPct/100)*withdrawable. The defensive "<=1.0 means a pasted fraction, x100" guard
    is applied in scan().
  - The fade scoring needs NO 24h momentum (unlike the trend book) — it uses only 1h/4h
    candles. market_list_instruments is still read for the per-name venue leverage clamp +
    the universe intersection (same as v2 get_universe_meta). FLAGGED: this means a fade tick
    still does one instrument-board read even though ret24h is unused — matches v2, which
    always read get_universe_meta() regardless of leg.
  - DROPPED v2 order-lifecycle / mutation behaviour: the v2 producer called push_signal()
    (a POST) + a JSON recent-signals cache; scan() is read-only and returns plain dicts. The
    recent-signals cache moves to ctx.state (same TTL/prune semantics). The free-margin
    affordability cap is preserved (pure arithmetic over a read, not a mutation). Nothing
    else mutated in v2 (no cancel_order / resting-order purge), so nothing else was dropped.
  - v2 emitted up to min(open_slots, affordable) candidates; preserved.
"""

import sys
import time

import scoring

# v2 _MACRO_WHITELIST (config.allowedAssets overrides). Pruned to LIVE HL instruments only:
# xyz:NIFTY / xyz:IBOV / xyz:DXY are delisted with no live equivalent, so they are dropped
# (they never scan anyway).
_MACRO_WHITELIST_DEFAULT = [
    "BTC",
    "xyz:SP500", "xyz:XYZ100", "xyz:JP225", "xyz:KR200",
    "xyz:GOLD", "xyz:SILVER", "xyz:PLATINUM", "xyz:COPPER",
    "xyz:BRENTOIL", "xyz:CL", "xyz:NATGAS",
    "xyz:EUR", "xyz:JPY", "xyz:GBP",
]
_DEFAULT_TTL = 180   # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup (3x ALO open fill window)


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission/illiquid error on ONE read must NOT roll
    back the whole tick (the contract rolls any uncaught exception back to []). Returns None
    on failure so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001 — one bad name must not kill the universe tick
        print(f"[elephant.fade.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _unwrap(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else resp


def _get_positions(ctx):
    """Returns (account_value, [position dicts], free_margin). The 'main' and 'xyz'
    clearinghouse sections are TWO VIEWS of ONE cross-margined wallet — accountValue is
    taken ONCE via max() across sections, NEVER summed (v2-quirk). Free margin = equity -
    committed margin. Ported verbatim from v2 cfg.get_positions, including the read-sanity
    guard (margin in use + empty positions -> corrupt read -> skip tick)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, [], 0.0
    data = _unwrap(ch)
    positions, account_value, used = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s, dict) else {}
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        used = max(used, float(ms.get("totalMarginUsed", 0) or 0),
                   abs(float(ms.get("totalNtlPos", 0) or 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": float(pos.get("marginUsed", 0) or 0),
            })
    # v2-quirk read-sanity guard (funding/$0 glitch 2026-06): margin/notional IN USE but an
    # EMPTY positions list is a corrupt read — sizing or held-dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    if used > 1.0 and not positions:
        print("[elephant.fade.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


def _get_universe_meta(ctx):
    """name -> {max_leverage}. Skips delisted. Verbatim v2 get_universe_meta() — the fade
    book uses this only for the per-name venue leverage clamp + the universe intersection
    (ret24h is unused by the fade thesis)."""
    resp = _read(ctx, "market_list_instruments", {})
    out = {}
    if not resp:
        return out
    insts = _unwrap(resp)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict) or inst.get("is_delisted"):
            continue
        name = inst.get("name") or (inst.get("context", {}) or {}).get("coin")
        if not name:
            continue
        entry = {"max_leverage": inst.get("max_leverage", inst.get("maxLeverage"))}
        out[name] = entry
        out[name.upper()] = entry
    return out


def _fetch_candles(ctx, asset):
    """1h + 4h candles for ONE asset, dex-routed for xyz. Guarded — a bad name returns
    ([], []) and the universe loop skips it. Verbatim v2 fetch_candles intervals."""
    resp = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": _dex_for(asset),
        "include_funding": False,
        "include_order_book": False,
    })
    if not resp:
        return [], []
    if isinstance(resp, dict) and resp.get("success") is False:
        return [], []
    d = _unwrap(resp)
    candles = (d.get("candles", {}) or {}) if isinstance(d, dict) else {}
    return candles.get("1h", []) or [], candles.get("4h", []) or []


def _build_universe(whitelist, meta_map):
    """Macro whitelist intersected with the live instrument board so dead/unavailable names
    are skipped. Verbatim v2 build_universe()."""
    out = []
    for name in whitelist:
        if not isinstance(name, str):
            continue
        if meta_map.get(name) or meta_map.get(name.upper()):
            out.append(name)
    return out


def scan(inputs, ctx):
    run_start = time.time()
    whitelist = inputs.get("allowedAssets", _MACRO_WHITELIST_DEFAULT)
    min_score = int(inputs.get("minScore", 4))
    margin_pct = float(inputs.get("marginPct", 15))          # PERCENT of withdrawable (0,100]
    # defensive: a stale config may carry the v2 FRACTION (0.15). <=1.0 means a pasted
    # fraction -> x100. (dire/koala guard.)
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0
    max_lev = int(inputs.get("maxLeverage", 5))
    max_slots = int(inputs.get("maxSlots", 3))
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
            print(f"[elephant.fade.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    account_value, positions, free_margin = _get_positions(ctx)
    if account_value <= 0:
        _persist()
        return []                                            # no value / corrupt read — skip tick
    held = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held}

    open_slots = max_slots - len(held)
    if open_slots <= 0:
        _persist()
        print(f"[elephant.fade.scan] WAITING — slots full held={held}", file=sys.stderr)
        return []                                            # book full — runtime also caps via slots

    meta_map = _get_universe_meta(ctx)
    universe = _build_universe(whitelist, meta_map)

    candidates = []
    scanned = 0
    for name in universe:
        if name.upper() in held_set:
            continue
        if recent.get(name.upper()) is not None and (now - recent[name.upper()]) < ttl:
            continue                                         # signal-dedup (race-window)
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        scanned += 1
        c1, c4 = _fetch_candles(ctx, name)                   # per-asset read-guarded
        if len(c1) < 22 or len(c4) < 6:
            continue
        thesis = scoring.score_fade(name, c1, c4, inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        _persist()
        print(f"[elephant.fade.scan] WAITING — no macro name cleared min score {min_score}; "
              f"scanned={scanned} held={held} elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
        return []

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # v2-quirk: never emit more than the wallet can actually FUND. Per-name margin =
    # (marginPct/100)*accountValue; 1.1 = fee/slippage headroom.
    per_name_margin = (margin_pct / 100.0) * account_value
    affordable = int(free_margin / (per_name_margin * 1.1)) if per_name_margin > 0 else 0
    to_emit = candidates[:max(0, min(open_slots, affordable))]

    out = []
    for th in to_emit:
        venue_max = (th.get("_meta") or {}).get("max_leverage")
        leverage = scoring.clamp_leverage(max_lev, venue_max)  # v2-quirk: per-name venue clamp
        if leverage <= 0:
            continue
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct,                           # PERCENT intent — runtime sizes the $
            "leverage": leverage,                              # already venue-clamped
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "direction": th["direction"],
                "reasons": th["reasons"][:6],
                "trend4h": th.get("trend4h"),
                "stretchPct": round(th.get("stretchPct", 0), 3),
                "rsi": round(th.get("rsi", 0), 1),
                "heldAssets": held,
            },
        })
        recent[th["coin"].upper()] = now

    _persist()
    if out:
        top = out[0]
        print(f"[elephant.fade.scan] EMIT {len(out)} (top {top['asset']} {top['direction']} "
              f"{top['leverage']}x marginPct={margin_pct:.1f}%) scanned={scanned} "
              f"candidates={len(candidates)} elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    else:
        print(f"[elephant.fade.scan] WAITING — candidates={len(candidates)} but none fundable/clamped; "
              f"free_margin={free_margin:.2f} elapsed={time.time() - run_start:.2f}s", file=sys.stderr)
    return out
