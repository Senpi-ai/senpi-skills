"""TURBINE VOLUME — supervised scanner (Runtime 3.0 port of the v2 Turbine VOLUME wallet).

Turbine is a VOLUME / MARKET-MAKING engine, NOT a directional strategy. This scanner
churns notional volume for builder-fee recycling by rotating funding-fade entries across
a tight, deep liquid universe (BTC/ETH/SOL/HYPE + xyz:BRENTOIL/GOLD/SP500). There is no
"score" — every gated candidate is emitted; the VOLUME wallet's DSL force-cuts every
position on a 10-minute clock (pure rotation cadence). Per tick it:

  - reads account value + open positions + resting orders (read-guarded, dual-DEX),
  - computes held_keys (positions + non-reduce-only resting orders = slot occupiers),
  - applies auto-downsize: effective_slots = min(config max, account_value / margin_usd),
  - fills the free slots by rotation (pick_rotation_asset), gating each pick on the
    spread book (parse_asset_data) and choosing direction by funding fade,
  - emits one volume-rotation signal per free slot.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`; the
runtime sizes the dollars, owns cooldowns/risk gates/slot accounting, and runs the DSL
exit. No daemon, no push_signal, no create_position, no cancel_order.

FIDELITY NOTES vs the v2 producer (turbine-producer.py / turbine_config.py v3.2.2):
  - SINGLE WALLET PER SCANNER. The v2 producer was ONE daemon managing BOTH wallets
    (volume + runners) in one main() tick. Runtime 3.0 binds one runtime per wallet,
    so the two wallets are split into two instances (volume/ + runners/), each its own
    scan() bound to its own ctx.wallet. This instance is the VOLUME wallet. The
    runners wallet is the sibling runners/ instance — same rotation alpha, patient DSL.
  - STALE-ORDER SWEEP DROPPED (MUTATION). The v2 producer called
    sweep_stale_resting_orders() -> cancel_order (a MUTATION) before computing held_keys
    to clear orphaned ALOs left by previous runtime swaps. cancel_order raises
    PermissionError under the read-only scan() boundary, so it is dropped — the runtime's
    own reconciliation + execution_timeout_seconds owns order lifecycle now. FLAGGED:
    held_keys may transiently over-count an orphaned resting order until the runtime
    cancels it, conservatively under-emitting (safe direction: never over-emits).
  - FIXED-USD MARGIN -> marginPct. The v2 producer sized a FIXED USD margin per slot
    ($700 volume) regardless of account value (auto-downsize only reduced the slot
    COUNT, never the per-slot dollars). Runtime 3.0 sizes marginPct/100 * withdrawable,
    so this port emits `marginPct` (PERCENT, default ~13% = $700/$5400 v2 budget). The
    fixed-USD per-slot intent is approximated by a percent of equity; operators tune
    `marginPct` to hit the same dollar notional. marginUsd is NOT emitted (spec 4.4:
    marginPct percent, top-level). The auto-downsize SLOT logic is preserved verbatim
    using `marginUsdEquiv` (the dollar margin the percent represents) for the affordable
    count, so slot accounting matches v2.
  - PER-WALLET STATE FILES -> ctx.state. The v2 producer kept rotation-index.json,
    prev-held.json, last-closed.json, cycle-stats.json in SKILL_DIR/state/<hash>/. All
    move into ctx.state: {rotIdx, prevHeld[], lastClosed{coin:ts}, result{}}. cycle-stats
    fill-rate fallback -> see cycle-length note below.
  - CYCLE-LENGTH FALLBACK DROPPED (informational only). The v2 producer adjusted the
    advertised `cycleMin` (10 -> 12 min) from a rolling maker fill-rate window. That value
    was purely informational telemetry on the signal payload (the runtime's hard_timeout,
    not the producer, controls the actual rotation cadence). The runtime can't observe per-
    entry maker fill rate from scan(), so cycleMin is emitted as the static default. The
    real 10-min rotation cadence lives in runtime.yaml exit.dsl_preset.hard_timeout. FLAGGED.
  - EMIT-ALL. The v2 producer filled all free slots opportunistically; preserved — scan()
    emits one signal per free slot (the runtime owns the slot ceiling either way).
"""

import random
import sys
import time

import scoring


