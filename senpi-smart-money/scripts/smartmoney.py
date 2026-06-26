#!/usr/bin/env python3
"""senpi-smart-money engine — cohort + divergence + near-term flow (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES
"where smart money is moving" (see SKILL.md). The script does the heavy, deterministic data work —
build the proven cohort vs the crowd, aggregate net positioning, find the divergences, pull the
near-term Leaderboard/Hyperfeed flow — and the LLM does all the prose, the "why", and the CTAs.

  python3 smartmoney.py               # full pull (cohorts + divergence + near-term)
  python3 smartmoney.py --no-near     # skip the leaderboard / Hyperfeed near-term layer
  python3 smartmoney.py --fixture f.json   # offline: recorded MCP-response map (tests)
  python3 smartmoney.py --dry         # dump raw MCP responses for schema debugging

Modeled on the whalehunter strategy's cohort engine (same definitions + bias math), and on
senpi-strategy-discover's hidden-engine pattern: guarded I/O, fails open, always valid JSON.

⚠ discovery_* requires a USER-scoped SENPI_AUTH_TOKEN (it resolves a user id). With an app-scoped
token the cohort pulls return empty and the engine reports `meta.cohorts_unavailable` — narrate that
honestly rather than pretending the smart cohort is flat.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────────────────────── cohort definitions (mirror whalehunter)
SMART_MIN_REALIZED = 1_000_000      # "smartest money": lifetime realized gains >= $1M
CROWD_MIN_REALIZED = 10_000         # "crowd": $10k ..
CROWD_MAX_REALIZED = 100_000        #        .. $100k realized
PAGE_SIZE = 1000                    # discovery_get_top_traders page size (ALL_TIME realized ranking)
MAX_PAGES = 6                       # page this deep to REACH the crowd band (it sits far below the smart top)
SAMPLE_CAP = 150                    # cap each cohort's membership sample (bounds trader_state load)
STATE_BATCH = 50                    # discovery_get_trader_state batch size
MIN_MEMBERS = 5                     # need this many in a cohort on a coin to trust its net bias
LEAN_THRESHOLD = 0.40               # |net/gross| past this = the cohort is meaningfully directional
DIVERGENCE_MIN_GAP = 0.50           # smart-vs-crowd bias gap to flag a divergence (opposite signs always flag)


# ──────────────────────────────────────────────────────────────── guarded I/O helpers
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


def _f(d, *keys, default=0.0):
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


def _traders_of(data):
    """Normalize a discovery response into a list of trader dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("traders", "data", "results"):
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
    """Offline stand-in. Keys a call by (tool, dex) or (tool, first trader_address) so a fixture can
    return DIFFERENT trader-state for the smart vs crowd cohort. Falls back to the bare tool name."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        if "dex" in kw:
            k = f"{tool}::{kw['dex']}"
            if k in self._r:
                return self._r[k]
        addrs = kw.get("trader_addresses")
        if addrs:
            k = f"{tool}::{str(addrs[0]).lower()}"
            if k in self._r:
                return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── cohort building (mirror whalehunter)
def _realized(t):
    # LIFETIME realized PnL — never fall back to total profitAndLoss (not monotonic with the realized sort)
    return _f(t, "realizedProfitAndLoss", "realized_profit_and_loss", "profit_and_loss_realized",
              "realizedPnl", "realized_pnl", default=0.0)


def build_cohorts(client, meta):
    """Smart cohort (realized >= $1M) + crowd cohort ($10k..$100k) from the ALL_TIME realized-PnL
    ranking. The ranking is DESC by realized, so the smart cohort is at the top and the crowd lives
    thousands of ranks deeper — page by offset until both are sampled or the page drops below the
    crowd floor."""
    smart, crowd, seen = [], [], set()
    pages = 0
    for page in range(MAX_PAGES):
        try:
            resp = client.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME",
                                   sort_by="PROFIT_AND_LOSS_REALIZED", open_position_filter=False,
                                   limit=PAGE_SIZE, offset=page * PAGE_SIZE, timeout=20)
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"top_traders page {page} failed: {e}")
            break
        rows = _traders_of(_ok(resp))
        if not rows:
            break
        pages += 1
        page_top = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            addr = str(_field(t, "address", "trader_address", "wallet", default="")).lower()
            if not addr or addr in seen:
                continue
            rp = _realized(t)
            page_top = rp if page_top is None else max(page_top, rp)
            if rp >= SMART_MIN_REALIZED:
                if len(smart) < SAMPLE_CAP:
                    smart.append(addr); seen.add(addr)
            elif CROWD_MIN_REALIZED <= rp <= CROWD_MAX_REALIZED:
                if len(crowd) < SAMPLE_CAP:
                    crowd.append(addr); seen.add(addr)
        if len(smart) >= SAMPLE_CAP and len(crowd) >= SAMPLE_CAP:
            break
        if page_top is not None and page_top < CROWD_MIN_REALIZED:
            break   # whole page below the crowd floor — we've paged past both cohorts
    meta["cohort_pages"] = pages
    return smart, crowd


def _signed_notional(p):
    szi = _f(p, "szi", "size")
    val = _f(p, "positionValue", "notional", "position_value")
    if val <= 0:
        val = abs(szi) * _f(p, "entryPx", "markPx", "entry_price")
    return (1.0 if szi > 0 else (-1.0 if szi < 0 else 0.0)) * abs(val)


def cohort_bias(client, addrs, meta, label):
    """Aggregate a cohort's NET positioning per coin: bias = net/gross in [-1,+1]
    (+1 = all long, -1 = all short), plus long/short member counts. Batched."""
    per = {}
    for i in range(0, len(addrs), STATE_BATCH):
        batch = addrs[i:i + STATE_BATCH]
        try:
            resp = client.mcp_call("discovery_get_trader_state", trader_addresses=batch, timeout=20)
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"{label} trader_state batch failed: {e}")
            continue
        for t in _traders_of(_ok(resp)):
            for p in (t.get("openPositions") or t.get("open_positions") or []):
                if not isinstance(p, dict):
                    continue
                coin = p.get("coin") or p.get("asset")
                sn = _signed_notional(p) if coin else 0.0
                if not coin or sn == 0:
                    continue
                d = per.setdefault(coin, {"net": 0.0, "gross": 0.0, "n_long": 0, "n_short": 0})
                d["net"] += sn
                d["gross"] += abs(sn)
                d["n_long" if sn > 0 else "n_short"] += 1
    for d in per.values():
        d["bias"] = round(d["net"] / d["gross"], 3) if d["gross"] > 0 else 0.0
        d["members"] = d["n_long"] + d["n_short"]
        d["net"] = round(d["net"], 2)
    return per


# ──────────────────────────────────────────────────────────────── signal computation
def _dir(bias):
    return "long" if bias > 0 else ("short" if bias < 0 else "flat")


def smart_conviction(smart_per):
    """Where the proven cohort is most net-directional (the 'where smart money is leaning' headline)."""
    out = []
    for coin, d in smart_per.items():
        if d["members"] >= MIN_MEMBERS and abs(d["bias"]) >= LEAN_THRESHOLD:
            out.append({"asset": coin, "bias": d["bias"], "direction": _dir(d["bias"]),
                        "members": d["members"], "n_long": d["n_long"], "n_short": d["n_short"],
                        "net_usd": d["net"]})
    out.sort(key=lambda x: abs(x["bias"]) * x["members"], reverse=True)
    return out


def divergences(smart_per, crowd_per):
    """Where the proven cohort and the crowd are on OPPOSITE sides (or far apart) — the core signal."""
    out = []
    for coin, sd in smart_per.items():
        if sd["members"] < MIN_MEMBERS:
            continue
        cd = crowd_per.get(coin)
        if not cd or cd["members"] < MIN_MEMBERS:
            continue
        gap = round(sd["bias"] - cd["bias"], 3)
        opposite = (sd["bias"] > 0) != (cd["bias"] > 0) and sd["bias"] != 0 and cd["bias"] != 0
        if opposite or abs(gap) >= DIVERGENCE_MIN_GAP:
            out.append({
                "asset": coin, "gap": gap, "opposite_sides": opposite,
                "smart_bias": sd["bias"], "smart_direction": _dir(sd["bias"]),
                "smart_members": sd["members"], "smart_net_usd": sd["net"],
                "crowd_bias": cd["bias"], "crowd_direction": _dir(cd["bias"]),
                "crowd_members": cd["members"],
            })
    out.sort(key=lambda x: (x["opposite_sides"], abs(x["gap"])), reverse=True)
    return out


# ──────────────────────────────────────────────────────────────── near-term layer (Leaderboard / Hyperfeed)
def fetch_near_term(client, meta):
    """The 4h-window momentum layer — health-gated. Returns None cleanly if Hyperfeed is down.
    leaderboard_get_markets = where the hot cohort's gains concentrate; momentum_events = the live
    entry/scale/exit flow (is the move building or fading)."""
    try:
        status = client.mcp_call("leaderboard_get_status", timeout=8)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"near-term layer unavailable (status: {e})")
        return None
    if _ok(status) is None:
        meta.setdefault("warnings", []).append("near-term layer unavailable (Hyperfeed unreachable)")
        return None
    near = {"status": _ok(status)}
    for label, tool in (("concentration", "leaderboard_get_markets"),
                        ("hot_traders", "leaderboard_get_top"),
                        ("momentum_events", "leaderboard_get_momentum_events")):
        try:
            near[label] = _ok(client.mcp_call(tool, timeout=10))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"{tool} failed: {e}")
            near[label] = None
    return near


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_near=True):
    meta = {"warnings": []}
    smart_addrs, crowd_addrs = build_cohorts(client, meta)
    meta["smart_cohort_size"] = len(smart_addrs)
    meta["crowd_cohort_size"] = len(crowd_addrs)

    if not smart_addrs and not crowd_addrs:
        meta["cohorts_unavailable"] = (
            "no cohort data — discovery_get_top_traders returned empty. discovery_* needs a "
            "USER-scoped SENPI_AUTH_TOKEN; an app-scoped token returns nothing here.")

    smart_per = cohort_bias(client, smart_addrs, meta, "smart") if smart_addrs else {}
    crowd_per = cohort_bias(client, crowd_addrs, meta, "crowd") if crowd_addrs else {}

    leaning = smart_conviction(smart_per)
    diverge = divergences(smart_per, crowd_per)
    near = fetch_near_term(client, meta) if want_near else None
    meta["near_term_available"] = near is not None

    return {
        "as_of": "live",
        "cohorts": {
            "smart": {"min_realized_usd": SMART_MIN_REALIZED, "members_sampled": len(smart_addrs),
                      "coins": len(smart_per)},
            "crowd": {"realized_band_usd": [CROWD_MIN_REALIZED, CROWD_MAX_REALIZED],
                      "members_sampled": len(crowd_addrs), "coins": len(crowd_per)},
        },
        "smart_leaning": leaning,        # where the proven cohort is concentrated (headline)
        "divergences": diverge,          # smart vs crowd, opposite sides (the core signal)
        "near_term": near,               # Leaderboard / Hyperfeed 4h flow (confirm or contradict)
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    try:
        out["discovery_get_top_traders(page0)"] = client.mcp_call(
            "discovery_get_top_traders", time_frame="ALL_TIME", sort_by="PROFIT_AND_LOSS_REALIZED",
            open_position_filter=False, limit=5, offset=0, timeout=20)
    except Exception as e:  # noqa
        out["discovery_get_top_traders(page0)"] = {"error": str(e)}
    for tool, kw in (("leaderboard_get_status", {}), ("leaderboard_get_markets", {})):
        try:
            out[tool] = client.mcp_call(tool, timeout=8, **kw)
        except Exception as e:  # noqa
            out[tool] = {"error": str(e)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi smart-money engine (cohort + divergence + near-term)")
    ap.add_argument("--no-near", action="store_true", help="skip the leaderboard / Hyperfeed near-term layer")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"smart_leaning": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"smart_leaning": [], "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    if args.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    try:
        result = run(client, want_near=not args.no_near)
    except Exception as e:  # noqa  — last-resort guard; layer functions already fail open
        print(json.dumps({"smart_leaning": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
