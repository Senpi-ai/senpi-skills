"""OTTER — supervised scanner (Runtime 3.0 port of the v2 OTTER producer).

Multi-asset UNIVERSE scanner, emit-one. A faithful port of the v2 "Open Interest
Velocity Hunter" (otter-producer.py v2.0.0 / SKILL.md v2.0.1):

  1. Read account state + open-position count (sizing context).
  2. Build the universe — every HL crypto perp from `market_list_instruments`
     with OI > 0 and a valid mark price (XYZ banned at parse time — different OI
     dynamics; Bald Eagle's lane).
  3. Maintain a ROLLING OI HISTORY per asset in ctx.state (last 60 samples =
     5h at this scanner's ~5min cadence): append {ts, oi, mark_px} each tick.
  4. Compute 1h + 4h OI/price deltas, apply the 4-quadrant TOP-only hard gates,
     and score every survivor via the pure `scoring.evaluate_oi_velocity`.
  5. Spread-gate the top eligible candidates (market_get_asset_data orderbook;
     REJECT > 5 bps, +1/+2 bonus inside), then emit ONE signal for the single
     highest-scoring survivor clearing MIN_SCORE — a marginPct sizing INTENT
     plus a per-score leverage tier.

Read-only + single-pass. NO daemon, NO push_signal, NO create_position — the
runtime sizes the dollars, owns the cooldowns/risk gates/slots, and trails the
DSL exit. The rolling OI history, per-asset cooldown, and per-tick signal dedup
all live in ctx.state (belt-and-suspenders alongside the runtime's per-asset
cooldown gate).

FIDELITY NOTES vs the v2 producer (otter-producer.py v2.0.0):
  - State migration: v2 persisted the rolling OI history to
    state/<wallet-hash>/oi-history.json and per-asset cooldowns to
    asset-cooldowns.json via atomic file writes. This port moves BOTH into
    ctx.state (the runtime's transactional history store) — the only durable
    cross-tick store available to a supervised scan(). The 60-sample/asset trim,
    the 1h/4h lookbacks, the 240min per-asset cooldown semantics, and the
    bootstrap window are all PRESERVED. Behaviour caveat: ctx.state advances ONLY
    on a clean tick (any exception/timeout rolls it back), so a hard-failing tick
    does not append an OI sample for that interval — the rolling window simply
    skips that slot, exactly as a missed v2 cron tick would. The 1h/4h lookbacks
    are by SAMPLE COUNT (12 / 48 back), not wall-clock, so a few skipped ticks
    stretch the effective lookback slightly — identical to v2's count-based
    lookback under missed crons. FLAGGED.
  - DROPPED v2 plumbing: producer_daemon while-loop, push_signal HTTP POST,
    fcntl producer.lock reentrancy guard, the OTTER_WALLET/STRATEGY_ADDRESS env
    resolution + wallet-hash state dir, the openclaw CLI emit, and the
    "fail-loud if wallet unset" branch — all owned by the Runtime 3.0 supervisor
    now (ctx.wallet, the scaffold's delivery/dedup, single-pass execution). NO
    order-lifecycle code existed in the v2 producer to drop (it never managed
    orders). FLAGGED.
  - Margin: v2 emitted marginUsd = account_value * OTTER_MARGIN_PCT (a FRACTION,
    default 0.25). This port emits `marginPct` = 25 (a PERCENT) at the top level;
    the runtime sizes (marginPct/100)*withdrawable. The defensive "<=1.0 means a
    pasted fraction, ×100" guard is applied (dire/koala pattern). The 25%-of-
    account INTENT is preserved.
  - Universe size: v2 scored EVERY instrument with sufficient history (no top-N
    cut). Preserved — the universe is the full crypto perp board minus XYZ.
  - Emit: v2 main() emitted exactly one signal (the top eligible candidate that
    cleared the spread gate). Preserved: scan() emits <= 1 signal/tick.
"""

import sys
import time

import scoring

