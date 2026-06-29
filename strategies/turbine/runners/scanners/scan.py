"""TURBINE RUNNERS — supervised scanner (Runtime 3.0 port of the v2 Turbine RUNNERS wallet).

The runners wallet receives the SAME funding-fade volume-rotation alpha as the volume
wallet (same scoring, same universe BTC/ETH/SOL/HYPE + xyz:BRENTOIL/GOLD/SP500, same
direction selection). The ONLY difference from the volume instance is the DSL exit
profile (patient: 240-min hard_timeout + Phase 2 ratchet, see runtime.yaml) and the
per-slot sizing (fewer, larger slots). Turbine is a VOLUME / MARKET-MAKING engine, NOT a
directional strategy — the runners wallet's value is the ~5% of entries that catch a
real directional move and ratchet to a much larger realized PnL instead of being
force-cut at 10 minutes. Per tick it:

  - reads account value + open positions + resting orders (read-guarded, dual-DEX),
  - computes held_keys (positions + non-reduce-only resting orders = slot occupiers),
  - applies auto-downsize: effective_slots = min(config max, account_value / margin_usd),
  - fills the free slots by rotation (pick_rotation_asset), gating each pick on the
    spread book (parse_asset_data) and choosing direction by funding fade,
  - emits one volume-rotation signal per free slot.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`; the
runtime sizes the dollars, owns cooldowns/risk gates/slot accounting, and runs the
patient DSL exit. No daemon, no push_signal, no create_position, no cancel_order.

FIDELITY NOTES vs the v2 producer (turbine-producer.py / turbine_config.py v3.2.2):
  - SINGLE WALLET PER SCANNER. The v2 producer was ONE daemon managing BOTH wallets in
    one main() tick. Runtime 3.0 binds one runtime per wallet, so the two wallets are
    split into two instances (volume/ + runners/), each its own scan() bound to its own
    ctx.wallet. This instance is the RUNNERS wallet. The v2 producer used a SEPARATE
    per-wallet rotation index so the two wallets desync naturally (cleaner volume
    distribution across the universe); that desync is preserved because each instance
    keeps its own ctx.state rotIdx and seeds its rng independently.
  - STALE-ORDER SWEEP DROPPED (MUTATION). The v2 producer called
    sweep_stale_resting_orders() -> cancel_order (a MUTATION) before computing held_keys.
    cancel_order raises PermissionError under the read-only scan() boundary, so it is
    dropped — the runtime's reconciliation + execution_timeout_seconds owns order
    lifecycle. FLAGGED: held_keys may transiently over-count an orphaned resting order
    until the runtime cancels it (conservatively under-emits; never over-emits).
  - FIXED-USD MARGIN -> marginPct. The v2 producer sized a FIXED USD margin per slot
    ($1300 runners) regardless of account value. Runtime 3.0 sizes marginPct/100 *
    withdrawable, so this port emits `marginPct` (PERCENT, default 50% = $1300/$2600 v2
    budget). The auto-downsize SLOT logic is preserved verbatim using `marginUsdEquiv`
    (the dollar margin the percent represents) for the affordable count. marginUsd is
    NOT emitted (spec 4.4: marginPct percent, top-level).
  - PER-WALLET STATE FILES -> ctx.state (rotation-index / prev-held / last-closed).
    Runners had no cycle-stats file (that was volume-only); the runners cycleMin is just
    the static default echoed onto the payload for telemetry parity.
  - EMIT-ALL. Preserved — one signal per free slot; the runtime owns the slot ceiling.
"""

import random
import sys
import time

import scoring


