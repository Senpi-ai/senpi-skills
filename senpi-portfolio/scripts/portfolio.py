#!/usr/bin/env python3
"""senpi-portfolio engine — real-time wallet/balance taxonomy + holdings analysis (hidden).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
portfolio analysis (see SKILL.md). The script does the precise, real-time data work — enumerate every
wallet, classify every dollar into the right bucket, attribute positions, and pull market context for
analysis — and the LLM does the prose, the comparison, and the CTAs.

  python3 portfolio.py              # full real-time pull (all wallets + market context)
  python3 portfolio.py --no-market  # skip the per-asset market enrichment
  python3 portfolio.py --fixture f.json   # offline: recorded MCP-response map (tests)
  python3 portfolio.py --dry        # dump raw MCP responses for schema debugging

WHY THIS EXISTS — the balance-bucket trap:
Agents conflate `total_withdrawable` (free margin sitting INSIDE strategy wallets) with "idle cash in
the main embedded wallet." They are different buckets. This engine computes three structurally
separate pools so the agent never mixes them:
  1. idle_in_embedded   = total_usdc_in_hyperliquid + EVM token_balances   (truly free; deploy or withdraw)
  2. idle_in_strategies = sum of each strategy wallet's `withdrawable`      (in a strategy, not a position)
  3. deployed           = margin backing open positions
Grand total = idle_in_embedded + idle_in_strategies + deployed.

REAL-TIME, NEVER CACHED: account_get_portfolio caches HL data 12h unless forceFetch=true — this
engine always passes forceFetch. Per-strategy truth comes from live strategy_get_clearinghouse_state.

⚠ All tools here are USER-scoped (your own account): needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET_ENRICH_CAP = 24      # cap the per-asset market pull


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


def _pct(mark, prev):
    m, p = _num(mark), _num(prev)
    if m is None or p is None or p == 0:
        return None
    return round((m - p) / p * 100, 2)


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from _mcp import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in. Keys a call by (tool, strategy_wallet) or (tool, asset/dex) so a fixture can
    return per-wallet clearinghouse state. Falls back to the bare tool name."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        for keyer in ("strategy_wallet", "asset"):
            if kw.get(keyer):
                k = f"{tool}::{str(kw[keyer]).lower()}"
                if k in self._r:
                    return self._r[k]
        if "dex" in kw:
            k = f"{tool}::{kw['dex']}"
            if k in self._r:
                return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── wallet discovery
def fetch_embedded(client, meta):
    """Main/embedded wallet idle cash — the ONLY truly-free pool. Real-time (forceFetch)."""
    out = {"address": None, "idle_hl_usdc": None, "evm_usdc": [], "spot_usd": None,
           "idle_total": None}
    try:
        me = _ok(client.mcp_call("user_get_me", timeout=12)) or {}
        wallets = _field(me, "wallets", default=[]) or (me.get("user", {}) or {}).get("wallets", [])
        for w in wallets if isinstance(wallets, list) else []:
            if str(_field(w, "walletType", "type", default="")).lower() == "embedded":
                out["address"] = _field(w, "walletAddress", "address")
                break
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_me failed: {e}")

    try:
        # forceFetch=True → bypass the 12h HL cache. This is the cache-freshness guarantee.
        p = _ok(client.mcp_call("account_get_portfolio", forceFetch=True, strategyStatus="ALL", timeout=25)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"account_get_portfolio failed: {e}")
        return out, {}

    out["idle_hl_usdc"] = _f(p, "total_usdc_in_hyperliquid", default=0.0)
    out["spot_usd"] = _f(p, "total_spot_usd_in_hyperliquid", default=0.0)
    evm = 0.0
    for tb in (_field(p, "token_balances", default=[]) or []):
        sym = str(_field(tb, "symbol", "tokenSymbol", default="")).upper()
        if sym in ("USDC", "USDC.E", "USDT"):
            amt = _f(tb, "usdValue", "usd_value", "amountUsd", "balanceUsd", "amount", default=0.0)
            chain = _field(tb, "chain", "network", "chainName", default="EVM")
            if amt:
                out["evm_usdc"].append({"chain": chain, "usd": round(amt, 2)})
                evm += amt
    out["idle_total"] = round((out["idle_hl_usdc"] or 0.0) + evm, 2)
    portfolio_totals = {
        "total_balance_usd": _f(p, "total_balance_usd", default=None),
        "total_allocated_in_strategy": _f(p, "total_allocated_in_strategy", default=None),
        "total_withdrawable": _f(p, "total_withdrawable", default=None),
    }
    return out, portfolio_totals


def fetch_strategies(client, meta):
    """Live per-strategy state: enumerate strategies, then clearinghouse state per wallet (real-time,
    both DEXes). withdrawable = free margin idle IN that strategy; positions = deployed."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    strategies = []
    for s in (rows or []):
        wallet = _field(s, "strategyWalletAddress", "strategy_wallet_address", "walletAddress")
        if not wallet:
            continue
        strategies.append({
            "name": _field(s, "tradingStrategyName", "name", default="strategy"),
            "wallet": wallet,
            "status": _field(s, "status", default="ACTIVE"),
            "total_funded": _f(s, "totalFunded", "total_funded", default=None),
            "total_withdrawn": _f(s, "totalWithdrawn", "total_withdrawn", default=None),
        })

    def hydrate(strat):
        try:
            ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=strat["wallet"], timeout=20))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"clearinghouse {strat['wallet'][:8]} failed: {e}")
            return strat
        acct_value, withdrawable, positions = 0.0, 0.0, []
        for dex in ("main", "xyz"):
            d = _field(ch, dex, default={}) if isinstance(ch, dict) else {}
            ms = _field(d, "marginSummary", "margin_summary", default={}) or {}
            acct_value += _f(ms, "accountValue", "account_value", default=0.0)
            withdrawable += _f(d, "withdrawable", default=0.0)
            for ap in (_field(d, "assetPositions", "asset_positions", default=[]) or []):
                pos = _field(ap, "position", default=ap) or {}
                szi = _f(pos, "szi", "size", default=0.0)
                if szi == 0:
                    continue
                lev = pos.get("leverage") or {}
                positions.append({
                    "asset": _field(pos, "coin", "asset"),
                    "dex": dex,
                    "direction": "long" if szi > 0 else "short",
                    "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
                    "notional": round(abs(_f(pos, "positionValue", "position_value", default=0.0)), 2),
                    "margin": round(_f(pos, "marginUsed", "margin_used", default=0.0), 2),
                    "entry_px": _f(pos, "entryPx", "entry_px", default=None),
                    "upnl": round(_f(pos, "unrealizedPnl", "unrealized_pnl", default=0.0), 2),
                    "return_on_equity_pct": round(_f(pos, "returnOnEquity", "return_on_equity", default=0.0) * 100, 2),
                    "liq_px": _f(pos, "liquidationPx", "liquidation_px", default=None),
                })
        strat["account_value"] = round(acct_value, 2)
        strat["idle_withdrawable"] = round(withdrawable, 2)        # free margin sitting in THIS strategy
        strat["deployed"] = round(acct_value - withdrawable, 2)    # equity tied up in positions (margin + uPnL)
        strat["position_margin"] = round(sum(p["margin"] for p in positions), 2)   # initial margin detail
        strat["positions"] = positions
        return strat

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            strategies = list(ex.map(hydrate, strategies))
    except Exception:  # noqa
        strategies = [hydrate(s) for s in strategies]
    return strategies