_DEFAULT_RECENT_TTL = 240        # 4min — per-tick race-window dedup
_DEFAULT_COOLDOWN_MIN = scoring.ASSET_COOLDOWN_MINUTES   # 240min per-asset cooldown
_DEFAULT_MARGIN_PCT = 25.0       # PERCENT of withdrawable (v2 fraction 0.25 ×100)
_TOP_N_SPREAD_CHECK = 3          # v2 main() spread-checked the top 3 eligible


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll
    back the whole tick (the contract rolls ANY exception back to []). Returns
    None on failure so the existing degrade paths apply (skip asset / neutral)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[otter.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _is_xyz(asset, dex=""):
    """XYZ (HIP-3) equities/commodities — banned (different OI dynamics). v2
    checked dex=='xyz' OR the 'xyz:' name prefix; the live combined
    market_list_instruments response identifies XYZ ONLY by the name prefix, so
    the dex check is kept for forward-compat but the prefix is the live signal."""
    if not asset:
        return False
    if dex and str(dex).lower() == "xyz":
        return True
    return str(asset).lower().startswith("xyz:")


# ═══════════════════════════════════════════════════════════════
# ACCOUNT VALUE (sizing context)
# ═══════════════════════════════════════════════════════════════

def _get_account(ctx):
    """(account_value, open_position_count) from strategy_get_clearinghouse_state.
    READ-GUARDED. Verbatim port of v2 get_account_value: account value summed
    across the main/xyz sub-DEX marginSummaries, non-zero positions counted."""
    if not getattr(ctx, "wallet", None):
        return None, 0
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return None, 0
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    if not isinstance(data, dict):
        return None, 0
    total_value = 0.0
    pos_count = 0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        total_value += scoring._f(ms.get("accountValue", 0))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring._f(pos.get("szi", 0)) != 0:
                pos_count += 1
    return total_value, pos_count


# ═══════════════════════════════════════════════════════════════
# UNIVERSE FETCH (read-guarded market_list_instruments)
# ═══════════════════════════════════════════════════════════════

def fetch_instruments(ctx):
    """Pull market_list_instruments. Returns list of {asset, dex, oi, mark_px,
    oi_usd}. READ-GUARDED. Verbatim port of v2 fetch_instruments: OI + mark read
    from the context-nested fields (Pangolin v1.3 fix), XYZ banned, oi<=0 /
    mark<=0 dropped."""
    raw = _read(ctx, "market_list_instruments", {})
    if not raw:
        return []
    instruments = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return []

    parsed = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = str(inst.get("name", inst.get("coin", ""))).upper()
        dex = str(inst.get("dex", "")).lower()
        if not name:
            continue
        if _is_xyz(name, dex):
            continue
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = scoring._f(ctx_block.get("openInterest", inst.get("openInterest", 0)))
        mark_px = scoring._f(ctx_block.get("markPx", ctx_block.get("midPx",
                             inst.get("markPx", inst.get("midPx", 0)))))
        if oi <= 0 or mark_px <= 0:
            continue
        parsed.append({
            "asset": name,
            "dex": dex,
            "oi": oi,
            "mark_px": mark_px,
            "oi_usd": oi * mark_px,
        })
    return parsed


# ═══════════════════════════════════════════════════════════════
# SM CONCENTRATION (optional scoring bonus, not a hard gate)
# ═══════════════════════════════════════════════════════════════

def fetch_sm_map(ctx, inputs):
    """{asset: {direction, pct, traders}} for the SM concentration bonus.
    READ-GUARDED. Verbatim port of v2 fetch_sm_map (XYZ skipped, pct ×100)."""
    limit = int(inputs.get("smLimit", 100))
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return {}
    sm = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(sm, dict):
        sm = sm.get("markets", sm)
    if isinstance(sm, dict):
        sm = sm.get("markets", [])
    if not isinstance(sm, list):
        return {}
    out = {}
    for m in sm:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if dex == "xyz":
            continue
        if not token:
            continue
        out[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": scoring._f(m.get("pct_of_top_traders_gain", 0)) * 100,
            "traders": int(m.get("trader_count", 0) or 0),
        }
    return out


# ═══════════════════════════════════════════════════════════════
# SPREAD CHECK (required before emit) — per-candidate orderbook read
# ═══════════════════════════════════════════════════════════════

def fetch_spread_bps(ctx, asset):
    """Orderbook spread (bps) for one asset, or None on failure. READ-GUARDED.
    Verbatim port of v2 fetch_spread_bps (best bid/ask -> spread over mid)."""
    r = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": [],
        "include_funding": False,
        "include_order_book": True,
    })
    if not r:
        return None
    data = r.get("data", r) if isinstance(r, dict) else {}
    if not isinstance(data, dict):
        return None
    ob = data.get("order_book") or data.get("orderBook") or {}
    if not isinstance(ob, dict):
        return None
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = scoring._f((bids[0] or {}).get("price") or (bids[0] or {}).get("px"))
    best_ask = scoring._f((asks[0] or {}).get("price") or (asks[0] or {}).get("px"))
    if best_bid <= 0 or best_ask <= 0:
        return None
    mid = (best_bid + best_ask) / 2
    if mid <= 0:
        return None
    return (best_ask - best_bid) / mid * 10000