# v2 defaults (turbine-config.json / turbine-producer.py v3.2.2). Same universe as the
# volume wallet; xyz:SP500 is the live HL symbol (v2 hardcoded the stale "xyz:SPX" which
# does NOT exist on HL — corrected here, see report).
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
    resting order is a slot occupier, v2.0.4 ghost-trade fix)."""
    account_value = 0.0
    held_keys = set()
    n_positions = 0
    n_resting = 0

    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[turbine.runners.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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

    try:
        oo = ctx.senpi_mcp.call_tool("strategy_get_open_orders",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — degrade: count only positions as held
        print(f"[turbine.runners.scan] open-orders read failed (count positions only): {exc!r}",
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
    """Auto-downsize: min(config max, account_value / margin_per_slot floored).
    Verbatim from v2 effective_slots — graceful slot reduction as the wallet bleeds."""
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
    cutoff = now - (cooldown_seconds * 4)
    return {k: v for k, v in last_closed.items()
            if isinstance(v, dict) and scoring._f(v.get("ts", 0)) >= cutoff}


def scan(inputs, ctx):
    now = time.time()
    xyz_pool = list(inputs.get("volumeXyz", _VOLUME_XYZ_DEFAULT))
    main_pool = list(inputs.get("volumeMain", _VOLUME_MAIN_DEFAULT))
    max_slots = int(inputs.get("slots", 2))
    margin_pct = float(inputs.get("marginPct", 50.0))      # PERCENT in (0,100]
    margin_usd_equiv = float(inputs.get("marginUsdEquiv", 1300.0))  # v2 fixed $/slot (downsize math)
    leverage = float(inputs.get("leverage", 5))
    xyz_weight = float(inputs.get("xyzWeight", 0.80))
    spread_main_bps = float(inputs.get("spreadMainBps", 3))
    spread_xyz_bps = float(inputs.get("spreadXyzBps", 10))
    cooldown = float(inputs.get("postCloseCooldownSeconds", _POST_CLOSE_COOLDOWN_SEC))
    cycle_min = float(inputs.get("cycleMin", _DEFAULT_CYCLE_MIN))
    seed = inputs.get("rotationSeed")  # optional; deterministic rotation for tests

    # Defensive fraction->percent guard (spec 4.4 / dire/koala): a pasted FRACTION
    # (e.g. 0.50) means 50%, so x100. A legitimate percent is always > 1.0.
    if 0 < margin_pct <= 1.0:
        margin_pct *= 100.0

    account_value, held_keys, n_positions, n_resting = _get_account_and_held(ctx)
    if account_value <= 0:
        print("[turbine.runners.scan] WAITING — cannot read account value; skip tick",
              file=sys.stderr)
        return []

    rot_idx, prev_held, last_closed = _load_state(ctx)

    closed_this_tick = sorted(prev_held - held_keys)
    for k in closed_this_tick:
        last_closed[k] = {"ts": now}
    last_closed = _prune_last_closed(last_closed, now, cooldown)

    eff_slots = _effective_slots(account_value, max_slots, margin_usd_equiv)
    free_slots = max(0, eff_slots - len(held_keys))

    rng = random.Random(seed) if seed is not None else random.Random()

    emitted = []
    out = []
    working_held = set(held_keys)
    for _slot in range(free_slots):
        asset, rot_idx = scoring.pick_rotation_asset(
            rot_idx, xyz_weight, working_held, last_closed, now,
            xyz_pool, main_pool, rng, cooldown_seconds=cooldown,
        )
        if asset is None:
            break
        try:
            resp = ctx.senpi_mcp.call_tool("market_get_asset_data", {
                "asset": asset,
                "candle_intervals": [],
                "include_funding": True,
                "include_order_book": True,
                "dex": _dex_for(asset),
            })
        except Exception as exc:  # noqa: BLE001 — skip this asset, keep filling other slots
            print(f"[turbine.runners.scan] market_get_asset_data({asset}) read failed: {exc!r}",
                  file=sys.stderr)
            continue
        ad = scoring.parse_asset_data(resp)
        if ad is None:
            continue
        max_spread = spread_xyz_bps if scoring.is_xyz(asset) else spread_main_bps
        if ad["spread_bps"] > max_spread:
            continue
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

    if out:
        print(f"[turbine.runners.scan] EMIT {len(out)} vol-rotation | "
              f"acct={account_value:.0f} slots eff={eff_slots} held={len(held_keys)} "
              f"free={free_slots} | {[e['asset'] + ':' + e['direction'] for e in emitted]}",
              file=sys.stderr)
    else:
        print(f"[turbine.runners.scan] WAITING — no free slot fills | acct={account_value:.0f} "
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
            print(f"[turbine.runners.scan] WARNING: state append failed; rotation/cooldown "
                  f"state may reset next tick: {exc!r}", file=sys.stderr)
    return out
