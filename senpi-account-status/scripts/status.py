#!/usr/bin/env python3
"""senpi-account-status engine — the user's standing across Senpi programs (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES the
user's standing (see SKILL.md): Senpi points + rank, the Senpi trading fee, and referral earnings.
One real-time pull across all the status tools.

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
    # The fee this user pays, as the backend reports it (0 on a paid plan). The payload field is
    # still named loyaltyTierFee, but the tier no longer sets it.
    fee = {
        "bps": _f(p, "loyaltyTierFee", "feeBps", "builderFeeBps"),
        "pct": _field(p, "builderFeePercent"),
    }
    return points, fee


def fetch_referral(client, meta):
    try:
        r = _ok(client.mcp_call("user_get_referral_rewards", timeout=12)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_referral_rewards failed: {e}")
        return {}
    return {"balance_usdc": _f(r, "balance_usdc", "balanceUsdc", "balance"),
            "wallet": _field(r, "wallet_address", "walletAddress")}


# ──────────────────────────────────────────────────────────────── orchestration
def run(client):
    meta = {"warnings": []}
    user = resolve_user(client, meta)
    points, fee = fetch_points(client, meta, user.get("wallet"), user.get("senpi_user_id"))
    referral = fetch_referral(client, meta)
    if not user.get("senpi_user_id") and not user.get("wallet"):
        meta["degraded"] = "no user resolved — check the token is USER-scoped"
    return {
        "as_of": "live",
        "identity": user,
        "points": points,
        "fee": fee,
        "referral": referral,
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    for tool in ("user_get_me", "user_get_referral_rewards"):
        try:
            out[tool] = client.mcp_call(tool, timeout=12)
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
