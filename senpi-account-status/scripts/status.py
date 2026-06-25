#!/usr/bin/env python3
"""senpi-account-status engine — the user's standing across Senpi programs (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES the
user's standing (see SKILL.md): Senpi points + rank, loyalty tier + fee, Arena position, referral
earnings, and shareable wins. One real-time pull across all the status tools.

  python3 status.py                 # full standing
  python3 status.py --fixture f.json   # offline (tests)   |   --dry  (raw dump)

Modeled on the other read skills: guarded I/O, fails open, valid JSON.
⚠ All tools are USER-scoped (the user's own account): needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


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


def _rows(data, *keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys + ("entries", "data", "tiers", "results"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from _mcp import MCPClient
    return MCPClient()


class _FixtureClient:
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        inp = kw.get("input") or {}
        disc = inp.get("walletAddress") or inp.get("userId") or kw.get("period_type")
        if disc:
            k = f"{tool}::{str(disc).lower()}"
            if k in self._r:
                return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── data layer
def resolve_user(client, meta):
    out = {"senpi_user_id": None, "wallet": None}
    try:
        me = _ok(client.mcp_call("user_get_me", timeout=12)) or {}
        u = me.get("user", me) if isinstance(me, dict) else {}
        out["senpi_user_id"] = _field(u, "senpiUserId", "userId", "id")
        for w in (_field(u, "wallets", default=[]) or []):
            if str(_field(w, "walletType", "type", default="")).lower() == "embedded":
                out["wallet"] = _field(w, "walletAddress", "address")
                break
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_me failed: {e}")
    return out


def fetch_points(client, meta, wallet, user_id):
    try:
        inp = {"walletAddress": wallet} if wallet else {"userId": user_id}
        p = _ok(client.mcp_call("user_get_senpi_points", input=inp, timeout=15)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_senpi_points failed: {e}")
        return {}, {}
    p = p.get("user", p) if isinstance(p, dict) and "user" in p else p
    points = {
        "total": _f(p, "totalPoints", "points", "total"),
        "base": _f(p, "basePoints", "base"),
        "perp": _f(p, "perpPoints", "perp"),
        "multiplier": _f(p, "loyaltyMultiplier", "multiplier"),
        "rank": _f(p, "rank"),
        "rank_change": _f(p, "rankChange", "rank_change"),
        "found": _field(p, "found", default=True),
    }
    loyalty = {
        "tier": _field(p, "loyaltyTier", "tier", "tierName"),
        "fee_bps": _f(p, "feeBps", "builderFeeBps", "fee_bps"),
        "fee_discount_pct": _f(p, "feeDiscount", "feeDiscountPct", "discount"),
        "maintenance": _field(p, "maintenanceStatus", "maintenance"),
        "next_tier": _field(p, "nextTier", "next_tier"),
        "points_to_next": _f(p, "pointsToNextTier", "points_to_next", "nextTierPoints"),
    }
    return points, loyalty


def enrich_next_tier(client, meta, loyalty, points_total):
    """If the points response didn't carry next-tier progress, derive it from the tier table."""
    if loyalty.get("points_to_next") is not None or points_total is None:
        return
    try:
        tiers = _rows(_ok(client.mcp_call("get_loyalty_tiers", timeout=12)), "tiers")
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"get_loyalty_tiers failed: {e}")
        return
    thresholds = sorted((_f(t, "threshold", "pointsThreshold", "minPoints", default=None), _field(t, "name", "tier"))
                        for t in tiers if _f(t, "threshold", "pointsThreshold", "minPoints", default=None) is not None)
    for thr, name in thresholds:
        if thr > points_total:
            loyalty["next_tier"] = loyalty.get("next_tier") or name
            loyalty["points_to_next"] = round(thr - points_total, 0)
            break


def fetch_referral(client, meta):
    try:
        r = _ok(client.mcp_call("user_get_referral_rewards", timeout=12)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_referral_rewards failed: {e}")
        return {}
    return {"balance_usdc": _f(r, "balance_usdc", "balanceUsdc", "balance"),
            "wallet": _field(r, "wallet_address", "walletAddress")}


def fetch_arena(client, meta, user_id):
    arena = {"enrolled": False}
    try:
        lb = _rows(_ok(client.mcp_call("arena_leaderboard", period_type="WEEK", limit=500, timeout=20)), "entries")
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"arena_leaderboard failed: {e}")
        lb = []
    me = next((e for e in lb if str(_field(e, "senpiUserId", "userId", default="")) == str(user_id)), None) if user_id else None
    if me:
        arena.update({"enrolled": True, "rank": _f(me, "rank"),
                      "roe_pct": _f(me, "roePct", "roe"),
                      "total_pnl_usd": _f(me, "totalPnl", "total_pnl"),
                      "trade_count": _f(me, "tradeCount", "trades"),
                      "notional_volume_usd": _f(me, "notionalVolume", "notional_volume"),
                      "qualified": _field(me, "qualified", default=None)})
    try:
        pool = _ok(client.mcp_call("arena_pool", timeout=12)) or {}
        arena["week_pool_usd"] = _f(pool, "currentWeekPool", "current_week_pool")
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"arena_pool failed: {e}")
    if arena.get("enrolled") and arena.get("rank"):
        try:
            prizes = _rows(_ok(client.mcp_call("arena_prizes", period_type="WEEK", timeout=12)), "entries")
            pe = next((e for e in prizes if _f(e, "rank") == arena["rank"]), None)
            if pe:
                arena["prize_estimate_usd"] = _f(pe, "prizeAmount", "prize_amount")
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"arena_prizes failed: {e}")
    return arena


def fetch_wins(client, meta, limit=5):
    try:
        w = _rows(_ok(client.mcp_call("get_share_your_wins", limit=limit, timeout=15)), "wins", "positions")
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"get_share_your_wins failed: {e}")
        return []
    out = []
    for p in w[:limit]:
        if isinstance(p, dict):
            out.append({"asset": _field(p, "coin", "asset"),
                        "realized_pnl_usd": _f(p, "realizedPnl", "realized_pnl"),
                        "return_pct": _f(p, "returnPercentage", "roe", "return_pct")})
    return out


# ──────────────────────────────────────────────────────────────── orchestration
def run(client):
    meta = {"warnings": []}
    user = resolve_user(client, meta)
    points, loyalty = fetch_points(client, meta, user.get("wallet"), user.get("senpi_user_id"))
    enrich_next_tier(client, meta, loyalty, points.get("total"))
    referral = fetch_referral(client, meta)
    arena = fetch_arena(client, meta, user.get("senpi_user_id"))
    wins = fetch_wins(client, meta)
    if not user.get("senpi_user_id") and not user.get("wallet"):
        meta["degraded"] = "no user resolved — check the token is USER-scoped"
    return {
        "as_of": "live",
        "identity": user,
        "points": points,
        "loyalty": loyalty,
        "arena": arena,
        "referral": referral,
        "wins": wins,
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    for tool, kw in (("user_get_me", {}), ("user_get_referral_rewards", {}),
                    ("arena_pool", {}), ("get_loyalty_tiers", {})):
        try:
            out[tool] = client.mcp_call(tool, timeout=12, **kw)
        except Exception as e:  # noqa
            out[tool] = {"error": str(e)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi account-status engine (standing across programs)")
    ap.add_argument("--fixture")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    if a.fixture:
        try:
            with open(a.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"points": {}, "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"points": {}, "meta": {"error": f"mcp init failed: {e}"}}))
            return 1

    if a.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0
    try:
        result = run(client)
    except Exception as e:  # noqa
        print(json.dumps({"points": {}, "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
