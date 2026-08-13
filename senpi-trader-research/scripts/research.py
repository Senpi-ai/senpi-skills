#!/usr/bin/env python3
"""senpi-trader-research engine — find copy candidates + vet a single trader (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
trader read (see SKILL.md). The script does the data work — and the LLM does the analyst prose + CTAs.

Track record only says whether a trader is GOOD; it never says whether you can COPY them right now.
So FIND is mirror-aware by default: it enriches the top candidates with their live book, the PRICE
distance of each position from the trader's entry (what a mirror's slippage gates on), and 4h
momentum — and returns a `mirror_shortlist` ranked by copyability, not ROI.

  python3 research.py                          # find copy candidates — mirror-aware (top + mirror_shortlist)
  python3 research.py --trader 0xabc…          # due-diligence dossier on one trader
  python3 research.py --strategies             # top copy-trading strategies (mirror leaderboard)
  python3 research.py --time-frame ALL_TIME --sort-by WIN_RATE --limit 15
  python3 research.py --no-mirror              # track record only (skip the live-book enrichment)
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
# A copy decision needs more than a track record. These gate the mirror layer:
CATASTROPHIC_DD = -60.0        # a max drawdown at/below this can't read "solid" — near-liquidation history
BLOWUP_DD = -80.0             # …and at/below this caps the verdict at "choppy" outright
HIGH_TURNOVER_PER_DAY = 8.0   # above this, a proportional copy bleeds fees (fees are the biggest killer)
NEAR_ENTRY_BAND_PCT = 5.0     # a position within this PRICE distance of the trader's entry is a fresh mirror entry
ENRICH_TOP_DEFAULT = 5        # how many top find-candidates to mirror-enrich (positions + distance + momentum)
MIN_NOTIONAL_USD = 12.0       # HL per-position minimum (the $10 floor, auto-bumped to ~$12) — a copy below this is skipped
MIRROR_DUST_FRAC = 0.01       # positions below this share of notional are dust — excluded from the whole-book budget so a residual tail can't explode it


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
    c = {
        "address": _field(t, "address", "trader_address", "wallet", default=""),
        "short": _field(t, "shortAddress", "short_address") or _short(_field(t, "address", "trader_address", "wallet")),
        "roi_pct": _f(t, "returnOnInvestment", "roi", "roiPct", "return_on_investment"),
        "pnl_usd": _f(t, "profitAndLoss", "pnl", "realizedProfitAndLoss"),
        "win_rate_pct": _f(t, "winRate", "win_rate"),
        "max_drawdown_pct": _f(t, "maxDrawdown", "max_drawdown"),
        "trades": _f(t, "totalTrades", "tradeCount", "trades", "numTrades"),
        "active_days": _f(t, "activeDays", "active_days", "traderAgeDays"),
        "consistency": _field(t, "tcsLabel", "consistency", "consistencyLabel", "tcs"),
        "risk": _field(t, "risk", "riskLabel"),
        "activity": _field(t, "activity", "activityLabel", "tas"),
        "trades_per_day": _f(t, "averageTradesPerDay"),
    }
    # live payload carries age as traderAgeSeconds and activity as averageTradesPerDay —
    # derive the human units the ranker/reliability gate need
    if c["active_days"] is None:
        age_s = _f(t, "traderAgeSeconds")
        if age_s is not None:
            c["active_days"] = round(age_s / 86400.0, 1)
    if c["trades"] is None:
        tpd = _f(t, "averageTradesPerDay")
        if tpd is not None and c["active_days"] is not None:
            c["trades"] = round(tpd * c["active_days"])
    if c["trades_per_day"] is None and c["trades"] and c["active_days"]:
        c["trades_per_day"] = round(c["trades"] / c["active_days"], 2)
    return c


def _reliability(c):
    trades, days = c.get("trades"), c.get("active_days")
    dd = c.get("max_drawdown_pct")
    if (trades is not None and trades < MIN_TRADES_FOR_TRUST) or \
       (days is not None and days < MIN_ACTIVE_DAYS_FOR_TRUST):
        return "thin"            # too few trades/days to trust the record
    if c.get("consistency") == "CHOPPY":
        return "choppy"          # erratic — high variance
    verdict = "solid" if c.get("consistency") in ("ELITE", "RELIABLE") else "ok"
    # A catastrophic drawdown can't read as "solid": the record may be real, but a trader who was
    # once near-liquidated is not a safe copy. The record stands; the risk caps the verdict.
    if dd is not None:
        if dd <= BLOWUP_DD:
            return "choppy"
        if dd <= CATASTROPHIC_DD and verdict == "solid":
            return "ok"
    return verdict


# ──────────────────────────────────────────────────────── the mirror-decision layer
# Track record says whether a trader is GOOD. None of it says whether you can COPY them right now.
# That takes their current book (can you open near their entries?) + 4h momentum (are they hot?).
def _positions_from_state(trec):
    """Open positions + account aggregates from a discovery_get_trader_state record. Each position
    carries `moved_from_entry_pct` — the PRICE distance from the trader's entry, which is exactly what
    a mirror's slippage tolerance gates on. (ROE is leveraged and overstates that distance.)"""
    positions, net_notional, upnl, margin_pct, account_value = [], 0.0, 0.0, None, None
    if isinstance(trec, dict):
        cms = _field(trec, "crossMarginSummary", "cross_margin_summary", default={}) or {}
        margin_pct = _f(trec, "marginPercentage", "margin_percentage") or _f(cms, "marginPercentage")
        account_value = _f(trec, "accountValue", "account_value") or _f(cms, "accountValue", "account_value")
        for p in (_field(trec, "openPositions", "open_positions", "positions", default=[]) or []):
            if not isinstance(p, dict):
                continue
            szi = _f(p, "szi", "size", default=0.0) or 0.0
            val = _f(p, "positionValue", "position_value", "notional", default=0.0) or 0.0
            pu = _f(p, "unrealizedPnl", "unrealized_pnl", default=0.0) or 0.0
            entry = _f(p, "entryPx", "entry_px")
            mark = (abs(val) / abs(szi)) if szi else None
            moved = round((mark - entry) / entry * 100, 2) if (entry and mark) else None
            upnl += pu
            net_notional += (val if szi > 0 else -val)
            positions.append({
                "asset": _field(p, "coin", "asset"),
                "direction": "long" if szi > 0 else "short",
                "notional": round(abs(val), 2),
                "upnl": round(pu, 2),
                "roe_pct": round((_f(p, "returnOnEquity", "return_on_equity", default=0.0) or 0.0) * 100, 2),
                "entry_px": entry,
                "mark_px": round(mark, 6) if mark else None,
                "moved_from_entry_pct": moved,      # signed price move since entry; |·| is the slippage distance
            })
    return (positions, round(net_notional, 2), round(upnl, 2),
            round(margin_pct, 1) if margin_pct is not None else None,
            round(account_value, 2) if account_value is not None else None)


