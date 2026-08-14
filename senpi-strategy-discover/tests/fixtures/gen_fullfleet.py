#!/usr/bin/env python3
"""Build a SYNTHETIC v2-schema full-fleet catalog for aggressive matcher testing, from the REAL
origin/main catalog.json (75 strategies: real id/name/emoji/tagline/group/risk_level/min_budget, plus a
`thesis` slug on thesis-fund variants) ENRICHED with `thesis` + free-text `tags` sourced from
`senpi-trading-runtime/references/producer-patterns.md` (the per-family "When to use" prose + the
per-agent Tags column, §1-27 incl. the hedge-fund / tail-risk / all-weather / thesis families).

This is the DATA-MODEL materialization for the thesis+tags discovery work: every record carries a
one-line `thesis` (worldview / when-to-use — the LLM's semantic-match surface) + free-text `tags`
(no controlled vocab). NOT authoritative classification — good enough to stress-test the matcher.

Usage:  git show origin/main:catalog.json | python3 gen_fullfleet.py > catalog_fullfleet.json
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import re
import sys

# ── group -> (archetype, default sub_style, asset_scope) — narration labels only (NOT matched) ──
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
    "hedge-fund": ("structural_neutral", "multi_book", "basket"),
    "thesis-fund": ("structural_neutral", "thesis_preset", "basket"),
}

# ── catalog group -> producer-patterns family slug (fallback when an agent isn't in AGENT below) ──
GROUP_FAMILY = {
    "universe-trend-follower": "universe-trend-follower",
    "single-asset-alpha-hunter": "single-asset-alpha-hunter",
    "single-asset-hunter": "single-asset-alpha-hunter",
    "xyz-specialist": "single-asset-xyz-specialist",
    "multi-asset-whitelist": "multi-asset-whitelist",
    "trader-follower": "trader-follower-hot-streak",
    "striker-rank-jump": "striker-rank-jump-detector",
    "funding-fade": "funding-regime-fade",
    "contrarian-unwind": "contrarian-crowding-unwind",
    "cross-asset-lag": "cross-asset-lag-detector",
    "microstructure-order-flow": "microstructure-order-flow",
    "relative-value-pairs": "relative-value-pairs",
    "self-tuning": "self-tuning-adaptive-threshold",
    "regime-classifier": "regime-classifier-meta-router",
    "volume-engine": "volume-engine-market-making",
    "thesis-fund": "thesis-fund-preset",
    "hedge-fund": "event-driven-regime-rotation",  # generic hedge-fund fallback
}

# ── per-family "When to use this pattern" -> one-line thesis (worldview / who it's for) ──
FAMILY_THESIS = {
    "universe-trend-follower": "You want broad market coverage and entries only when multiple confirmations align across timeframes.",
    "single-asset-alpha-hunter": "You have a thesis on one specific asset and want scoring + exits tuned to that asset's behavior.",
    "single-asset-xyz-specialist": "You want to trade one non-crypto market — oil, gold, an index, or pre-IPO names — on the 24/7 XYZ venue.",
    "multi-asset-whitelist": "You want to restrict to a known basket of majors, cutting low-cap noise, and trade the best-scoring of them.",
    "trader-follower-hot-streak": "You'd rather copy proven alpha-generating traders than form your own technical view.",
    "striker-rank-jump-detector": "You want to catch the inflection when smart-money interest suddenly spikes on a previously-quiet asset.",
    "funding-regime-fade": "You believe persistent funding extremity precedes forced unwinds and want to fade the crowd at exhaustion.",
    "contrarian-crowding-unwind": "You believe crowded trades reliably unwind and want to fade the exhausted crowd once price stops following.",
    "cross-asset-lag-detector": "You believe BTC leads the alt market and want to systematically capture the laggard's catch-up.",
    "multi-asset-xyz-contrarian-fader": "You want a contrarian book on macro XYZ assets (commodities/indices/equities), fading over-extension.",
    "volume-engine-market-making": "You're not trading direction — you want to recycle builder fees and accrue volume credits.",
    "microstructure-order-flow": "You want an edge from microstructure — forced liquidations and order-book imbalance — not candle trend.",
    "relative-value-pairs": "You want to trade the spread between two tethered assets reverting, not guess absolute direction.",
    "meta-strategy-follower": "You trust the platform's aggregate of best strategies more than any one trader or your own read.",
    "self-tuning-adaptive-threshold": "You want an agent that auto-tunes its own entry bar from its own trade history.",
    "regime-classifier-meta-router": "You want one agent making a single macro call — trending up / down / sideways — and trading off it.",
    "volatility-breakout-expansion": "You want to harvest volatility expansion as a low-correlation, direction-agnostic return stream (convexity flavor).",
    "global-macro-cross-asset": "You want a low-correlation, regime-driven book across indices, metals, energy and FX — the global-macro lane, not crypto-only.",
    "thesis-fund-preset": "You have a strong view on the world — a war, the economy, one coin beating the rest — and want it as a one-tap, risk-managed bet.",
    "event-driven-regime-rotation": "You want a vehicle that adapts to a headline-driven, risk-on/off whipsaw — taking the prevailing side and flipping as it turns.",
    "tail-risk-crisis-alpha": "You want the portfolio hedge of a fund line-up — green on the days everything else is red — without having to hold a view.",
    "risk-parity-all-weather": "You want a diversified, lower-drawdown core to hold in any regime — balance over a directional view.",
    "equity-long-short-dispersion": "You want the classic equity hedge-fund play — long the relative-strength leaders, short the laggards, ~beta-neutral — on tokenized US stocks.",
    "ipo-new-listing": "You want the event alpha of new equity listings — riding pre-IPO ramps and the IPOP-to-equity conversion moment.",
    "two-speed-k-shaped": "You have a directional thematic view — one cohort booms, another suffers — and want it as a hedged cross-asset long/short.",
    "composite-allocation": "You want a proven fund's core plus a small higher-optionality satellite sleeve under one strategy, mixed by a funding dial.",
    "thesis-fund-family": "You have an opinionated macro divergence with a tradeable long AND short basket, expressed as a hedged long/short on the spread.",
}

# ── per-agent (family, free-text tags) extracted from producer-patterns.md §1-27 Tags column ──
# Tags are author free-text (no controlled vocab); lowercased + de-punctuated for the test fixture.
AGENT = {
    # §1 universe trend-follower
    "condor": ("universe-trend-follower", ["top-50", "multi-tf", "tick-180s"]),
    "cheetah": ("universe-trend-follower", ["top-100", "multi-signal", "quality-trader"]),
    "python": ("universe-trend-follower", ["multi-day", "mixed-sig", "min-score-8"]),
    "scorpion": ("universe-trend-follower", ["funding-backstop", "cooldown", "universe"]),
    # §2 single-asset alpha hunter
    "kodiak": ("single-asset-alpha-hunter", ["sol", "6-gate", "single-slot"]),
    "grizzly": ("single-asset-alpha-hunter", ["btc", "low-vol", "blue-chip"]),
    "polar": ("single-asset-alpha-hunter", ["eth", "6-gate", "single-slot"]),
    "wolverine": ("single-asset-alpha-hunter", ["hype", "high-vol", "sharp-moves"]),
    "koala": ("single-asset-alpha-hunter", ["onboarding", "hodl", "fire-once", "ultra-wide-dsl", "no-scoring"]),
    "beaver": ("single-asset-alpha-hunter", ["onboarding", "btc", "sm-gate", "tick-300s"]),
    "heron": ("single-asset-alpha-hunter", ["onboarding", "eth", "sm-gate", "tick-300s"]),
    "hummingbird": ("single-asset-alpha-hunter", ["onboarding", "hype", "sm-gate", "tick-300s"]),
    # §3 single-asset XYZ specialist
    "dire": ("single-asset-xyz-specialist", ["brentoil", "wide-dsl", "xyz"]),
    "lemur": ("single-asset-xyz-specialist", ["onboarding", "ipop", "auto-discover", "tick-900s", "24-7"]),
    "falcon": ("single-asset-xyz-specialist", ["ipop", "conversion-event", "momentum", "wide-dsl", "state-cache"]),
    # §4 multi-asset whitelist
    "bison": ("multi-asset-whitelist", ["whitelist", "best-of-n", "tick-300s"]),
    "hedgehog": ("multi-asset-whitelist", ["onboarding", "basket", "per-leg-dsl", "3-slot"]),
    "hawk": ("multi-asset-whitelist", ["onboarding", "breakout", "tight-dsl", "hard-timeout-24h"]),
    "salamander": ("multi-asset-whitelist", ["onboarding", "pullback", "asymmetric-dsl"]),
    "bobcat": ("multi-asset-whitelist", ["onboarding", "xyz-big-tech", "hard-timeout-48h"]),
    "raccoon": ("multi-asset-whitelist", ["onboarding", "xyz-weekend", "reconciliation", "time-gated"]),
    "tortoise": ("multi-asset-whitelist", ["onboarding", "dca", "time-trigger", "no-prediction", "wide-dsl"]),
    "sheep": ("multi-asset-whitelist", ["onboarding", "long-only", "multi-timeframe", "ema-stack", "balanced-dsl"]),
    "iguana": ("multi-asset-whitelist", ["onboarding", "xyz-macro", "index-only", "balanced-dsl", "hard-timeout-48h"]),
    "sailfish": ("multi-asset-whitelist", ["momentum-rotation", "rs-leader", "margin-gate", "balanced-dsl"]),
    "stag": ("multi-asset-whitelist", ["parabolic", "5-gate", "long-only", "operator-driven", "parabolic-runner-dsl"]),
    "spider": ("multi-asset-whitelist", ["two-leg", "hedge-fund", "ai-tech-long", "macro-scalp", "both-direction", "dynamic-universe"]),
    # §5 trader-follower
    "raptor": ("trader-follower-hot-streak", ["coat-tail", "24h-cache", "tick-60-180s"]),
    "jackal": ("trader-follower-hot-streak", ["new-entry", "ta", "funding"]),
    "albatross": ("trader-follower-hot-streak", ["onboarding", "arena", "multi-week", "conviction-weighted", "user-scope-auth-required"]),
    "remora": ("trader-follower-hot-streak", ["whale-mirror", "consensus", "operator-picked", "wide-dsl"]),
    # §6 striker / rank-jump
    "jaguar": ("striker-rank-jump-detector", ["rank-jump", "1-per-day", "3m-notional"]),
    "roach": ("striker-rank-jump-detector", ["striker", "first-jump", "volume-floor"]),
    "roach-b": ("striker-rank-jump-detector", ["striker", "roach-pattern", "multi-wallet"]),
    "orca": ("striker-rank-jump-detector", ["striker", "vanilla-gen-1", "first-jump"]),
    "meerkat": ("striker-rank-jump-detector", ["momentum-event", "tier-sniper", "freshness-gate", "wide-dsl", "tick-120s"]),
    # §7 funding-regime fade
    "pangolin": ("funding-regime-fade", ["funding-fade", "tick-300s", "quiet-hours"]),
    "dog": ("funding-regime-fade", ["4-coin", "hard-gate", "regime"]),
    "vulture": ("funding-regime-fade", ["small-cap", "long-tail", "conviction-scaled", "min-score-9"]),
    # §8 contrarian crowding-unwind
    "owl": ("contrarian-crowding-unwind", ["crowding", "tick-900s", "6h-cooldown"]),
    "lemon": ("contrarian-crowding-unwind", ["degen-fader", "macro-gate", "min-score-9"]),
    # §9 cross-asset lag
    "mantis": ("cross-asset-lag-detector", ["btc-led", "follow-rate", "tick-60s"]),
    "osprey": ("cross-asset-lag-detector", ["cross-venue", "xyz-equity", "beta-gap", "wide-dsl", "self-computed"]),
    # §10 multi-asset XYZ contrarian fader
    "bald-eagle": ("multi-asset-xyz-contrarian-fader", ["xyz-macro", "stale-cancel", "tick-300s", "contrarian"]),
    "kestrel": ("multi-asset-xyz-contrarian-fader", ["xyz-macro", "funding-align", "13-xyz-assets"]),
    # §11 volume engine
    # §12 microstructure
    "piranha": ("microstructure-order-flow", ["oi-velocity", "order-book", "forced-flow"]),
    "marlin": ("microstructure-order-flow", ["order-book", "imbalance", "momentum"]),
    # §13 relative-value / pairs
    "chameleon": ("relative-value-pairs", ["ratio-z-score", "pairs", "mean-reversion", "market-neutral"]),
    # §14 meta-strategy
    "cuckoo": ("meta-strategy-follower", ["meta-follower", "copy-the-copiers", "consensus", "performance-weighted", "user-scope-auth-required"]),
    # §15 self-tuning
    "lynx": ("self-tuning-adaptive-threshold", ["self-tuning", "adaptive-min-score", "audit-cron", "rl-on-threshold", "user-scope-auth-required"]),
    # §16 regime classifier
    "coyote": ("regime-classifier-meta-router", ["regime-classifier", "meta-router", "macro", "vol-aware", "user-scope-auth-required"]),
    # §17 volatility / breakout-expansion
    "caracal": ("volatility-breakout-expansion", ["volatility", "compression-expansion", "both-direction", "two-universe", "episodic", "two-wallet"]),
    # §18 global macro
    "elephant": ("global-macro-cross-asset", ["global-macro", "cross-asset", "trend-fade", "both-direction", "two-wallet", "24-7"]),
    # §20 event-driven / regime rotation
    "wolf": ("event-driven-regime-rotation", ["hedge-fund", "event-driven", "regime-rotation", "shared-brain", "adaptive", "two-wallet"]),
    # §21 tail-risk / crisis-alpha
    "rhino": ("tail-risk-crisis-alpha", ["hedge-fund", "tail-risk", "crisis-alpha", "convexity", "shared-brain", "stress-gated", "two-wallet"]),
    # §22 risk parity / all-weather
    "ox": ("risk-parity-all-weather", ["hedge-fund", "risk-parity", "all-weather", "inverse-vol-sizing", "core-holding", "two-wallet"]),
    # §23 equity long/short
    "cougar": ("equity-long-short-dispersion", ["hedge-fund", "equity-long-short", "dispersion", "market-neutral", "xyz-equities", "two-wallet"]),
    # §24 IPO / new-listing
    "magpie": ("ipo-new-listing", ["hedge-fund", "event-driven", "ipo", "ipop", "conversion-detection", "two-wallet"]),
    # §25 two-speed / K-shaped
    "lion": ("two-speed-k-shaped", ["hedge-fund", "two-speed-market", "k-shaped", "thematic-long-short", "cross-asset", "conviction-sizing", "ai", "hype", "two-wallet"]),
    # §26 composite allocation
    "cub": ("composite-allocation", ["hedge-fund", "composite-allocation", "lion-variation", "pre-ipo", "ipop", "satellite-sleeve", "three-wallet"]),
    # §27 thesis-fund family
    "eel": ("thesis-fund-family", ["hedge-fund", "thesis-fund", "energy", "ai-power", "same-sector-pair", "two-wallet"]),
    "mongoose": ("thesis-fund-family", ["hedge-fund", "thesis-fund", "on-chain-finance", "stablecoins", "tokenization", "two-wallet"]),
    "boar": ("thesis-fund-family", ["hedge-fund", "thesis-fund", "debasement", "hard-money", "gold", "btc", "two-wallet"]),
}

# ── asset-class detection: UNION every class a tagline touches (macro/hedge funds are multi-class) ──
CLASS_KEYWORDS = {
    "btc_eth": [r"\bbtc\b", r"\bbitcoin\b", r"\beth\b", r"\bethereum\b"],
    "major_alts": [r"\bsol\b", r"\bhype\b", r"\balt", r"\bavax\b", r"\blink\b", r"\bdoge\b", r"\bsui\b",
                   r"\bondo\b", r"\bxrp\b", r"\bada\b", r"\bnear\b", r"\bapt\b", r"crypto majors", r"\bmajors\b"],
    "universe_crypto": [r"universe", r"top-50", r"top-100", r"cross-section", r"every liquid", r"\bscan"],
    "xyz_equities": [r"\bnvda\b", r"\btsla\b", r"\baapl\b", r"equit", r"\bstock", r"big-tech", r"big tech",
                     r"tokenized", r"ai complex", r"ai-power", r"datacenter"],
    "commodities": [r"\boil\b", r"\bgold\b", r"\bsilver\b", r"metal", r"copper", r"natgas", r"brentoil",
                    r"platinum", r"palladium", r"uranium", r"commodit", r"energy", r"hard money", r"\bcl\b", r"\bfx\b"],
    "indices": [r"sp500", r"sp 500", r"xyz100", r"\bindex\b", r"indices", r"us indices", r"growth indices"],
    "pre_ipo": [r"pre-ipo", r"pre ipo", r"\bipop", r"spacex", r"\bipo\b", r"new-listing", r"new listing"],
}


def detect_asset_classes(tagline, group, scope):
    tag = (tagline or "").lower()
    out = [cls for cls, pats in CLASS_KEYWORDS.items() if any(re.search(p, tag) for p in pats)]
    if out:
        return out
    # fallbacks when the tagline names nothing concrete
    if "xyz" in tag or "macro asset" in tag:   # an XYZ macro book that didn't name a ticker -> xyz domain
        return ["indices", "commodities", "xyz_equities"]
    if group == "trader-follower":
        return ["none"]
    if scope == "universe":
        return ["btc_eth", "major_alts", "universe_crypto"]
    if scope == "single":
        return ["btc_eth"] if re.search(r"\b(btc|eth|bitcoin|ethereum)\b", tag) else ["major_alts"]
    return ["btc_eth", "major_alts"]


KNOWN_TICKERS = ["BTC", "ETH", "SOL", "HYPE", "SUI", "ONDO", "AVAX", "LINK", "DOGE", "ARB",
                 "NVDA", "TSLA", "AAPL", "META", "MSFT", "GOOGL", "AMZN", "AMD", "MU", "INTC",
                 "TSM", "ORCL", "BRENTOIL", "GOLD", "SILVER", "COPPER", "NATGAS", "SP500", "XYZ100", "SPCX"]
XYZ_TICKERS = {"NVDA", "TSLA", "AAPL", "META", "MSFT", "GOOGL", "AMZN", "AMD", "MU", "INTC", "TSM",
               "ORCL", "BRENTOIL", "GOLD", "SILVER", "COPPER", "NATGAS", "SP500", "XYZ100", "SPCX"}


def derive_tags(e, family):
    """Free-text tags: producer-patterns Tags if the agent is catalogued there; else group/tagline-derived."""
    sid, group, tag = e["id"], e.get("group"), (e.get("tagline") or "").lower()
    if sid in AGENT:
        tags = list(AGENT[sid][1])
    else:
        tags = []
        if group == "thesis-fund":
            tags = ["thesis-fund", "macro", "view-based"]
            if e.get("thesis"):
                tags += [w for w in str(e["thesis"]).split("_") if w]
        elif group == "hedge-fund":
            tags = ["hedge-fund", "two-wallet"]
        # tagline keyword sprinkles for the long tail
        for kw, t in (("breakout", "breakout"), ("oi ", "oi-velocity"), ("open interest", "oi-velocity"),
                      ("market-neutral", "market-neutral"), ("dispersion", "dispersion"),
                      ("funding", "funding"), ("dca", "dca"), ("fade", "contrarian")):
            if kw in tag and t not in tags:
                tags.append(t)
        if not tags:
            tags = [(group or "strategy").replace("_", "-")]
    return tags


# the §17-27 hedge-fund families whose FAMILY_THESIS genuinely fits a hedge-fund agent
HEDGE_FAMILIES = {
    "volatility-breakout-expansion", "global-macro-cross-asset", "event-driven-regime-rotation",
    "tail-risk-crisis-alpha", "risk-parity-all-weather", "equity-long-short-dispersion",
    "ipo-new-listing", "two-speed-k-shaped", "composite-allocation", "thesis-fund-family",
}


def _tagline_thesis(e, n=2):
    parts = re.split(r"(?<=[.!])\s", (e.get("tagline") or "").strip())
    return " ".join(parts[:n])[:200] or e.get("name")


def derive_thesis(e, family):
    """One-line worldview / when-to-use. thesis-fund variants keep their precise tagline; hedge-fund
    agents use their family's 'when to use' only when that family genuinely fits (else their tagline —
    catches Spider, catalogued under §4 'whitelist', and Octopus/Camel, absent from the tables)."""
    group = e.get("group")
    if group == "thesis-fund":
        return (e.get("tagline") or "").strip()
    if group == "hedge-fund":
        sid = e["id"]
        if sid in AGENT and AGENT[sid][0] in HEDGE_FAMILIES:
            return FAMILY_THESIS[AGENT[sid][0]]
        return _tagline_thesis(e)
    if family in FAMILY_THESIS:
        return FAMILY_THESIS[family]
    return _tagline_thesis(e, 1)


def classify(e):
    tag = (e.get("tagline") or "").lower()
    group = e.get("group")
    archetype, sub_style, scope = GROUP.get(group, ("trend_following", "basket", "basket"))
    direction = "long_short"

    # tagline keyword overrides for the heterogeneous single-market cases
    if any(k in tag for k in ("pre-ipo", "ipop", "pre ipo", "spacex")):
        archetype, sub_style, scope = "single_market", "pre_ipo", "single"
    elif any(k in tag for k in ("sp500", "xyz100", "index", "indices", "sp 500")) and group != "hedge-fund":
        archetype, sub_style, scope = "single_market", "broad_index", "basket"
    elif any(k in tag for k in ("nvda", "tsla", "big-tech", "big tech")) and group != "hedge-fund":
        archetype, sub_style, scope = "single_market", "big_tech", "basket"
    elif any(k in tag for k in ("brentoil", "oil", "gold", "silver", "commodit", "metal")) and group not in ("hedge-fund", "thesis-fund"):
        archetype, sub_style, scope = "single_market", "commodity", "single"
    elif "weekend" in tag:
        archetype, sub_style, scope = "single_market", "weekend_gap", "basket"
    elif any(k in tag for k in ("dca", "fixed %", "fixed percent", "no prediction", "slow and steady")):
        archetype, sub_style, scope = "structural_neutral", "dca", "basket"
        direction = "long_only"

    if any(k in tag for k in ("long-only", "long only", "never short", "never shorts")):
        direction = "long_only"

    family = AGENT[e["id"]][0] if e["id"] in AGENT else GROUP_FAMILY.get(group, "multi-asset-whitelist")
    asset_classes = detect_asset_classes(e.get("tagline"), group, scope)

    # named assets from tagline tickers
    assets, up = [], (e.get("tagline") or "").upper()
    for t in KNOWN_TICKERS:
        if re.search(r"\b" + re.escape(t) + r"\b", up) and t not in assets:
            assets.append(("xyz:" + t) if t in XYZ_TICKERS else t)

    tier = "starter" if ("onboarding" in tag or e.get("risk_level") == "conservative") else "advanced"
    lev = {"conservative": 3, "moderate": 5, "aggressive": 10}.get(e.get("risk_level"), 5)
    horizon = "scalp" if sub_style in ("orderbook_pressure", "rank_jump", "momentum_event") else \
              ("hodl" if sub_style in ("hodl", "dca") else "swing")
    cadence = 60 if horizon == "scalp" else (86400 if horizon == "hodl" else 300)
    belief_plain = re.split(r"(?<=[.!])\s", e.get("tagline") or "")[0][:140] or e.get("name")

    return {
        "id": e["id"], "name": e["name"], "emoji": e.get("emoji"), "tagline": e.get("tagline"),
        "belief_plain": belief_plain, "version": e.get("version"), "group": group,
        # thesis + free-text tags (the discovery worldview/theme surface)
        "thesis": derive_thesis(e, family), "tags": derive_tags(e, family),
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
           "_note": "SYNTHETIC v2 catalog for aggressive matcher testing — built from origin/main "
                    "catalog.json (75 strategies) ENRICHED with thesis + free-text tags from "
                    "producer-patterns.md (§1-27). Discovery fields inferred from group/tagline/family, "
                    "NOT authoritative classification.",
           "skills": [classify(e) for e in items]}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
