#!/usr/bin/env python3
"""senpi-market-pulse engine — Data Layer + deterministic cross-asset signals (hidden).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES the
top-down market read (see SKILL.md output contract). The script does the streamlined, resilient
data-gathering + the *concrete* signal computation; the LLM does ALL of the narrative analysis,
the catalyst (web-search) layer, and the closing CTAs.

  python3 pulse.py                 # full live pull (crypto + XYZ equities + indices + commodities/macro)
  python3 pulse.py --no-smart      # skip the leaderboard (smart-money) layer
  python3 pulse.py --fixture f.json  # offline: read a recorded MCP-response map (for tests)

Design contract (mirrors senpi-strategy-discover):
- ONE health-check + parallel bulk pull, then a capped parallel deep-pull of the biggest movers.
- Resilient by construction: daily moves come from `prevDayPx` on the instruments list, so a candle
  500 or a Hyperfeed outage never drops an asset or an asset class. Every class is always attempted.
- Fails open: any call that errors degrades to a flag in `meta`, never an exception; always valid
  JSON; exit 0 for handled cases.
- The script computes only CONCRETE signals (per-group averages, dispersion, the confirmation
  checklist, a coarse day-classification). The LLM owns the prose, the "why", and the CTAs.

NOTE FOR REVIEW: the MCP field names below (markPx / prevDayPx / funding / dayNtlVlm, and the
instruments-list shape) are taken from senpi-strategy-discover's live usage + the observed
`market_*` responses. Verify against one live `market_list_instruments` / `market_get_asset_data`
call before merge and adjust the `_field()` fallbacks if the schema differs.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────────────────────── asset universe (edit me)
# Grouped so the engine can compute per-group dispersion. `main` dex = crypto perps;
# everything else is an XYZ (HIP-3) instrument and needs dex="xyz" on per-asset pulls.
CRYPTO = ["BTC", "ETH", "SOL", "HYPE", "XRP", "SUI", "DOGE", "AVAX", "LINK", "AAVE", "BNB", "LTC", "NEAR"]

XYZ_GROUPS = {
    # the usual epicenter — split by distance from the memory/hardware core so the LLM can read the gradient
    "semis_memory":   ["MU", "SNDK", "SKHX", "DRAM", "WDC"],
    "semis_equipment": ["ASML", "TSM", "QCOM", "ARM", "MRVL"],
    "semis_logic":    ["NVDA", "AMD", "AVGO", "SMH"],
    "software_megacap": ["AMZN", "MSFT", "META", "GOOGL", "AAPL", "ORCL", "PLTR"],
    "crypto_proxy":   ["MSTR", "COIN", "HOOD", "CRWV"],
    "other_equity":   ["TSLA", "SPCX", "ZHIPU"],
    "indices":        ["SP500", "XYZ100", "JP225", "KR200", "NIFTY", "VIX"],
    "commodities":    ["GOLD", "SILVER", "COPPER", "BRENTOIL", "NATGAS", "PLATINUM"],
    "macro_fx":       ["DXY", "JPY", "EUR", "GBP"],
}
XYZ_ALL = [a for g in XYZ_GROUPS.values() for a in g]

# how many of the biggest movers get a deep (candle/volume/funding) pull
MOVER_DEEP_PULL = 12


# ──────────────────────────────────────────────────────────────── guarded I/O helpers
def _ok(resp):
    """Unwrap an MCP response; None if it failed (mcp_call returns success:False, not an exception)."""
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def _field(d, *names, default=None):
    """First present key among aliases — defends against minor schema drift across market_* tools."""
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(mark, prev):
    m, p = _num(mark), _num(prev)
    if m is None or p is None or p == 0:
        return None
    return round((m - p) / p * 100, 2)


def _funding_sign(funding):
    f = _num(funding)
    if f is None:
        return None
    return "positive" if f > 1e-6 else ("negative" if f < -1e-6 else "flat")


# ──────────────────────────────────────────────────────────────── data layer (MCP, fails open)
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from _mcp import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in: maps an mcp_call to a recorded response by `(tool, dex)`. Used by tests."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        key = tool + ("::" + kw["dex"] if "dex" in kw else "")
        return self._r.get(key, self._r.get(tool))


def fetch_instruments(client, meta):
    """Bulk price+prevDayPx for EVERY asset, both dexes, in two calls. This is the resilient backbone:
    daily moves never depend on candles, so a candle 500 can't drop an asset class.

    Returns {ASSET: {"price": float, "change_pct": float}}.
    """
    out = {}

    def pull(dex):
        kw = {"timeout": 12}
        if dex:
            kw["dex"] = dex
        try:
            data = _ok(client.mcp_call("market_list_instruments", **kw))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"instruments({dex or 'main'}) failed: {e}")
            return
        # accept {instruments:[...]} | {universe:[...]} | [...] | {data:[...]}
        rows = data if isinstance(data, list) else _field(data, "instruments", "universe", "data", default=[])
        if not isinstance(rows, list):
            return
        for r in rows:
            sym = _field(r, "name", "coin", "symbol", "asset")
            if not sym:
                continue
            sym = str(sym).upper().replace("XYZ:", "")
            price = _num(_field(r, "markPx", "mark", "price", "midPx"))
            prev = _num(_field(r, "prevDayPx", "prevDay", "prev"))
            out[sym] = {"price": price, "change_pct": _pct(price, prev)}

    pull(None)      # main (crypto)
    pull("xyz")     # HIP-3 equities / indices / commodities / fx
    if not out:
        meta.setdefault("warnings", []).append("instruments empty on both dexes — prices unavailable")
    return out


def deep_pull_movers(client, movers, meta):
    """Capped parallel candle/volume/funding pull on the biggest movers — adds conviction + funding read.

    `movers` is a list of (asset, is_xyz). Returns {ASSET: {volume_usd, funding, ...}}.
    """
    regime = None
    try:
        regime = _field(_ok(client.mcp_call("market_get_funding_regime", timeout=8)) or {}, "regime")
    except Exception:  # noqa
        pass

    def one(item):
        asset, is_xyz = item
        kw = dict(asset=asset, candle_intervals=["1h"], include_order_book=False,
                  include_funding=True, timeout=12)
        if is_xyz:
            kw["dex"] = "xyz"   # HIP-3 requires dex, else INVALID_ARGUMENT
        try:
            data = _ok(client.mcp_call("market_get_asset_data", **kw))
            if not data:
                return (asset, None)
            ctx = _field(data, "asset_context", "context", default={}) or {}
            return (asset, {
                "volume_usd": _num(_field(ctx, "dayNtlVlm", "dayNotionalVolume", "volume_usd")),
                "funding": _funding_sign(_field(ctx, "funding")),
                "oi_trend": _field(data.get("oi_velocity") or {}, "oi_trend"),
            })
        except Exception:  # noqa
            return (asset, None)

    pairs = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            pairs = list(ex.map(one, movers))
    except Exception:  # noqa
        pairs = [one(m) for m in movers]
    return {a: v for a, v in pairs if v}, regime


def fetch_smart_money(client, meta):
    """The leaderboard / Hyperfeed layer — health-gated. Returns None (cleanly) if the feed is down."""
    try:
        status = _ok(client.mcp_call("leaderboard_get_status", timeout=8))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"smart-money layer unavailable (status check: {e})")
        return None
    healthy = bool(status) and _field(status, "healthy", "ok", default=True)
    if not healthy:
        meta.setdefault("warnings", []).append("smart-money layer unavailable (Hyperfeed unhealthy)")
        return None

    sm = {"status": status}
    for label, tool in (("concentration", "leaderboard_get_markets"),
                        ("top_traders", "leaderboard_get_top"),
                        ("momentum_events", "leaderboard_get_momentum_events")):
        try:
            sm[label] = _ok(client.mcp_call(tool, timeout=10))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"{tool} failed: {e}")
            sm[label] = None
    return sm


# ──────────────────────────────────────────────────────────────── deterministic signals
def _avg(vals):
    xs = [v for v in vals if v is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def build_groups(prices):
    """Per-asset rows + per-group average move, for crypto and every XYZ group."""
    def rows(assets):
        out = []
        for a in assets:
            p = prices.get(a)
            if p:
                out.append({"asset": a, "price": p.get("price"), "change_pct": p.get("change_pct")})
            else:
                out.append({"asset": a, "price": None, "change_pct": None, "missing": True})
        return out

    groups = {"crypto": {"rows": rows(CRYPTO)}}
    groups["crypto"]["avg_change_pct"] = _avg(r["change_pct"] for r in groups["crypto"]["rows"])
    for name, assets in XYZ_GROUPS.items():
        groups[name] = {"rows": rows(assets)}
        groups[name]["avg_change_pct"] = _avg(r["change_pct"] for r in groups[name]["rows"])
    return groups


def compute_signals(prices, groups):
    """The concrete cross-asset reads the framework leans on (see references/analysis-framework.md).
    Booleans/labels only — the LLM turns these into prose."""
    def chg(a):
        return (prices.get(a) or {}).get("change_pct")

    sig = {}
    # the confirmation checklist (each: value + a plain label the LLM can cite)
    gold, dxy, vix = chg("GOLD"), chg("DXY"), chg("VIX")
    sp500 = chg("SP500")
    sig["gold"] = {"change_pct": gold,
                   "read": "haven bid intact (no forced-liquidation cascade)" if (gold is not None and gold > -2)
                   else "haven also selling — possible liquidity event" if gold is not None else None}
    sig["dxy"] = {"change_pct": dxy,
                  "read": "dollar calm — no funding stress" if (dxy is not None and abs(dxy) < 0.6)
                  else "dollar bid — flight-to-USD / funding stress" if (dxy is not None and dxy > 0.6) else None}
    sig["vix"] = {"value": (prices.get("VIX") or {}).get("price"), "change_pct": vix,
                  "read": "fear contained" if (vix is not None and (prices.get('VIX') or {}).get('price') or 0) and ((prices.get('VIX') or {}).get('price') or 0) < 22
                  else "fear elevated — watch for broadening" if vix is not None else None}

    # dispersion: is the headline index calm while components break?
    worst_group, worst_avg = None, 0.0
    for name, g in groups.items():
        a = g.get("avg_change_pct")
        if a is not None and a < worst_avg:
            worst_group, worst_avg = name, a
    sig["dispersion"] = {
        "sp500_change_pct": sp500,
        "worst_group": worst_group, "worst_group_avg_pct": round(worst_avg, 2) if worst_group else None,
        "read": ("dispersion — index calm while a sector breaks (rotation, not capitulation)"
                 if (sp500 is not None and worst_group and (sp500 - worst_avg) > 2.5)
                 else "broad — index moving with its components (macro, not rotation)"
                 if sp500 is not None and worst_group else None),
    }

    # coarse day classification (the LLM refines; this just seeds the headline)
    crypto_avg = groups.get("crypto", {}).get("avg_change_pct")
    breadth = [g.get("avg_change_pct") for g in groups.values() if g.get("avg_change_pct") is not None]
    down = sum(1 for x in breadth if x is not None and x < -0.5)
    up = sum(1 for x in breadth if x is not None and x > 0.5)
    if breadth:
        if down >= up * 2 and down >= 3:
            day = "risk_off"
        elif up >= down * 2 and up >= 3:
            day = "risk_on"
        else:
            day = "mixed"
    else:
        day = None
    sig["day_classification"] = {"label": day, "groups_down": down, "groups_up": up,
                                 "crypto_avg_pct": crypto_avg}
    return sig


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_smart=True):
    meta = {"warnings": []}
    prices = fetch_instruments(client, meta)
    groups = build_groups(prices)
    signals = compute_signals(prices, groups)

    # deep-pull the biggest movers for volume/funding conviction (capped)
    ranked = sorted(
        ((a, p.get("change_pct")) for a, p in prices.items() if p.get("change_pct") is not None),
        key=lambda x: abs(x[1]), reverse=True,
    )
    xyz_set = set(XYZ_ALL)
    movers = [(a, a in xyz_set) for a, _ in ranked[:MOVER_DEEP_PULL]]
    deep, regime = ({}, None)
    if movers:
        deep, regime = deep_pull_movers(client, movers, meta)
    # fold conviction back onto the rows
    for g in groups.values():
        for row in g["rows"]:
            if row["asset"] in deep:
                row.update({k: v for k, v in deep[row["asset"]].items() if v is not None})
    signals["funding_regime"] = regime

    smart = fetch_smart_money(client, meta) if want_smart else None
    meta["smart_money_available"] = smart is not None
    if not prices:
        meta["degraded"] = "no price data — all instrument pulls failed"

    return {
        "as_of": "live",                       # the agent stamps the human date; script stays deterministic
        "day_classification": signals.get("day_classification"),
        "signals": signals,
        "groups": groups,
        "smart_money": smart,
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi market-pulse engine (cross-asset snapshot + signals)")
    ap.add_argument("--no-smart", action="store_true", help="skip the leaderboard / smart-money layer")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"groups": {}, "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"groups": {}, "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    try:
        result = run(client, want_smart=not args.no_smart)
    except Exception as e:  # noqa  — last-resort guard; the layer functions already fail open
        print(json.dumps({"groups": {}, "meta": {"error": f"engine failure: {e}"}}))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