# ═══════════════════════════════════════════════════════════════
# ctx.state — rolling OI history + cooldowns + per-tick dedup
# ═══════════════════════════════════════════════════════════════

def _load_state(ctx):
    """Return (history, cooldowns, recent) from the last clean tick.
    history    = {asset: [{ts, oi, mark_px}, ...]} (oldest first, <=60/asset)
    cooldowns  = {asset: emittedEpoch}
    recent     = {asset: signaledEpoch} (race-window dedup)"""
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}, {}
    last = ctx.state.last() or {}
    history = last.get("oi_history", {})
    cooldowns = last.get("cooldowns", {})
    recent = last.get("recent", {})
    return (dict(history) if isinstance(history, dict) else {},
            dict(cooldowns) if isinstance(cooldowns, dict) else {},
            dict(recent) if isinstance(recent, dict) else {})


def _update_history(history, instruments, now):
    """Append the current sample per instrument; trim to HISTORY_MAX_SAMPLES.
    Verbatim port of v2 update_history (delisted assets keep history but stop
    accumulating). Returns the mutated history dict."""
    for inst in instruments:
        asset = inst["asset"]
        sample = {"ts": now, "oi": inst["oi"], "mark_px": inst["mark_px"]}
        if asset not in history:
            history[asset] = []
        history[asset].append(sample)
        if len(history[asset]) > scoring.HISTORY_MAX_SAMPLES:
            history[asset] = history[asset][-scoring.HISTORY_MAX_SAMPLES:]
    return history


def _is_cooled_down(cooldowns, asset, cooldown_minutes, now):
    """v2 is_asset_cooled_down: True if last emit is within the cooldown window."""
    last_emit = cooldowns.get(asset)
    if last_emit is None:
        return False
    return ((now - last_emit) / 60) < cooldown_minutes


# ═══════════════════════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════════════════════

