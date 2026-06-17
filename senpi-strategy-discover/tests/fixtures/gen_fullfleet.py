#!/usr/bin/env python3
"""Build a SYNTHETIC v2-schema full-fleet catalog for aggressive matcher testing, from the REAL
main-branch catalog.json (which has real id/name/emoji/tagline/group/risk_level/min_budget but none of
the v2 discovery fields). Discovery fields are inferred from `group` + tagline keywords — good enough to
stress-test the matcher across a realistic fleet; NOT authoritative classification.

Usage:  git show main:catalog.json | python3 gen_fullfleet.py > catalog_fullfleet.json
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import re
import sys

# group -> (archetype, default sub_style, asset_scope)
GROUP = {
    "contrarian-unwind": ("contrarian_fade", "sm_crowding", "universe"),
    "funding-fade": ("contrarian_fade", "funding_extreme", "universe"),
    "cross-asset-lag": ("structural_neutral", "cross_asset_lag", "single"),
    "microstructure-order-flow": ("breakout_momentum", "orderbook_pressure", "universe"),
    "multi-asset-whitelist": ("trend_following", "basket", "basket"),
    "regime-classifier": ("structural_neutral", "adaptive", "universe"),
    "relative-value-pairs": ("structural_neutral", "pairs_rv", "single"),
    "self-tuning": ("trend_following", "adaptive", "universe"),
    "single-asset-alpha-hunter": ("trend_following", "alpha_hunter", "single"),
    "single-asset-hunter": ("trend_following", "hodl", "single"),
    "striker-rank-jump": ("breakout_momentum", "rank_jump", "universe"),
    "trader-follower": ("copy_trading", "arena_winners", "follows_traders"),
    "universe-trend-follower": ("trend_following", "rotation", "universe"),
    "volume-engine": ("structural_neutral", "market_making", "universe"),
    "xyz-specialist": ("single_market", "commodity", "single"),
}

KNOWN_TICKERS = ["BTC", "ETH", "SOL", "HYPE", "SUI", "ONDO", "AVAX", "LINK", "DOGE", "ARB",
                 "NVDA", "TSLA", "AAPL", "META", "MSFT", "GOOGL", "AMZN", "AMD", "MU", "INTC",
                 "TSM", "ORCL", "BRENTOIL", "GOLD", "SILVER", "SP500", "XYZ100", "SPCX"]
XYZ_TICKERS = {"NVDA", "TSLA", "AAPL", "META", "MSFT", "GOOGL", "AMZN", "AMD", "MU", "INTC",
               "TSM", "ORCL", "BRENTOIL", "GOLD", "SILVER", "SP500", "XYZ100", "SPCX"}


def classify(e):
    tag = (e.get("tagline") or "").lower()
    group = e.get("group")
    archetype, sub_style, scope = GROUP.get(group, ("trend_following", "basket", "basket"))
    asset_classes = None
    direction = "long_short"

    # tagline keyword overrides (the heterogeneous cases)
    if any(k in tag for k in ("pre-ipo", "ipop", "pre ipo", "spacex")):
        archetype, sub_style, asset_classes, scope = "single_market", "pre_ipo", ["pre_ipo"], "single"
    elif any(k in tag for k in ("sp500", "xyz100", "index", "indices", "sp 500")):
        archetype, sub_style, asset_classes, scope = "single_market", "broad_index", ["indices"], "basket"
    elif any(k in tag for k in ("nvda", "tsla", "big-tech", "big tech", "stock", "equit")):
        archetype, sub_style, asset_classes, scope = "single_market", "big_tech", ["xyz_equities"], "basket"
    elif any(k in tag for k in ("brentoil", "oil", "gold", "silver", "commodit", "metal")):
        archetype, sub_style, asset_classes, scope = "single_market", "commodity", ["commodities"], "single"
    elif "weekend" in tag:
        archetype, sub_style, asset_classes, scope = "single_market", "weekend_gap", ["xyz_equities"], "basket"
    elif any(k in tag for k in ("dca", "fixed %", "fixed percent", "no prediction", "cadence", "slow and steady")):
        archetype, sub_style, scope = "structural_neutral", "dca", "basket"
        direction = "long_only"

    if any(k in tag for k in ("long-only", "long only", "never short", "never shorts")):
        direction = "long_only"

    # asset_classes default if not set by tagline
    if asset_classes is None:
        if group == "trader-follower":
            asset_classes = ["none"]
        elif scope in ("universe",):
            asset_classes = ["btc_eth", "major_alts", "universe_crypto"]
        elif scope == "single":
            asset_classes = ["btc_eth"] if re.search(r"\b(btc|eth|bitcoin|ethereum)\b", tag) else ["major_alts"]
        else:  # basket
            asset_classes = ["btc_eth", "major_alts"]

    # assets from tagline tickers
    assets = []
    up = (e.get("tagline") or "").upper()
    for t in KNOWN_TICKERS:
        if re.search(r"\b" + re.escape(t) + r"\b", up) and t not in assets:
            assets.append(("xyz:" + t) if t in XYZ_TICKERS else t)

    # tier: onboarding-tagged or conservative -> starter
    tier = "starter" if ("onboarding" in tag or e.get("risk_level") == "conservative") else "advanced"

    # synthetic mechanicals
    lev = {"conservative": 3, "moderate": 5, "aggressive": 10}.get(e.get("risk_level"), 5)
    horizon = "scalp" if sub_style in ("orderbook_pressure", "rank_jump", "momentum_event") else \
              ("hodl" if sub_style in ("hodl", "dca") else "swing")
    cadence = 60 if horizon == "scalp" else (86400 if horizon == "hodl" else 300)
    belief_plain = re.split(r"(?<=[.!])\s", e.get("tagline") or "")[0][:140] or e.get("name")

    return {
        "id": e["id"], "name": e["name"], "emoji": e.get("emoji"), "tagline": e.get("tagline"),
        "belief_plain": belief_plain, "version": e.get("version"), "group": group,
        "archetype": archetype, "archetype_label": archetype.replace("_", " ").title(),
        "sub_style": sub_style, "sub_style_label": (sub_style or "").replace("_", " ").title(),
        "asset_classes": asset_classes, "asset_scope": scope, "assets": assets, "direction": direction,
        "risk_level": e.get("risk_level"), "tier": tier, "leverage_max": lev,
        "time_horizon": horizon, "cadence_seconds": cadence,
        "min_budget": e.get("min_budget", 100), "instance_count": 1, "funding_split": [1.0],
        "max_slots": 3, "sort_order": e.get("sort_order", 1), "branch": "main-synthetic",
    }


def main():
    src = json.load(sys.stdin)
    items = src.get("skills") or src.get("strategies") or []
    out = {"_version": "2.1", "_generated": True,
           "_note": "SYNTHETIC v2 catalog for aggressive matcher testing — discovery fields inferred "
                    "from main-branch group+tagline, NOT authoritative.",
           "skills": [classify(e) for e in items]}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
