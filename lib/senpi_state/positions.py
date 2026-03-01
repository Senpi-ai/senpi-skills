"""
Deterministic position lifecycle — enter and close with full bookkeeping.

These functions handle the complete trade lifecycle atomically:
  1. Execute the trade via mcporter
  2. Create/deactivate DSL state
  3. Update the skill's state file
  4. Journal the event

LLM agents call thin wrapper scripts (e.g. tiger-enter.py) that delegate
here.  The agent never writes JSON state files directly.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from senpi_state.mcporter import mcporter_call


def enter_position(
    wallet: str,
    coin: str,
    direction: str,
    leverage: int,
    margin: float,
    pattern: str,
    score: float,
    load_state: Callable,
    save_state: Callable,
    create_dsl: Callable,
    save_dsl: Callable,
    journal=None,
    skill: str = "",
    instance_key: str = "",
    max_slots: int = 3,
    active_positions_key: str = "activePositions",
    dry_run: bool = False,
) -> dict:
    """Full entry lifecycle: guard -> create_position -> DSL -> state -> journal.

    Args:
        wallet: Strategy wallet address.
        coin: Asset symbol (e.g. "SOL").
        direction: "LONG" or "SHORT".
        leverage: Leverage multiplier.
        margin: Margin amount in USD.
        pattern: Signal pattern name (e.g. "MOMENTUM_BREAKOUT").
        score: Confluence score.
        load_state: Callable that returns the current state dict.
        save_state: Callable(state) that persists state atomically.
        create_dsl: Callable(asset, direction, entry_price, size, margin,
                    leverage, pattern) that returns a DSL state dict.
        save_dsl: Callable(asset, dsl_state) that persists DSL atomically.
        journal: Optional TradeJournal instance.
        skill: Skill name for journaling (e.g. "tiger").
        instance_key: Instance ID for journaling.
        max_slots: Maximum concurrent positions.
        active_positions_key: Key name in state dict for position map.
        dry_run: If True, skip the actual MCP call.

    Returns:
        Result dict with success/error status and position details.
    """
    state = load_state()

    if state.get("halted") or state.get("safety", {}).get("halted"):
        return {"success": False, "error": "HALTED", "reason": "Trading is halted"}

    active = state.get(active_positions_key, {})
    if coin in active:
        return {"success": False, "error": "DUPLICATE",
                "reason": f"{coin} already in {active_positions_key}"}

    available = max(0, max_slots - len(active))
    if available <= 0:
        return {"success": False, "error": "NO_SLOTS",
                "reason": f"All {max_slots} slots occupied"}

    order = {
        "coin": coin,
        "direction": direction.upper(),
        "leverage": leverage,
        "marginAmount": margin,
        "orderType": "MARKET",
    }

    entry_price = 0
    size = 0
    order_id = ""

    if not dry_run:
        try:
            result = mcporter_call(
                "create_position",
                strategyWalletAddress=wallet,
                orders=[order],
                reason=f"{pattern} score={score:.2f}",
            )
            if isinstance(result, dict):
                statuses = result.get("statuses", [result])
                if statuses:
                    st = statuses[0] if isinstance(statuses, list) else statuses
                    filled = st.get("filled", st)
                    if isinstance(filled, dict):
                        entry_price = float(filled.get("avgPx", filled.get("px", 0)))
                        size = abs(float(filled.get("totalSz", filled.get("sz", 0))))
                        order_id = str(filled.get("oid", ""))
        except Exception as e:
            return {"success": False, "error": "MCP_FAILED", "reason": str(e)}

        if not entry_price:
            try:
                prices = mcporter_call("market_get_prices", assets=[coin])
                if isinstance(prices, dict):
                    p = prices.get("prices", prices)
                    entry_price = float(p.get(coin, 0))
            except Exception:
                pass

        if not size and entry_price > 0:
            size = round(margin * leverage / entry_price, 4)
    else:
        entry_price = 1.0
        size = margin * leverage

    dsl_state = create_dsl(coin, direction.upper(), entry_price, size,
                           margin, leverage, pattern)
    save_dsl(coin, dsl_state)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    active[coin] = {
        "direction": direction.upper(),
        "leverage": leverage,
        "margin": round(margin, 2),
        "entryPrice": entry_price,
        "size": size,
        "pattern": pattern,
        "enteredAt": now_iso,
        "score": score,
        "high_water_roe": 0,
        "stagnant_checks": 0,
        "prev_high_water": 0,
    }
    if order_id:
        active[coin]["orderId"] = order_id

    state[active_positions_key] = active
    state["availableSlots"] = max(0, max_slots - len(active))
    save_state(state)

    if journal:
        journal.record_entry(
            skill=skill, instance_key=instance_key, asset=coin,
            direction=direction.upper(), leverage=leverage, margin=margin,
            entry_price=entry_price, size=size, pattern=pattern,
            score=score, order_id=order_id, source=f"{skill}-enter",
        )
        journal.record_dsl(
            skill=skill, instance_key=instance_key, asset=coin,
            event_type="DSL_CREATED", source=f"{skill}-enter",
            details={"pattern": pattern, "phase": 1},
        )

    return {
        "success": True,
        "action": "POSITION_OPENED",
        "coin": coin,
        "direction": direction.upper(),
        "leverage": leverage,
        "margin": round(margin, 2),
        "entryPrice": entry_price,
        "size": size,
        "pattern": pattern,
        "score": score,
        "orderId": order_id,
        "slotsRemaining": state["availableSlots"],
    }


def close_position_safe(
    wallet: str,
    coin: str,
    reason: str,
    load_state: Callable,
    save_state: Callable,
    load_dsl: Callable,
    save_dsl: Callable,
    journal=None,
    skill: str = "",
    instance_key: str = "",
    max_slots: int = 3,
    active_positions_key: str = "activePositions",
    log_trade_fn: Optional[Callable] = None,
    dry_run: bool = False,
) -> dict:
    """Full close lifecycle: close -> DSL deactivate -> state cleanup -> journal.

    Idempotent — safe to call even if position is already closed on-chain
    (handles CLOSE_NO_POSITION gracefully).

    Args:
        wallet: Strategy wallet address.
        coin: Asset symbol.
        reason: Human-readable close reason.
        load_state: Callable returning current state dict.
        save_state: Callable(state) to persist state.
        load_dsl: Callable(asset) returning DSL state dict or None.
        save_dsl: Callable(asset, dsl_state) to persist DSL.
        journal: Optional TradeJournal instance.
        skill: Skill name for journaling.
        instance_key: Instance ID for journaling.
        max_slots: Maximum concurrent positions.
        active_positions_key: Key in state dict for position map.
        log_trade_fn: Optional callable(trade_dict) for skill-specific trade logging.
        dry_run: If True, skip the actual MCP close call.

    Returns:
        Result dict with success/error status and close details.
    """
    state = load_state()
    active = state.get(active_positions_key, {})
    pos_data = active.get(coin, {})

    close_result = None
    already_closed = False

    if not dry_run:
        try:
            close_result = mcporter_call(
                "close_position",
                strategyWalletAddress=wallet,
                coin=coin,
                reason=reason,
            )
        except RuntimeError as e:
            err_str = str(e).lower()
            if "no_position" in err_str or "not found" in err_str:
                already_closed = True
            else:
                return {"success": False, "error": "MCP_FAILED", "reason": str(e)}

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    dsl_state = load_dsl(coin)
    exit_price = 0
    if dsl_state:
        dsl_state["active"] = False
        dsl_state["closedAt"] = now_iso
        dsl_state["closeReason"] = reason
        if dsl_state.get("lastPrice"):
            exit_price = dsl_state["lastPrice"]
        save_dsl(coin, dsl_state)

    if coin in active:
        del active[coin]
        state[active_positions_key] = active
        state["availableSlots"] = max(0, max_slots - len(active))
        save_state(state)

    entry_price = pos_data.get("entryPrice", dsl_state.get("entryPrice", 0) if dsl_state else 0)
    direction = pos_data.get("direction", dsl_state.get("direction", "") if dsl_state else "")
    pnl = 0
    if entry_price and exit_price and direction:
        if direction == "LONG":
            pnl = (exit_price - entry_price) / entry_price * pos_data.get("leverage", 1) * pos_data.get("margin", 0)
        elif direction == "SHORT":
            pnl = (entry_price - exit_price) / entry_price * pos_data.get("leverage", 1) * pos_data.get("margin", 0)

    if log_trade_fn and pos_data:
        try:
            log_trade_fn({
                "asset": coin,
                "direction": direction,
                "pattern": pos_data.get("pattern", ""),
                "entryPrice": entry_price,
                "exitPrice": exit_price,
                "leverage": pos_data.get("leverage", 0),
                "margin": pos_data.get("margin", 0),
                "pnlUsd": round(pnl, 2),
                "exitReason": reason,
                "enteredAt": pos_data.get("enteredAt", ""),
                "closedAt": now_iso,
            })
        except Exception:
            pass

    if journal:
        journal.record_exit(
            skill=skill, instance_key=instance_key, asset=coin,
            direction=direction, reason=reason, pnl=round(pnl, 2),
            entry_price=entry_price, exit_price=exit_price,
            source=f"{skill}-close",
        )
        if dsl_state:
            journal.record_dsl(
                skill=skill, instance_key=instance_key, asset=coin,
                event_type="DSL_DEACTIVATED", source=f"{skill}-close",
                details={"reason": reason},
            )

    return {
        "success": True,
        "action": "POSITION_CLOSED",
        "coin": coin,
        "direction": direction,
        "reason": reason,
        "entryPrice": entry_price,
        "exitPrice": exit_price,
        "pnl": round(pnl, 2),
        "alreadyClosed": already_closed,
        "slotsRemaining": max(0, max_slots - len(active)),
    }
