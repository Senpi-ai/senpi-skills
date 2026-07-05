#!/usr/bin/env python3
"""senpi-account-status engine — the user's standing across Senpi programs (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES the
user's standing (see SKILL.md): Senpi points + rank, loyalty tier + fee, Arena position, and referral
earnings. One real-time pull across all the status tools.

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
    from mcp_client import MCPClient
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
    if not wallet and not user_id:
        return {}, {}
    try:
        inp = {"walletAddress": wallet} if wallet else {"userId": user_id}
        p = _ok(client.mcp_call("user_get_senpi_points", input=inp, timeout=15)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_senpi_points failed: {e}")
        return {}, {}
    p = p.get("user", p) if isinstance(p, dict) and "user" in p else p
    points = {
        "total": _f(p, "points", "totalPoints", "total"),
        "base": _f(p, "basePoints", "base"),
        "perp": _f(p, "perpPoints", "perp"),
        "multiplier": _f(p, "loyaltyMultiplier", "multiplier"),
        "rank": _f(p, "rank"),
        "rank_change": _f(p, "rankChange", "rank_change"),
        "found": _field(p, "found", default=True),
    }
    loyalty = {
        "tier": _field(p, "loyaltyTier", "tier", "tierName"),
        "fee_bps": _f(p, "loyaltyTierFee", "feeBps", "builderFeeBps"),
        "fee_pct": _field(p, "builderFeePercent"),
        "fee_discount_pct": None,  # not in the points payload — enriched from the tier table below
        "maintenance": _field(p, "maintenanceStatus", "maintenance"),
        "maintenance_deadline": _field(p, "tierMaintenanceDeadline"),
        "demoted": _field(p, "isDemoted", default=False),
        "previous_tier": _field(p, "previousLoyaltyTier"),
        "next_tier": _field(p, "nextTier", "next_tier"),
        "points_to_next": _f(p, "pointsToNextTier", "points_to_next", "nextTierPoints"),
        "next_tier_threshold": _f(p, "nextTierThreshold"),
    }
    return points, loyalty


def enrich_from_tiers(client, meta, loyalty, points_total):
    """Fill what the points payload doesn't carry from the tier table: the current tier's fee
    discount (only lives in get_loyalty_tiers) and, if missing, next-tier progress."""
    need_discount = loyalty.get("tier") is not None
    need_next = loyalty.get("points_to_next") is None and points_total is not None
    if not (need_discount or need_next):
        return
    try:
        tiers = _rows(_ok(client.mcp_call("get_loyalty_tiers", timeout=12)), "tiers")
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"get_loyalty_tiers failed: {e}")
        return
    if need_discount:
        cur = next((t for t in tiers if str(_field(t, "tier", "name", default="")).upper()
                    == str(loyalty["tier"]).upper()), None)
        if cur:
            loyalty["fee_discount_pct"] = _f(cur, "discountPercent", "discount")
    if need_next:
        thresholds = sorted((thr, _field(t, "tier", "name"))
                            for t in tiers
                            if (thr := _f(t, "threshold", "pointsThreshold", "minPoints")) is not None)
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


def _inner(data, key):
    """Descend one level into the tool's named wrapper: {leaderboard:{...}} / {pool:{...}} / {prizes:{...}}."""
    if isinstance(data, dict) and isinstance(data.get(key), dict):
        return data[key]
    return data


def fetch_arena(client, meta, user_id):
    arena = {"enrolled": False}
    try:
        lb_doc = _inner(_ok(client.mcp_call("arena_leaderboard", period_type="WEEK", limit=500, timeout=20)) or {},
                        "leaderboard")
        lb = _rows(lb_doc, "entries")
        if _f(lb_doc, "totalCount", default=len(lb)) > len(lb):
            meta.setdefault("warnings", []).append(
                "arena_leaderboard truncated at 500 rows — enrollment check may miss agents ranked below 500")
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
        pool = _inner(_ok(client.mcp_call("arena_pool", timeout=12)) or {}, "pool")
        arena["week_pool_usd"] = _f(pool, "currentWeekPool", "current_week_pool")
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"arena_pool failed: {e}")
    if arena.get("enrolled") and arena.get("rank"):
        try:
            prizes = _rows(_inner(_ok(client.mcp_call("arena_prizes", period_type="WEEK", timeout=12)) or {},
                                  "prizes"), "entries")
            pe = next((e for e in prizes if _f(e, "rank") == arena["rank"]), None)
            if pe:
                arena["prize_estimate_usd"] = _f(pe, "prizeAmount", "prize_amount")
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"arena_prizes failed: {e}")
    return arena


# ──────────────────────────────────────────────────────────────── orchestration
def run(client):
    meta = {"warnings": []}
    user = resolve_user(client, meta)
    points, loyalty = fetch_points(client, meta, user.get("wallet"), user.get("senpi_user_id"))
    enrich_from_tiers(client, meta, loyalty, points.get("total"))
    referral = fetch_referral(client, meta)
    arena = fetch_arena(client, meta, user.get("senpi_user_id"))
    if not user.get("senpi_user_id") and not user.get("wallet"):
        meta["degraded"] = "no user resolved — check the token is USER-scoped"
    return {
        "as_of": "live",
        "identity": user,
        "points": points,
        "loyalty": loyalty,
        "arena": arena,
        "referral": referral,
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