# ──────────────────────────────────────────────────────────────── market context (for analysis)
def enrich_market(client, strategies, meta):
    """Per-held-asset 24h move so the LLM can compare each position to the broader market."""
    assets = []
    for s in strategies:
        for p in s.get("positions", []):
            tag = (p["asset"], p["dex"])
            if p["asset"] and tag not in assets:
                assets.append(tag)
    assets = assets[:MARKET_ENRICH_CAP]

    def one(item):
        asset, dex = item
        kw = dict(asset=asset, candle_intervals=["1h"], include_order_book=False, timeout=12)
        if dex == "xyz" or str(asset).startswith("xyz:"):
            kw["dex"] = "xyz"
        try:
            data = _ok(client.mcp_call("market_get_asset_data", **kw))
            ctx = _field(data, "asset_context", "context", default={}) or {}
            # live schema nests the quote under `context`; handle both
            inner = ctx if ("markPx" in ctx) else (_field(data, "context", default={}) or {})
            mark = _field(ctx, "markPx", default=None) or _field(inner, "markPx", default=None)
            prev = _field(ctx, "prevDayPx", default=None) or _field(inner, "prevDayPx", default=None)
            return (asset, _pct(mark, prev))
        except Exception:  # noqa
            return (asset, None)

    facts = {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            for a, chg in ex.map(one, assets):
                facts[a] = chg
    except Exception:  # noqa
        for item in assets:
            a, chg = one(item)
            facts[a] = chg
    # fold onto positions + tag alignment
    for s in strategies:
        for p in s.get("positions", []):
            chg = facts.get(p["asset"])
            if chg is None:
                continue
            p["market_24h_pct"] = chg
            # a short is "working" when the asset is down; a long when it's up
            working = (p["direction"] == "short" and chg < 0) or (p["direction"] == "long" and chg > 0)
            p["vs_market"] = "with the move" if working else "against the move"
    return facts


# ──────────────────────────────────────────────────────────────── taxonomy + signals
def compute(embedded, strategies, portfolio_totals):
    idle_strat = round(sum(_num(s.get("idle_withdrawable")) or 0.0 for s in strategies), 2)
    deployed = round(sum(_num(s.get("deployed")) or 0.0 for s in strategies), 2)
    idle_emb = embedded.get("idle_total") or 0.0
    strat_acct = round(sum(_num(s.get("account_value")) or 0.0 for s in strategies), 2)
    grand_total = round(idle_emb + strat_acct, 2)

    # exposure
    gross_long = gross_short = 0.0
    by_asset = {}
    upnl_total = 0.0
    largest = None
    for s in strategies:
        for p in s.get("positions", []):
            n = p["notional"]
            upnl_total += p["upnl"]
            if p["direction"] == "long":
                gross_long += n
            else:
                gross_short += n
            sign = n if p["direction"] == "long" else -n
            by_asset[p["asset"]] = round(by_asset.get(p["asset"], 0.0) + sign, 2)
            if largest is None or n > largest["notional"]:
                largest = {"asset": p["asset"], "notional": n, "strategy": s["name"]}

    totals = {
        "grand_total_usd": grand_total,
        "idle_in_embedded": round(idle_emb, 2),
        "idle_in_strategies": idle_strat,
        "deployed_in_positions": deployed,
        "strategy_account_value": strat_acct,
        "unrealized_pnl": round(upnl_total, 2),
        # cross-check against the (cached-bypassed) portfolio aggregate, if present
        "portfolio_total_balance_usd": portfolio_totals.get("total_balance_usd"),
        "portfolio_total_withdrawable": portfolio_totals.get("total_withdrawable"),
    }
    # reconciliation flag — surfaces silent drift between the two sources
    pbal = portfolio_totals.get("total_balance_usd")
    totals["reconciles"] = (pbal is None) or (abs(pbal - grand_total) <= max(2.0, 0.01 * grand_total))

    net = round(gross_long - gross_short, 2)
    exposure = {
        "net_notional_usd": net, "net_bias": ("long" if net > 0 else "short" if net < 0 else "flat"),
        "gross_long_usd": round(gross_long, 2), "gross_short_usd": round(gross_short, 2),
        "by_asset_net_usd": by_asset, "largest_position": largest,
    }
    working_cap = idle_emb + strat_acct
    signals = {
        "idle_drag_pct": round((idle_emb + idle_strat) / working_cap * 100, 1) if working_cap else None,
        "deployed_pct": round(deployed / working_cap * 100, 1) if working_cap else None,
        "largest_position_pct_of_deployed": round(largest["notional"] / (gross_long + gross_short) * 100, 1)
            if largest and (gross_long + gross_short) else None,
    }
    return totals, exposure, signals


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_market=True):
    meta = {"warnings": [], "real_time": True, "force_fetch": True}
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategies(client, meta)
    if want_market and strategies:
        enrich_market(client, strategies, meta)
    totals, exposure, signals = compute(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    return {
        "as_of": "live",
        "totals": totals,           # the three buckets — NEVER conflate them
        "embedded_wallet": embedded,
        "strategies": strategies,
        "exposure": exposure,
        "signals": signals,
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    for label, tool, kw in (("user_get_me", "user_get_me", {}),
                            ("account_get_portfolio", "account_get_portfolio", {"forceFetch": True}),
                            ("strategy_list", "strategy_list", {})):
        try:
            out[label] = client.mcp_call(tool, timeout=20, **kw)
        except Exception as e:  # noqa
            out[label] = {"error": str(e)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi portfolio engine (real-time wallet taxonomy + analysis)")
    ap.add_argument("--no-market", action="store_true", help="skip per-asset market enrichment")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"strategies": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"strategies": [], "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    if args.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    try:
        result = run(client, want_market=not args.no_market)
    except Exception as e:  # noqa
        print(json.dumps({"strategies": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
