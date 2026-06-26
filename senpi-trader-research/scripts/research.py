#!/usr/bin/env python3
"""senpi-trader-research engine — find copy candidates + vet a single trader (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
trader read (see SKILL.md). The script does the data work — pull the historical track-record ranking,
or build a due-diligence dossier on one trader (track record + current positions + 4h momentum) — and
the LLM does the analyst prose + the CTAs.

  python3 research.py                          # top traders (find copy candidates)
  python3 research.py --trader 0xabc…          # due-diligence dossier on one trader
  python3 research.py --strategies             # top copy-trading strategies (mirror leaderboard)
  python3 research.py --time-frame ALL_TIME --sort-by WIN_RATE --limit 15
  python3 research.py --fixture f.json          # offline (tests)   |   --dry  (raw dump)

Modeled on senpi-strategy-discover's hidden-engine pattern: guarded I/O, fails open, valid JSON.
⚠ discovery_* needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Senpi Discovery reliability floor (per the overview): a track record needs enough trades + days.
MIN_TRADES_FOR_TRUST = 5
MIN_ACTIVE_DAYS_FOR_TRUST = 7


# ──────────────────────────────────────────────────────────────── guarded helpers
def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(d, *keys, default=None):
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                n = _num(d[k])
                if n is not None:
                    return n
    return default


def _field(d, *names, default=None):
    if isinstance(d, dict):
        for n in names:
            if n in d and d[n] is not None:
                return d[n]
    return default


def _short(addr):
    a = str(addr or "")
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def _rows(data, *keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys + ("traders", "data", "results", "strategies", "entries"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from mcp_client import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in. Keys a call by tool + the most specific discriminator present."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        addr = (kw.get("trader_addresses") or [None])[0] or kw.get("trader_address") or kw.get("trader_id")
        for disc in (addr, kw.get("dex")):
            if disc:
                k = f"{tool}::{str(disc).lower()}"
                if k in self._r:
                    return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── find candidates
def _candidate(t):
    return {
        "address": _field(t, "address", "trader_address", "wallet", default=""),
        "short": _field(t, "shortAddress", "short_address") or _short(_field(t, "address", "trader_address", "wallet")),
        "roi_pct": _f(t, "returnOnInvestment", "roi", "roiPct", "return_on_investment"),
        "pnl_usd": _f(t, "profitAndLoss", "pnl", "realizedProfitAndLoss"),
        "win_rate_pct": _f(t, "winRate", "win_rate"),
        "max_drawdown_pct": _f(t, "maxDrawdown", "max_drawdown"),
        "trades": _f(t, "totalTrades", "tradeCount", "trades", "numTrades"),
        "active_days": _f(t, "activeDays", "active_days", "traderAgeDays"),
        "consistency": _field(t, "consistency", "consistencyLabel", "tcs"),
        "risk": _field(t, "risk", "riskLabel"),
        "activity": _field(t, "activity", "activityLabel", "tas"),
    }


def _reliability(c):
    trades, days = c.get("trades"), c.get("active_days")
    if (trades is not None and trades < MIN_TRADES_FOR_TRUST) or \
       (days is not None and days < MIN_ACTIVE_DAYS_FOR_TRUST):
        return "thin"            # too few trades/days to trust the record
    if c.get("consistency") == "CHOPPY":
        return "choppy"          # erratic — high variance
    if c.get("consistency") in ("ELITE", "RELIABLE"):
        return "solid"
    return "ok"


def find_top_traders(client, meta, time_frame, sort_by, limit):
    try:
        resp = client.mcp_call("discovery_get_top_traders", time_frame=time_frame, sort_by=sort_by,
                               limit=limit, timeout=20)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"top_traders failed: {e}")
        return []
    out = []
    for t in _rows(_ok(resp)):
        if not isinstance(t, dict):
            continue
        c = _candidate(t)
        c["reliability"] = _reliability(c)
        out.append(c)
    return out


def find_top_strategies(client, meta, limit):
    try:
        resp = client.mcp_call("discovery_get_top_strategies", limit=limit, timeout=20)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"top_strategies failed: {e}")
        return []
    out = []
    for s in _rows(_ok(resp)):
        if not isinstance(s, dict):
            continue
        out.append({
            "strategy_wallet": _field(s, "strategyWalletAddress", "strategy_wallet", "wallet"),
            "copied_trader": _short(_field(s, "traderAddress", "copied_trader", "trader_address")),
            "total_pnl_usd": _f(s, "totalPnl", "total_pnl"),
            "realized_pnl_usd": _f(s, "realizedPnl", "realized_pnl"),
            "return_pct": _f(s, "returnPercentage", "return_pct", "roi"),
            "followers": _f(s, "followers", "followerCount"),
            "age_days": _f(s, "ageDays", "strategyAgeDays", "age_days"),
        })
    return out


# ──────────────────────────────────────────────────────────────── vet one trader
def vet_trader(client, meta, addr):
    dossier = {"address": addr, "short": _short(addr)}

    # 1) track record + behavior labels — pull this trader's row from the ranking
    try:
        tr = client.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME",
                             addresses=[addr], limit=1, timeout=20)
        row = next((r for r in _rows(_ok(tr)) if isinstance(r, dict)), {})
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"labels lookup failed: {e}")
        row = {}
    c = _candidate(row) if row else {}
    dossier["track_record"] = {k: c.get(k) for k in
                               ("roi_pct", "pnl_usd", "win_rate_pct", "max_drawdown_pct", "trades", "active_days")}
    dossier["labels"] = {"consistency": c.get("consistency"), "risk": c.get("risk"), "activity": c.get("activity")}
    dossier["reliability"] = _reliability(c) if c else "unknown"

    # 2) current state — open positions + account risk
    try:
        st = _ok(client.mcp_call("discovery_get_trader_state", trader_addresses=[addr], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"trader_state failed: {e}")
        st = None
    positions, net_notional, upnl, margin_pct = [], 0.0, 0.0, None
    trec = next((t for t in _rows(st, "traders") if isinstance(t, dict)), st if isinstance(st, dict) else {})
    if isinstance(trec, dict):
        cms = _field(trec, "crossMarginSummary", "cross_margin_summary", default={}) or {}
        margin_pct = _f(trec, "marginPercentage", "margin_percentage") or _f(cms, "marginPercentage")
        for p in (_field(trec, "openPositions", "open_positions", "positions", default=[]) or []):
            if not isinstance(p, dict):
                continue
            szi = _f(p, "szi", "size", default=0.0)
            val = _f(p, "positionValue", "position_value", "notional", default=0.0) or 0.0
            pu = _f(p, "unrealizedPnl", "unrealized_pnl", default=0.0) or 0.0
            upnl += pu
            net_notional += (val if szi > 0 else -val)
            positions.append({"asset": _field(p, "coin", "asset"),
                              "direction": "long" if szi > 0 else "short",
                              "notional": round(abs(val), 2), "upnl": round(pu, 2),
                              "roe_pct": round((_f(p, "returnOnEquity", "return_on_equity", default=0.0) or 0.0) * 100, 2)})
    dossier["current_positions"] = positions
    dossier["net_exposure"] = {"net_notional_usd": round(net_notional, 2),
                               "bias": "long" if net_notional > 0 else "short" if net_notional < 0 else "flat",
                               "unrealized_pnl_usd": round(upnl, 2),
                               "margin_pct": round(margin_pct, 1) if margin_pct is not None else None}

    # 3) recent 4h momentum
    try:
        lm = _ok(client.mcp_call("leaderboard_get_trader", trader_id=addr, timeout=15))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"leaderboard_get_trader failed: {e}")
        lm = None
    if isinstance(lm, dict):
        dossier["recent_momentum"] = {"rank": _f(lm, "rank"),
                                      "delta_pnl_4h_usd": _f(lm, "deltaPnl", "delta_pnl"),
                                      "active_positions": _f(lm, "activePositions", "active_positions")}
    else:
        dossier["recent_momentum"] = None

    # 4) flags + caveats (analyst anchors; the LLM narrates)
    flags = []
    if dossier["reliability"] == "thin":
        flags.append("thin_track_record")     # < 5 trades or < 7 active days — record not yet trustworthy
    if dossier["labels"].get("consistency") == "CHOPPY":
        flags.append("choppy_consistency")
    if margin_pct is not None and margin_pct > 90:
        flags.append("critical_margin_usage")
    elif margin_pct is not None and margin_pct > 80:
        flags.append("high_margin_usage")
    if upnl < 0:
        flags.append("currently_in_drawdown")
    if positions and max(p["notional"] for p in positions) > 0.6 * (sum(p["notional"] for p in positions) or 1):
        flags.append("concentrated_book")
    dossier["flags"] = flags
    return dossier


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, mode, addr=None, time_frame="MONTHLY", sort_by="RETURN_ON_INVESTMENT", limit=20):
    meta = {"warnings": []}
    out = {"as_of": "live", "mode": mode, "meta": meta}
    if mode == "vet":
        out["trader"] = vet_trader(client, meta, addr)
    elif mode == "strategies":
        out["strategies"] = find_top_strategies(client, meta, limit)
    else:
        out["candidates"] = find_top_traders(client, meta, time_frame, sort_by, limit)
        out["ranking"] = {"time_frame": time_frame, "sort_by": sort_by}
    if mode != "vet" and not out.get("candidates") and not out.get("strategies"):
        meta["degraded"] = "no ranking data — check the token is USER-scoped"
    return out


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    try:
        out["discovery_get_top_traders"] = client.mcp_call("discovery_get_top_traders",
                                                           time_frame="MONTHLY", limit=3, timeout=20)
    except Exception as e:  # noqa
        out["discovery_get_top_traders"] = {"error": str(e)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi trader-research engine (find candidates + vet one)")
    ap.add_argument("--trader", help="vet this trader address (due-diligence dossier)")
    ap.add_argument("--strategies", action="store_true", help="rank top copy-trading strategies instead")
    ap.add_argument("--time-frame", default="MONTHLY", choices=["DAILY", "WEEKLY", "MONTHLY", "ALL_TIME"])
    ap.add_argument("--sort-by", default="RETURN_ON_INVESTMENT")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--fixture")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    if a.fixture:
        try:
            with open(a.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"candidates": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"candidates": [], "meta": {"error": f"mcp init failed: {e}"}}))
            return 1

    if a.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    mode = "vet" if a.trader else ("strategies" if a.strategies else "top")
    try:
        result = run(client, mode, addr=a.trader, time_frame=a.time_frame, sort_by=a.sort_by, limit=a.limit)
    except Exception as e:  # noqa
        print(json.dumps({"candidates": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
