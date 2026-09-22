#!/usr/bin/env python3
"""Closed round trips from Senpi discovery (`discovery_get_trader_history`) — the PRIMARY source when a
Senpi token is present. Senpi ingests the whole fill stream, TWAP slices included, so its closed positions
are complete where the public endpoints are not. Rows are mapped onto the same episode schema the
fill-based tracker produces, so every downstream module is source-agnostic.

Row fields (guide `senpi://guides/trader-closed-positions`): closedOrderId, coin, coinDisplayName, entryPx,
exitPx, leverage{type,value}, maxLeverage, openTime, closeTime (ms), szi (absolute size), realizedPnl
(gross of fees), marginUsed, type, totalFills, totalFees (signed). Numbers arrive as strings.

`type` is the exchange's own label and is the ONLY place a liquidation is named: "Close Long",
"Close Short", "Liquidated Cross Long", "Liquidated Isolated Short", "Auto-Deleveraging". A row whose
opening side discovery could not rebuild arrives with szi, entryPx and openTime all ZERO — common on
cross-liquidations and HIP-3 `xyz:` assets. Those rows still carry real realized P&L, so they are kept
and marked `complete=False`/`truncated=True` rather than dropped."""
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
    """Page newest-first until a page ends before the window; returns episode dicts closed in the window.

    Sets `meta["senpi_history_failed"]` when a page could not be READ — an exception or a
    `success: false` envelope. An empty list means one of two very different things, and the caller
    has to tell them apart: senpi has no closed positions for this wallet in the window (a quiet
    wallet, or one that is not indexed yet), or senpi could not answer. Reporting the second as the
    first tells an indexed reader they are not indexed — and, since the desk offers to flag them to
    the team, promises something about a wallet that is already there.
    """
    out, offset = [], 0
    for _ in range(MAX_PAGES):
        try:
            resp = client.mcp_call("discovery_get_trader_history", trader_address=addr, latest=True, limit=PAGE, offset=offset,
                                   sort_by="CLOSED_TIME", sort_direction="DESC", timeout=20)
        except Exception as e:  # noqa: BLE001
            meta.setdefault("warnings", []).append(f"senpi history page {offset // PAGE} failed: {e}")
            meta["senpi_history_failed"] = True
            # Pages already read are a PREFIX of the history, not the history. Breaking with `out`
            # non-empty shipped a truncated record as a complete one — same source line, same
            # `indexed: True`, totals quietly short. (@0xsarvesh, #718.)
            if out:
                meta["senpi_history_partial"] = True
            break
        if isinstance(resp, dict) and resp.get("success") is False:
            # `_rows` flattens this to [] like any empty page, and it is the shape a degraded
            # discovery service returns most often — so it has to be caught before that.
            meta.setdefault("warnings", []).append(
                f"senpi history page {offset // PAGE} refused: {str((resp.get('error') or {}).get('code') or 'success=false')[:80]}")
            meta["senpi_history_failed"] = True
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


def _direction(r, szi, ent, ext, realized):
    """`szi` on a closed row is the absolute size on the live API (a closed short arrives as "0.2099", not
    "-0.2099"), so the sign is the last resort: the price move against the P&L decides when both are
    non-zero, then the row's `type` ("Close Short" / "Close Long"), then an explicit side field, then szi."""
    if ent and ext and ext != ent and realized:
        return "LONG" if ((ext > ent) == (realized > 0)) else "SHORT"
    typ = str(r.get("type") or "").upper()
    if "SHORT" in typ:
        return "SHORT"
    if "LONG" in typ:
        return "LONG"
    side = str(r.get("direction") or r.get("side") or r.get("positionSide") or "").upper()
    if side in ("LONG", "SHORT"):
        return side
    if side in ("BUY", "B"):
        return "LONG"
    if side in ("SELL", "A", "S"):
        return "SHORT"
    return "LONG" if szi > 0 else "SHORT"


def _liquidated(r):
    """`type` carries the exchange's own words — "Liquidated Cross Long", "Liquidated Isolated Short".
    Matching on "LIQUIDAT" covers all four. `Auto-Deleveraging` is deliberately NOT counted: ADL is the
    venue unwinding a profitable book, not a stop the trader failed to place."""
    return "LIQUIDAT" in str(r.get("type") or "").upper()


def episode(r):
    coin = r.get("coin") or r.get("coinDisplayName")
    close_t = _ms(r.get("closeTime"))
    if not coin or not close_t:
        return None          # with no coin and no close there is nothing to place in the window
    szi = _num(r.get("szi"))
    ent, ext = _num(r.get("entryPx")), _num(r.get("exitPx"))
    lev = r.get("leverage") or {}
    open_t = _ms(r.get("openTime"))
    # Discovery cannot always rebuild the OPENING side of a position: a cross-liquidation arrives with
    # szi, entryPx and openTime all zero. Requiring them dropped the whole row — and those rows carry
    # real realized P&L. On one wallet that was $1,137,374 of a $1,496,623 loss, including a $773,802
    # cross-liquidation; venue-wide it is 14% of realized P&L by magnitude. Keep the row so every TOTAL
    # is right, and mark it incomplete so the statistics that genuinely need an open — hold time, entry
    # timing, sizing, the lever grid — skip it exactly as they skip a truncated fill-built episode.
    reconstructed = bool(szi) and bool(open_t)
    if not open_t:
        # never leave this 0: `score._funding_after` reads `open_time + 24h` as a real instant, and an
        # epoch open would match every funding payment before the close.
        open_t = close_t
    size = abs(szi); notional = size * ent
    realized = _num(r.get("realizedPnl")); fees = _num(r.get("totalFees"))
    direction = _direction(r, szi, ent, ext, realized)
    signed = size if direction == "LONG" else -size
    return dict(coin=coin, signed=signed, direction=direction, open_time=open_t, close_time=close_t, last_time=close_t,
                realized=realized, fees=fees, net=realized - fees, volume=size * (ent + ext), taker_volume=0.0, twap_volume=0.0,
                adds=None, partial_closes=0, entry_qty=size, entry_val=notional, exit_qty=size,
                exit_val=size * ext, peak_size=size, peak_notional=notional, liquidated=_liquidated(r),
                truncated=not reconstructed, n_fills=int(_num(r.get("totalFills"), 0)),
                unobserved_qty=0.0, unobserved_notional=0.0, close_observed=True, entry_vwap=ent or None, exit_vwap=ext or None,
                hold_h=(close_t - open_t) / 3.6e6,
                taker_share=None, complete=reconstructed, win=realized > 0, leverage=_num(lev.get("value")) if isinstance(lev, dict) else _num(lev),
                margin_used=_num(r.get("marginUsed")), closed_order_id=r.get("closedOrderId"), source="senpi")