def _mirrorability(positions):
    """How copyable this book is RIGHT NOW: the share of notional still within slippage range of the
    trader's entry. A mirror opens those near-entry positions; the ones that already ran it either
    skips (tight slippage) or chases into a bad price (loose slippage). This is the go/no-go."""
    total = sum(p["notional"] for p in positions) if positions else 0.0
    scored = [p for p in (positions or []) if p.get("moved_from_entry_pct") is not None]
    if not scored or total <= 0:
        return {"fresh_entry_surface_pct": None, "mirror_fit": "unknown",
                "positions_scored": 0, "near_entry_band_pct": NEAR_ENTRY_BAND_PCT}
    near = sum(p["notional"] for p in scored if abs(p["moved_from_entry_pct"]) <= NEAR_ENTRY_BAND_PCT)
    surface = round(near / total * 100, 1)
    fit = "good" if surface >= 60 else "partial" if surface >= 20 else "poor"
    return {"fresh_entry_surface_pct": surface, "mirror_fit": fit,
            "positions_scored": len(scored), "near_entry_band_pct": NEAR_ENTRY_BAND_PCT}


def _min_mirror_budget(account_value, positions, mult=1.0, dust_frac=MIRROR_DUST_FRAC):
    """The minimum budget required to run a mirror of THIS trader's CURRENT book PROPERLY — the copy-trading
    analog of a template's catalog minimum. This is a FACT about the floor, NOT a recommendation of how much
    to trade (that is the user's call). Your copy of a position opens only when
        budget >= MIN_NOTIONAL_USD * (account_value / position_notional) / mult.
    - `min_budget_usd` opens their whole book minus dust tails (positions >= `dust_frac` of notional) — the
      minimum at which the mirror actually replicates them rather than fragments of them, sized to their
      leverage. A residual tail is excluded so it can't explode the figure.
    - `opens_nothing_below_usd` is the hard floor: below it, even their largest position scales under the
      minimum and NOTHING opens. Whales run leveraged, concentrated books, so this is often a few dollars.
    Both at 1x; a higher multiplier divides them. A snapshot; the pre-fund sim is the exact per-position
    check. Returns None when it can't be computed (flat / no account value)."""
    notionals = sorted((p["notional"] for p in (positions or [])
                        if p.get("notional") and p["notional"] > 0), reverse=True)
    if not account_value or account_value <= 0 or not notionals:
        return None
    mult = mult or 1.0
    total = sum(notionals)
    nondust = [n for n in notionals if n >= dust_frac * total] or notionals

    def _b(pn):
        return round(MIN_NOTIONAL_USD * account_value / (pn * mult), 2)
    return {
        "min_budget_usd": _b(nondust[-1]),           # minimum to run PROPERLY — opens their whole book ex-dust
        "opens_nothing_below_usd": _b(notionals[0]), # hard floor: below this, literally nothing opens
        "at_multiplier": mult,
        "positions": len(notionals),
        "dust_excluded": len(notionals) - len(nondust),
        "note": f"minimum to run the mirror properly at {mult}x (opens their whole book ex-dust) — a fact, not a trade-size recommendation; the sim is the exact check",
    }