# v2 defaults (turbine-config.json / turbine-producer.py v3.2.2). The volume universe
# is the tight, deep, tight-spread set; xyz:SP500 is the live HL symbol (v2 hardcoded
# the stale "xyz:SPX" which does NOT exist on HL — corrected here, see report).
_VOLUME_XYZ_DEFAULT = ["xyz:BRENTOIL", "xyz:GOLD", "xyz:SP500"]
_VOLUME_MAIN_DEFAULT = ["BTC", "ETH", "SOL", "HYPE"]
_POST_CLOSE_COOLDOWN_SEC = 90        # v2 pick_rotation_asset post-close cooldown
_DEFAULT_CYCLE_MIN = 10              # v2 cycle.volumeDefaultMin (informational on payload)


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account_and_held(ctx):
    """(account_value, held_keys:set, n_positions, n_resting) — READ-GUARDED.

    Dual-DEX equity collapse via max() across main/xyz (two views of ONE cross-margined
    wallet; summing double-counts the shared free balance -> 2x sizing). held_keys =
    open positions + non-reduce-only, non-trigger resting orders (every non-reduce-only
    resting order is a slot occupier, v2.0.4 ghost-trade fix). Ported from
    cfg.get_open_positions + cfg.get_resting_orders + cfg.get_account_value, merged into
    one clearinghouse read + one open-orders read."""
    account_value = 0.0
    held_keys = set()
    n_positions = 0
    n_resting = 0

    # ── clearinghouse: account value + open positions ──
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[turbine.volume.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, set(), 0, 0
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, set(), 0, 0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            if scoring._f(pos.get("szi", 0)) == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held_keys.add(scoring.normalize_coin_key(coin))
                n_positions += 1

    # ── open orders: resting non-reduce-only, non-trigger = slot occupiers ──
    try:
        oo = ctx.senpi_mcp.call_tool("strategy_get_open_orders",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — degrade: count only positions as held
        print(f"[turbine.volume.scan] open-orders read failed (count positions only): {exc!r}",
              file=sys.stderr)
        oo = None
    orders = []
    if oo:
        od = oo.get("data", oo) if isinstance(oo, dict) else oo
        if isinstance(od, dict):
            od = od.get("orders", od.get("openOrders", []))
        if isinstance(od, list):
            orders = od
    for o in orders:
        if not isinstance(o, dict):
            continue
        if o.get("reduceOnly") or o.get("isTrigger"):
            continue
        coin = o.get("coin") or o.get("asset", "")
        if not coin:
            continue
        held_keys.add(scoring.normalize_coin_key(coin))
        n_resting += 1

    return account_value, held_keys, n_positions, n_resting


def _effective_slots(account_value, max_slots, margin_usd_equiv):
    """Auto-downsize: how many slots can the wallet actually open right now?
    min(config max, account_value / margin_per_slot floored). Verbatim from v2
    effective_slots. Handles cost-of-volume bleed gracefully (no insufficient_margin
    rejections). `margin_usd_equiv` is the dollar margin the emitted marginPct
    represents, so this affordable count matches the v2 fixed-USD logic."""
    if account_value <= 0 or margin_usd_equiv <= 0:
        return 0
    affordable = int(account_value / margin_usd_equiv)
    return max(0, min(max_slots, affordable))


# ── ctx.state: rotation index + prev-held (close detection) + post-close cooldown ──

def _load_state(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return 0, set(), {}
    last = ctx.state.last() or {}
    rot_idx = int(last.get("rotIdx", 0)) if isinstance(last.get("rotIdx"), (int, float)) else 0
    prev_held = set(last.get("prevHeld", [])) if isinstance(last.get("prevHeld"), list) else set()
    last_closed = dict(last.get("lastClosed", {})) if isinstance(last.get("lastClosed"), dict) else {}
    return rot_idx, prev_held, last_closed


def _prune_last_closed(last_closed, now, cooldown_seconds):
    """Drop close timestamps older than 4x the cooldown (bounded growth)."""
    cutoff = now - (cooldown_seconds * 4)
    return {k: v for k, v in last_closed.items()
            if isinstance(v, dict) and scoring._f(v.get("ts", 0)) >= cutoff}


def scan(inputs, ctx):
    now = time.time()
    xyz_pool = list(inputs.get("volumeXyz", _VOLUME_XYZ_DEFAULT))
    main_pool = list(inputs.get("volumeMain", _VOLUME_MAIN_DEFAULT))
    max_slots = int(inputs.get("slots", 7))
    margin_pct = float(inputs.get("marginPct", 13.0))      # PERCENT in (0,100]
    margin_usd_equiv = float(inputs.get("marginUsdEquiv", 700.0))  # v2 fixed $/slot (downsize math)
    leverage = float(inputs.get("leverage", 5))
    xyz_weight = float(inputs.get("xyzWeight", 0.80))
    spread_main_bps = float(inputs.get("spreadMainBps", 3))
    spread_xyz_bps = float(inputs.get("spreadXyzBps", 10))
    cooldown = float(inputs.get("postCloseCooldownSeconds", _POST_CLOSE_COOLDOWN_SEC))
    cycle_min = float(inputs.get("cycleMin", _DEFAULT_CYCLE_MIN))
    seed = inputs.get("rotationSeed")  # optional; deterministic rotation for tests

    # Defensive fraction->percent guard (spec 4.4 / dire/koala pattern): a pasted
    # FRACTION (e.g. 0.13) means 13%, so x100. A legitimate percent is always > 1.0.
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0

    account_value, held_keys, n_positions, n_resting = _get_account_and_held(ctx)
    if account_value <= 0:
        print("[turbine.volume.scan] WAITING — cannot read account value; skip tick",
              file=sys.stderr)
        return []

    rot_idx, prev_held, last_closed = _load_state(ctx)

    # Close detection -> post-close cooldown (v2 prev-held diff). Names that were held
    # last tick but aren't now just closed; stamp them so rotation skips them briefly.
    closed_this_tick = sorted(prev_held - held_keys)
    for k in closed_this_tick:
        last_closed[k] = {"ts": now}
    last_closed = _prune_last_closed(last_closed, now, cooldown)

    # Auto-downsize -> free slots.
    eff_slots = _effective_slots(account_value, max_slots, margin_usd_equiv)
    free_slots = max(0, eff_slots - len(held_keys))

    rng = random.Random(seed) if seed is not None else random.Random()

    # ── fill free slots by rotation (the volume engine) ──
    emitted = []
    out = []
    # working held set so two picks in one tick don't collide on the same coin
    working_held = set(held_keys)
    for _slot in range(free_slots):
        asset, rot_idx = scoring.pick_rotation_asset(
            rot_idx, xyz_weight, working_held, last_closed, now,
            xyz_pool, main_pool, rng, cooldown_seconds=cooldown,
        )
        if asset is None:
            break  # whole universe held / cooled — nothing to emit
        # spread gate (the only entry gate) — read the order book for this asset
        try:
            resp = ctx.senpi_mcp.call_tool("market_get_asset_data", {
                "asset": asset,
                "candle_intervals": [],
                "include_funding": True,
                "include_order_book": True,
                "dex": _dex_for(asset),
            })
        except Exception as exc:  # noqa: BLE001 — skip this asset, keep filling other slots
            print(f"[turbine.volume.scan] market_get_asset_data({asset}) read failed: {exc!r}",
                  file=sys.stderr)
            continue
        ad = scoring.parse_asset_data(resp)
        if ad is None:
            continue
        max_spread = spread_xyz_bps if scoring.is_xyz(asset) else spread_main_bps
        if ad["spread_bps"] > max_spread:
            continue  # too wide — would not net out on cost
        direction, thesis = scoring.choose_direction(ad["funding_regime"], rng)

        slot_index = len(out)
        out.append({
            "asset": asset,
            "direction": direction,
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes dollars
            "leverage": leverage,
            "data": {
                "thesis": thesis,
                "leverage": leverage,
                "marginPct": margin_pct,
                "fundingRegime": ad["funding_regime"],
                "fundingAnnualizedPct": ad["funding_annualized_pct"],
                "spreadBps": ad["spread_bps"],
                "slotIndex": slot_index,
                "isXyz": scoring.is_xyz(asset),
                "cycleMin": cycle_min,
            },
        })
        emitted.append({"asset": asset, "direction": direction, "thesis": thesis})
        working_held.add(scoring.normalize_coin_key(asset))

    # ── observability: one-line stderr + per-tick result into ctx.state ──
    if out:
        print(f"[turbine.volume.scan] EMIT {len(out)} vol-rotation | "
              f"acct={account_value:.0f} slots eff={eff_slots} held={len(held_keys)} "
              f"free={free_slots} | {[e['asset'] + ':' + e['direction'] for e in emitted]}",
              file=sys.stderr)
    else:
        print(f"[turbine.volume.scan] WAITING — no free slot fills | acct={account_value:.0f} "
              f"slots eff={eff_slots} held={len(held_keys)} free={free_slots}",
              file=sys.stderr)

    result = {
        "ts": now, "acct": round(account_value, 2),
        "slotsEff": eff_slots, "held": len(held_keys), "free": free_slots,
        "positions": n_positions, "resting": n_resting,
        "emitted": emitted, "closed": closed_this_tick,
    }
    if ctx.state is not None:
        try:
            ctx.state.append({
                "rotIdx": rot_idx,
                "prevHeld": sorted(held_keys),
                "lastClosed": last_closed,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[turbine.volume.scan] WARNING: state append failed; rotation/cooldown "
                  f"state may reset next tick: {exc!r}", file=sys.stderr)
    return out
