#!/usr/bin/env python3
"""What senpi's own runtime is doing to a position — read-only, and never an overclaim.

The desk reads resting orders off the exchange, so a DSL-managed position already shows as
PROTECTED: phase 1 posts the stop at entry, before any ratchet tier arms. Verified against the
public order book on three unarmed positions — `activeSLOrderId` was null on all three (that field
tracks the RATCHET's order, not phase 1) and all three carried a full-size reduce-only trigger.

What the desk could not say is WHAT that stop is. A price with no context reads as a static stop
when it is a floor that ratchets, and the reader cannot tell which tier is armed or what is still
ahead. This module supplies that, and the one thing it must never do is present the ladder as
protection already in force.

  tiers: [{triggerRoe: 8, lockRoe: 35}, …]   currentTierIndex: 0   tierFloorPrice: 3.1134

`currentTierIndex` is the armed tier; -1 means none and the floor is phase 1's. `lockRoe` is a
share of the HIGH-WATER GAIN, not an absolute: floor = entry + lock% x (high_water - entry).
Saying "protected in all tiers" would claim tier 3's 88% floor when 35% is locked — on the worked
example, 1.45% of entry at 5x of protection that does not exist. An agent made exactly that
mistake in production once: it reported "Tier 3 locked 17.7%" when only breakeven was locked.

Shapes here come from captured production responses, not from a live call — every read is
`.get()`, and any failure leaves the book exactly as it was.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _rows(resp, key):
    d = (resp or {}).get("data") or {}
    v = d.get(key)
    return v if isinstance(v, list) else []


def strategies_for(mcp, addr):
    """Every senpi strategy whose wallet IS this address. Empty for an external wallet."""
    if mcp is None or not addr:
        return []
    try:
        resp = mcp.mcp_call("strategy_list", timeout=20)
    except Exception:  # noqa: BLE001
        return []
    a = addr.lower()
    return [s for s in _rows(resp, "strategies")
            if str(s.get("strategyWalletAddress") or "").lower() == a]


def attach(mcp, addr, book, meta=None):
    """Annotate each open position with the DSL state senpi holds for it.

    Adds `position["dsl"]` only where there is one to add. Silent no-op for an external wallet, a
    missing token, or any failure — the protection audit must read exactly as it does today rather
    than lose a section because a side read went down.
    """
    strats = strategies_for(mcp, addr)
    if not strats:
        return 0
    if meta is not None:
        meta.setdefault("sources", {})["dsl"] = (
            f"senpi runtime — {len(strats)} strateg{'y' if len(strats) == 1 else 'ies'} on this wallet")
    found = 0
    for p in book.get("positions") or []:
        for s in strats:
            sid = s.get("id")
            if not sid:
                continue
            try:
                resp = mcp.mcp_call("ratchet_stop_list", strategyId=sid,
                                    strategy_wallet_address=addr, asset=p["coin"],
                                    status="ACTIVE", timeout=20)
            except Exception:  # noqa: BLE001
                continue
            live = [r for r in _rows(resp, "positions") if r.get("status") == "ACTIVE"]
            if not live:
                continue
            r = live[0]
            tiers = (((r.get("dslConfig") or {}).get("tiered") or {}).get("tiers")) or []
            idx = r.get("currentTierIndex")
            idx = -1 if idx is None else int(idx)
            p["dsl"] = dict(
                strategy=s.get("strategyName"), tiers=tiers,
                tier_index=idx, n_tiers=len(tiers),
                # -1 is phase 1: the survival floor, posted at entry. 0+ is a profit tier armed.
                phase=1 if idx < 0 else 2,
                floor_px=r.get("tierFloorPrice"),
                high_water_px=r.get("highWaterPrice"), high_water_roe=r.get("highWaterRoe"),
                armed=None if idx < 0 else tiers[idx] if idx < len(tiers) else None,
                next_tier=tiers[idx + 1] if 0 <= idx + 1 < len(tiers) else (tiers[0] if idx < 0 and tiers else None),
            )
            found += 1
            break
    return found


def line(p):
    """One sentence for a DSL-managed position. States what is LOCKED, then what is merely ahead.

    The distinction is the whole point: an armed tier is protection, an unarmed one is a rule that
    has not fired. Never collapse them into "protected in all tiers"."""
    d = p.get("dsl")
    if not d:
        return None
    n, nxt = d["n_tiers"], d.get("next_tier")
    nxt_trig = float(nxt.get("triggerRoe") or 0) if nxt else None
    nxt_lock = float(nxt.get("lockRoe") or 0) if nxt else None
    if d["phase"] == 1:
        tail = f"; the first locks at +{nxt_trig:.0f}% ROE" if nxt else ""
        head = ("**DSL phase 1** \u2014 the survival floor, posted at entry. "
                f"No profit tier armed yet{tail}.")
    else:
        a = d.get("armed") or {}
        floor = d.get("floor_px")
        # .4g turns 3.1134 into 3.113. This is a price the reader may place by hand —
        # keep the venue's own precision.
        floor_s = f"{float(floor):,.6g}" if floor is not None else "its floor"
        ahead = (f" Next: +{nxt_trig:.0f}% ROE locks {nxt_lock:.0f}%." if nxt
                 else " That is the last tier.")
        head = (f"**DSL tier {d['tier_index'] + 1} of {n}** armed \u2014 floor {floor_s}, "
                f"locking {float(a.get('lockRoe') or 0):.0f}% of the gain from entry.{ahead}")
    return head + " The floor only ever rises."