def _flags(c, positions=None, net_upnl=None, margin_pct=None):
    """The analyst's anchor list — surfaced verbatim by the skill. Track-record + book risks together."""
    flags = []
    if c.get("reliability") == "thin":
        flags.append("thin_track_record")     # < 5 trades or < 7 active days — not yet trustworthy
    if c.get("consistency") == "CHOPPY":
        flags.append("choppy_consistency")
    dd = c.get("max_drawdown_pct")
    if dd is not None and dd <= CATASTROPHIC_DD:
        flags.append("blowup_risk")            # ≤ -60% max drawdown — near-liquidation history
    tpd = c.get("trades_per_day")
    if tpd is not None and tpd > HIGH_TURNOVER_PER_DAY:
        flags.append("high_turnover")          # a proportional copy will bleed fees
    if margin_pct is not None and margin_pct > 90:
        flags.append("critical_margin_usage")
    elif margin_pct is not None and margin_pct > 80:
        flags.append("high_margin_usage")
    if net_upnl is not None and net_upnl < 0:
        flags.append("currently_in_drawdown")
    if positions:
        tot = sum(p["notional"] for p in positions) or 1
        if max(p["notional"] for p in positions) > 0.6 * tot:
            flags.append("concentrated_book")
        if len(positions) == 1:
            flags.append("single_position")    # one bet — un-diversifiable and often already run
    return flags


def _momentum_from_leaderboard(lm):
    if not isinstance(lm, dict):
        return None
    t = lm.get("trader") if isinstance(lm.get("trader"), dict) else lm
    pnl = t.get("pnl") if isinstance(t.get("pnl"), dict) else {}
    return {"rank": _f(t, "rank"),
            "delta_pnl_4h_usd": _f(t, "deltaPnl", "delta_pnl") or _f(pnl, "unrealized"),
            "active_positions": _f(t, "position_count", "activePositions", "active_positions")}