def scan(inputs, ctx):
    now = time.time()
    hour = time.gmtime(now).tm_hour
    min_score = float(inputs.get("minScore", scoring.MIN_SCORE))
    max_spread = float(inputs.get("maxSpreadBps", scoring.MAX_SPREAD_BPS))
    cooldown_min = float(inputs.get("assetCooldownMinutes", _DEFAULT_COOLDOWN_MIN))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    top_n = int(inputs.get("spreadCheckTopN", _TOP_N_SPREAD_CHECK))

    # marginPct: PERCENT in (0,100]. Defensive fraction->percent guard
    # (dire/koala): a pasted <=1.0 is a fraction; ×100.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        margin_pct *= 100.0

    history, cooldowns, recent = _load_state(ctx)
    # prune the race-window dedup map (TTL)
    recent = {k: v for k, v in recent.items() if (now - v) < ttl}

    def _persist(result):
        if ctx.state is None:
            return
        rec = {"oi_history": history, "cooldowns": cooldowns,
               "recent": recent, "result": result}
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[otter.scan] WARNING: state append failed; rolling OI history "
                  f"and cooldowns will not advance this tick: {exc!r}", file=sys.stderr)

    # 1. Account value for sizing context (sizing itself is marginPct intent).
    account_value, pos_count = _get_account(ctx)
    if account_value is None or account_value <= 0:
        print("[otter.scan] cannot read account value — skip tick", file=sys.stderr)
        # do NOT advance history off a failed account read (matches v2 early-return)
        _persist({"ts": now, "emitted": False, "gate": "no_account"})
        return []

    # 2. Fetch the crypto-perp universe (XYZ banned).
    instruments = fetch_instruments(ctx)
    if not instruments:
        print("[otter.scan] market_list_instruments empty/failed — no signal", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_universe"})
        return []

    # 3. Append this tick's OI sample to the rolling history (then trim).
    history = _update_history(history, instruments, now)

    # 4. SM concentration map (optional bonus).
    sm_map = fetch_sm_map(ctx, inputs)

    # 5. Score every asset with sufficient history through the verbatim scorer.
    candidates = []
    bootstrapping = 0
    for inst in instruments:
        samples = history.get(inst["asset"], [])
        sig = scoring.evaluate_oi_velocity(inst, samples, sm_map.get(inst["asset"]),
                                           hour, inputs)
        if sig == "BOOTSTRAP":
            bootstrapping += 1
            continue
        if sig:
            candidates.append(sig)

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # 6. min-score + per-asset cooldown filter.
    eligible = [
        c for c in candidates
        if c["score"] >= min_score
        and not _is_cooled_down(cooldowns, c["asset"], cooldown_min, now)
    ]

    total = len(instruments)
    if not eligible and bootstrapping > 0:
        avg = round(sum(len(s) for s in history.values()) / max(len(history), 1), 1)
        print(f"[otter.scan] WAITING bootstrapping_history "
              f"({bootstrapping}/{total} assets need more samples, avg {avg})",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "bootstrapping",
                  "bootstrapping": bootstrapping, "candidates": len(candidates),
                  "samples_avg": avg})
        return []

    if not eligible:
        best = candidates[0] if candidates else None
        note = ("no candidate passed score+cooldown" if not best else
                f"best {best['asset']} {best['direction']} score={best['score']} (need >= {min_score:.0f})")
        print(f"[otter.scan] WAITING — {note} (scanned {total})", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_eligible",
                  "candidates": len(candidates)})
        return []

    # 7. Spread gate on the top-N eligible (per-candidate orderbook read).
    emitted = None
    for c in eligible[:top_n]:
        # defense-in-depth dedup: skip if pushed within the race window
        au = c["asset"].upper()
        if recent.get(au) is not None and (now - recent[au]) < ttl:
            c["reasons"].append("DEDUP_SKIP")
            continue
        spread_bps = fetch_spread_bps(ctx, c["asset"])
        if not scoring.apply_spread_bonus(c, spread_bps, max_spread):
            continue
        if c["score"] < min_score:    # spread bonus could not lift it back over floor
            continue
        emitted = c
        break

    if not emitted:
        print(f"[otter.scan] WAITING — all top {top_n} eligible failed spread/dedup gate",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "spread",
                  "candidates": len(candidates), "eligible": len(eligible)})
        return []

    # 8. Emit the single best survivor. Score-scaled leverage; marginPct intent.
    leverage = scoring.get_leverage_for_score(emitted["score"],
                                              inputs.get("leverageTiers"))
    au = emitted["asset"].upper()
    cooldowns[au] = now
    recent[au] = now

    result = {"ts": now, "emitted": True, "gate": "pass", "asset": emitted["asset"],
              "direction": emitted["direction"], "score": emitted["score"],
              "leverage": leverage, "marginPct": margin_pct,
              "open_positions": pos_count, "account_value": round(account_value, 2),
              "reasons": emitted["reasons"]}
    print(f"[otter.scan] EMIT {emitted['asset']} {emitted['direction']} "
          f"score={emitted['score']} {leverage}x margin={margin_pct:.1f}% "
          f"oiD1h={emitted['oi_delta_1h_pct']:+.1f}% pxD1h={emitted['price_delta_1h_pct']:+.2f}% "
          f"| {' | '.join(emitted['reasons'])}", file=sys.stderr)
    _persist(result)

    oi_d_4h = emitted["oi_delta_4h_pct"]
    px_d_4h = emitted["price_delta_4h_pct"]
    return [{
        "asset": emitted["asset"],
        "direction": emitted["direction"],
        "marginPct": margin_pct,                  # SIZING INTENT — PERCENT (0,100]; runtime sizes USD
        "leverage": leverage,                     # score-tiered (5/7/10); runtime clamps to venue max
        "data": {
            "score": emitted["score"],
            "leverage": leverage,
            "direction": emitted["direction"],
            "oiDelta1hPct": round(emitted["oi_delta_1h_pct"], 3),
            "priceDelta1hPct": round(emitted["price_delta_1h_pct"], 3),
            "oiUsd": round(emitted["oi_usd"], 2),
            "oiDelta4hPct": round(oi_d_4h, 3) if oi_d_4h is not None else 0,
            "priceDelta4hPct": round(px_d_4h, 3) if px_d_4h is not None else 0,
            "spreadBps": round(emitted["spread_bps"], 2) if emitted["spread_bps"] is not None else 0,
            "smAligned": bool(emitted["sm_aligned"]),
            "smPctOfTopTraders": round(emitted["sm_pct"], 2),
            "markPx": round(emitted["mark_px"], 6),
            "samplesInHistory": int(emitted["samples"]),
            "reasons": " | ".join(emitted.get("reasons", [])),
        },
    }]
