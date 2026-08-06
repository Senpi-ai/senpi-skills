"""SPIDER SCALP — supervised external scanner entrypoint.

Port of senpi-skills/spider (producer v5.1.1, SPIDER_LEG=scalp) to the v2
`scan(inputs, ctx)` contract. The daemon loop is gone — the runtime supervisor
calls scan() every interval_seconds.

What stayed faithful to the source:
  - score_scalp scoring + all thresholds (see scoring.py — ported verbatim).
  - Static universe (allowedAssets): majors + energy.
  - The recent-signal dedup (RECENT_SIGNAL_TTL): a coin signaled in the last
    `recentSignalTtlSeconds` is skipped. Source kept this in a JSON file; here it
    lives in ctx.state.
  - Leverage clamp to the per-asset HL venue max (clamp_leverage).
  - The dual-DEX equity collapse via max() (NOT sum()) when reading account value.

What was simplified vs the source (FLAGGED, same as the swing leg):
  - The producer's `affordable` per-tick emission cap moves to the v2 runtime
    (strategy.slots + risk gates). scan() emits ALL qualifying candidates above
    minScore (held + recently-signaled filtered). The signal-GATING thresholds
    (minScore, held-set, recent-dedup, venue min notional) are preserved exactly.
"""

import sys
import time

import scoring


_DEFAULT_RECENT_TTL = 180

# Per-signal margin as a PERCENT of equity in (0,100] (source marginPct 0.15 fraction).
_DEFAULT_MARGIN_PCT = 15.0


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_universe_meta(ctx):
    """{name: {"max_leverage": int|None, "ctx": {...}}} for every live
    instrument on both dexes (one market_list_instruments call)."""
    try:
        data = ctx.senpi_mcp.call_tool("market_list_instruments", {})
    except Exception:
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
        if inst.get("is_delisted"):
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
    try:
        data = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "dex": _dex_for(asset),
            "include_funding": False,
            "include_order_book": False,
        })
    except Exception:
        return None
    if not data or (isinstance(data, dict) and not data.get("success", True)):
        return None
    d = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(d, dict):
        return None
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def _get_account(ctx):
    """(account_value, [position_dicts], free_margin) — dual-DEX equity collapse via
    max(); free margin = equity - committed margin (sum of per-position marginUsed)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception:
        return 0.0, [], 0.0
    if not ch:
        return 0.0, [], 0.0
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, [], 0.0
    positions, account_value, used = [], 0.0, 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        used = max(used, float(ms.get("totalMarginUsed", 0) or 0),
                   abs(float(ms.get("totalNtlPos", 0) or 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": float(pos.get("marginUsed", 0) or 0)})
    # Read-sanity guard (ported from camel): margin/notional IN USE but an EMPTY
    # positions list is a corrupt read — sizing or held-dedup off that re-enters
    # held names (pyramiding) and mis-sizes. Skip the tick.
    if used > 1.0 and not positions:
        print("[spider.scalp.scan] read-sanity guard: margin in use but empty "
              "positions — skipping tick", file=sys.stderr)
        return 0.0, [], 0.0
    free_margin = max(0.0, account_value - sum(p["margin"] for p in positions))
    return account_value, positions, free_margin


# ── ctx.state recent-signal dedup ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def scan(inputs, ctx):
    now = time.time()
    min_score = inputs.get("minScore", 4)
    # margin PERCENT of equity in (0,100] (source fraction 0.15 -> 15). Defensive
    # guard: a value <=1.0 is a pasted FRACTION -> x100.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        margin_pct *= 100
    leg_max_lev = inputs.get("maxLeverage", 5)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))

    account_value, positions, free_margin = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        return []

    # Book full → emit nothing (and skip the universe scan). The runtime also caps via
    # strategy.slots, but emitting INTO a full book is what generated the insufficient-funds
    # spam — camel guards the same way (open_slots return before building the universe).
    max_slots = max(1, int(inputs.get("maxSlots", 4)))
    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        print(f"[spider.scalp.scan] WAITING — slots full ({len(held_assets)}/{max_slots})",
              file=sys.stderr)
        return []

    min_notional = max(account_value * min_notional_pct, venue_min_notional)
    margin_usd = round(account_value * margin_pct / 100.0, 2)

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    meta_map = _get_universe_meta(ctx)
    allowed = list(inputs.get("allowedAssets", []))

    candidates = []
    for asset in allowed:
        au = asset.upper()
        if au in held_set:
            continue
        if _was_recently_signaled(signaled, asset, ttl, now):
            continue
        meta = meta_map.get(asset) or meta_map.get(au)
        if not meta:
            continue
        md = _fetch_candles(ctx, asset, ["5m", "15m", "1h"])
        if not md:
            continue
        c15 = md["candles"].get("15m", [])
        c1 = md["candles"].get("1h", [])
        thesis = scoring.score_scalp(asset, c15, c1, meta.get("ctx", {}), inputs)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Cap the emitted batch. The runtime opens up to maxSlots positions, and the /signals intake caps items
    # per POST — emitting the whole gated basket (up to xyzMaxNames names on a broad day) overflows that cap
    # and the runtime drops the ENTIRE batch (max_items_exceeded → silently missed entries). Emit only the
    # top-scoring maxEmit (default maxSlots); the runtime picks its slots from these. Overridable via inputs.maxEmit.
    emit_cap = max(1, int(inputs.get("maxEmit") or inputs.get("maxSlots") or 4))
    # ...and by the FREE slots and what FREE margin can actually pay for. margin_usd is already
    # (marginPct/100)*account_value (= camel's per_name_margin); ×1.1 leaves fee/slippage headroom on
    # a FEE_OPTIMIZED_LIMIT taker fill. Without this the batch tail asks for margin already in use →
    # insufficient-funds spam once the book is partly filled. Ports camel's open_slots/affordable cap;
    # PER-NAME SIZE IS UNCHANGED — only the emitted count. (scalp at 60% is quiet today; kept in sync.)
    affordable = int(free_margin / (margin_usd * 1.1)) if margin_usd > 0 else 0
    emit_cap = max(0, min(emit_cap, open_slots, affordable))
    out = []
    for th in candidates:
        if len(out) >= emit_cap:
            break
        leverage = scoring.clamp_leverage(leg_max_lev, th["_meta"].get("max_leverage"))
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        # Runtime 3.0 sizes off a top-level marginPct (PERCENT of equity in (0,100]),
        # NOT a top-level marginUsd (silently dropped). Emit the configured PERCENT
        # verbatim, clamped into the runtime's accepted range.
        margin_pct_emit = round(min(max(margin_pct, 0.01), 100.0), 4)
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginPct": margin_pct_emit,
            "leverage": leverage,
            "data": {
                "score": th["score"],
                "direction": th["direction"],
                "reasons": th["reasons"],
                "heldAssets": held_assets,
                "trend1h": th.get("trend1h"),
                "rsi": round(th.get("rsi", 0), 1),
                "stretchPct": round(th.get("stretchPct", 0), 3),
            },
        })
        signaled[th["coin"].upper()] = now

    # If this append fails (e.g. state_history_max_count is 0/unset so append
    # raises, or a transient persist error), the next tick reloads stale dedup
    # state and may re-emit already-suppressed signals. Log-and-continue: don't
    # crash the tick, but make the failure visible on stderr (the runtime
    # supervisor captures the scaffold child's stderr) instead of swallowing it.
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled})
        except Exception as exc:
            print(
                f"[spider.scalp.scan] WARNING: dedup-state append failed; next "
                f"tick may re-emit suppressed signals: {exc!r}",
                file=sys.stderr,
            )

    return out