def _momentum_label(m):
    if not m or m.get("delta_pnl_4h_usd") is None:
        return "unknown"
    d = m["delta_pnl_4h_usd"]
    return "hot" if d > 0 else "cold" if d < 0 else "flat"


def _enrich_for_mirror(client, meta, c):
    """Attach the copy-decision layer to a find candidate — current book, price-distance mirrorability,
    4h momentum, full flags. Best-effort per trader; fails open so one bad lookup can't sink the find."""
    addr = c.get("address")
    positions, net_upnl, margin_pct, momentum, account_value = [], None, None, None, None
    try:
        st = _ok(client.mcp_call("discovery_get_trader_state", trader_addresses=[addr], timeout=15))
        trec = next((t for t in _rows(st, "traders") if isinstance(t, dict)), st if isinstance(st, dict) else {})
        positions, _net, net_upnl, margin_pct, account_value = _positions_from_state(trec)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"state {c.get('short')}: {e}")
    try:
        lm = _ok(client.mcp_call("leaderboard_get_trader", trader_id=addr, timeout=12))
        momentum = _momentum_from_leaderboard(lm)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"momentum {c.get('short')}: {e}")
    c["current_positions"] = positions
    c["mirrorability"] = _mirrorability(positions)
    c["min_mirror_budget"] = _min_mirror_budget(account_value, positions)
    c["recent_momentum"] = momentum
    c["momentum"] = _momentum_label(momentum)
    c["net_exposure"] = {"unrealized_pnl_usd": net_upnl, "margin_pct": margin_pct}
    c["flags"] = _flags(c, positions=positions, net_upnl=net_upnl, margin_pct=margin_pct)
    return c


_FIT_RANK = {"good": 0, "partial": 1, "poor": 2, "unknown": 3}
_REL_RANK = {"solid": 0, "ok": 1, "choppy": 2, "thin": 3, "unknown": 4}


def _mirror_sort_key(c):
    """Order the shortlist by COPYABILITY, not ROI: no blowup history, then mirror-fit, then a trusted
    record, then how much of the book is still near entry. This is what 'smart from the get-go' means."""
    m = c.get("mirrorability") or {}
    flags = c.get("flags") or []
    return (1 if "blowup_risk" in flags else 0,
            _FIT_RANK.get(m.get("mirror_fit"), 3),
            _REL_RANK.get(c.get("reliability"), 4),
            -(m.get("fresh_entry_surface_pct") or 0))


