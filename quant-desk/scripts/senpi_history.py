#!/usr/bin/env python3
"""Closed round trips from Senpi discovery (`discovery_get_trader_history`) — the PRIMARY source when a
Senpi token is present. Senpi ingests the whole fill stream, TWAP slices included, so its closed positions
are complete where the public endpoints are not. Rows are mapped onto the same episode schema the
fill-based tracker produces, so every downstream module is source-agnostic.

Row fields (guide `senpi://guides/trader-closed-positions`): closedOrderId, coin, coinDisplayName, entryPx,
exitPx, leverage{type,value}, maxLeverage, openTime, closeTime (ms), szi (signed size, + = closed long),
realizedPnl (gross of fees), marginUsed, type, totalFills, totalFees (signed). Numbers arrive as strings."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
PAGE = 200
MAX_PAGES = 25


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ms(t):
    t = _num(t)
    return t * 1000.0 if t and t < 1e12 else t


def _rows(data):
    if isinstance(data, dict):
        if data.get("success") is False:
            return []
        data = data.get("data", data)
    if isinstance(data, dict):
        for k in ("closedPositions", "closed_positions", "positions", "data"):
            v = data.get(k)
            if isinstance(v, list):
                return v
        return []
    return data if isinstance(data, list) else []


def fetch(client, addr, window_start_ms, meta):
    """Page newest-first until a page ends before the window; returns episode dicts closed in the window."""
    out, offset = [], 0
    for _ in range(MAX_PAGES):
        try:
            resp = client.mcp_call("discovery_get_trader_history", trader_address=addr, latest=True, limit=PAGE, offset=offset,
                                   sort_by="CLOSED_TIME", sort_direction="DESC", timeout=20)
        except Exception as e:  # noqa: BLE001
            meta.setdefault("warnings", []).append(f"senpi history page {offset // PAGE} failed: {e}")
            break
        rows = _rows(resp)
        if not rows:
            break
        for r in rows:
            e = episode(r)
            if e and e["close_time"] >= window_start_ms:
                out.append(e)
        if _ms(rows[-1].get("closeTime")) < window_start_ms or len(rows) < PAGE:
            break
        offset += PAGE
    return sorted(out, key=lambda e: e["close_time"])


def episode(r):
    coin = r.get("coin") or r.get("coinDisplayName")
    szi = _num(r.get("szi"))
    if not coin or not szi:
        return None
    ent, ext = _num(r.get("entryPx")), _num(r.get("exitPx"))
    lev = r.get("leverage") or {}
    open_t, close_t = _ms(r.get("openTime")), _ms(r.get("closeTime"))
    if not open_t or not close_t:
        return None
    size = abs(szi); notional = size * ent
    realized = _num(r.get("realizedPnl")); fees = _num(r.get("totalFees"))
    return dict(coin=coin, signed=szi, direction="LONG" if szi > 0 else "SHORT", open_time=open_t, close_time=close_t, last_time=close_t,
                realized=realized, fees=fees, net=realized - fees, volume=size * (ent + ext), taker_volume=0.0, twap_volume=0.0,
                adds=max(0, int(_num(r.get("totalFills"), 1)) - 2), partial_closes=0, entry_qty=size, entry_val=notional, exit_qty=size,
                exit_val=size * ext, peak_size=size, peak_notional=notional, liquidated=False, truncated=False, n_fills=int(_num(r.get("totalFills"), 0)),
                unobserved_qty=0.0, close_observed=True, entry_vwap=ent or None, exit_vwap=ext or None, hold_h=(close_t - open_t) / 3.6e6,
                taker_share=None, complete=True, win=realized > 0, leverage=_num(lev.get("value")) if isinstance(lev, dict) else _num(lev),
                margin_used=_num(r.get("marginUsed")), closed_order_id=r.get("closedOrderId"), source="senpi")