def find_top_traders(client, meta, time_frame, sort_by, limit, enrich_top=ENRICH_TOP_DEFAULT):
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
    # Mirror-aware by default: the FIRST answer must be able to lead with whether you can actually
    # copy these traders now, not just their track record. Enrich the top few we'd realistically
    # recommend with their live book + distance-from-entry + momentum. `enrich_top=0` opts out.
    for c in out[:min(enrich_top, len(out))] if enrich_top else []:
        _enrich_for_mirror(client, meta, c)
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
        followers = _f(s, "traderFollowerCount", "followerCount")
        if followers is None and isinstance(s.get("followers"), list):
            followers = float(len(s["followers"]))
        age_days = _f(s, "ageDays", "strategyAgeDays", "age_days")
        if age_days is None:
            created = _field(s, "strategyCreatedAt", "createdAt")
            if created:
                try:
                    import datetime as _dt
                    dt = _dt.datetime.fromisoformat(str(created).replace(" ", "T").replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_dt.timezone.utc)
                    age_days = round((_dt.datetime.now(_dt.timezone.utc) - dt).total_seconds() / 86400.0, 1)
                except ValueError:
                    pass
        out.append({
            "strategy_wallet": _field(s, "strategyWalletAddress", "strategy_wallet", "wallet"),
            "copied_trader": _short(_field(s, "traderAddress", "copied_trader", "trader_address")),
            "total_pnl_usd": _f(s, "totalPnl", "total_pnl"),
            "realized_pnl_usd": _f(s, "realizedPnl", "realized_pnl"),
            "return_pct": _f(s, "pnlPercentage", "returnPercentage", "return_pct", "roi"),
            "followers": followers,
            "age_days": age_days,
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
    c["reliability"] = _reliability(c) if c else "unknown"
    dossier["track_record"] = {k: c.get(k) for k in
                               ("roi_pct", "pnl_usd", "win_rate_pct", "max_drawdown_pct",
                                "trades", "active_days", "trades_per_day")}
    dossier["labels"] = {"consistency": c.get("consistency"), "risk": c.get("risk"), "activity": c.get("activity")}
    dossier["reliability"] = c.get("reliability", "unknown")

    # 2) current book — positions + PRICE-distance-from-entry mirrorability + account risk
    try:
        st = _ok(client.mcp_call("discovery_get_trader_state", trader_addresses=[addr], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"trader_state failed: {e}")
        st = None
    trec = next((t for t in _rows(st, "traders") if isinstance(t, dict)), st if isinstance(st, dict) else {})
    positions, net_notional, upnl, margin_pct, account_value = _positions_from_state(trec)
    dossier["current_positions"] = positions
    dossier["mirrorability"] = _mirrorability(positions)
    dossier["min_mirror_budget"] = _min_mirror_budget(account_value, positions)
    dossier["net_exposure"] = {"net_notional_usd": net_notional,
                               "bias": "long" if net_notional > 0 else "short" if net_notional < 0 else "flat",
                               "unrealized_pnl_usd": upnl,
                               "margin_pct": margin_pct}

    # 3) recent 4h momentum
    try:
        lm = _ok(client.mcp_call("leaderboard_get_trader", trader_id=addr, timeout=15))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"leaderboard_get_trader failed: {e}")
        lm = None
    dossier["recent_momentum"] = _momentum_from_leaderboard(lm)
    dossier["momentum"] = _momentum_label(dossier["recent_momentum"])

    # 4) flags + caveats (analyst anchors; the LLM narrates)
    dossier["flags"] = _flags(c, positions=positions, net_upnl=upnl, margin_pct=margin_pct)
    return dossier


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, mode, addr=None, time_frame="MONTHLY", sort_by="RETURN_ON_INVESTMENT", limit=20,
        enrich_top=ENRICH_TOP_DEFAULT):
    meta = {"warnings": []}
    out = {"as_of": "live", "mode": mode, "meta": meta}
    if mode == "vet":
        out["trader"] = vet_trader(client, meta, addr)
    elif mode == "strategies":
        out["strategies"] = find_top_strategies(client, meta, limit)
    else:
        cands = find_top_traders(client, meta, time_frame, sort_by, limit, enrich_top=enrich_top)
        out["candidates"] = cands
        # Lead the copy decision with the shortlist ranked by COPYABILITY, not the ROI table.
        enriched = [c for c in cands if "mirrorability" in c]
        if enriched:
            out["mirror_shortlist"] = sorted(enriched, key=_mirror_sort_key)
        out["ranking"] = {"time_frame": time_frame, "sort_by": sort_by, "enriched": len(enriched)}
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
    ap.add_argument("--enrich-top", type=int, default=ENRICH_TOP_DEFAULT,
                    help="mirror-enrich the top N find candidates (live book + distance-from-entry + momentum)")
    ap.add_argument("--no-mirror", action="store_true", help="skip mirror enrichment — track record only")
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
    enrich = 0 if a.no_mirror else a.enrich_top
    try:
        result = run(client, mode, addr=a.trader, time_frame=a.time_frame, sort_by=a.sort_by,
                     limit=a.limit, enrich_top=enrich)
    except Exception as e:  # noqa
        print(json.dumps({"candidates": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
