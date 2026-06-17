#!/usr/bin/env python3
"""senpi-strategy-discover engine — Data Layer + Matcher (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool with discrete flags; it reads the JSON on
stdout and narrates 2-3 cards. The script fetches data + matches; the LLM converses + selects.

  python3 discover.py --risk conservative --assets btc_eth --budget 300

Contract: see docs/strategy-discover/discovery-architecture.md.
- rejects only the impossible (cross-domain asset, named-asset unavailable, strict-opposite direction,
  explicit exclusions); coarse-ranks the rest by a flat +1 relevance count; returns the top-N.
- fails open: unknown values drop to "unstated" (widen, never dead-end); always emits valid JSON;
  exit 0 for handled cases.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_CATALOG = os.path.join(REPO_ROOT, "catalog.json")
RUNTIME_DIR = os.path.join(REPO_ROOT, "senpi-trading-runtime")

# ---------------------------------------------------------------- vocabulary
RISK_ORDER = ["conservative", "moderate", "aggressive"]
HORIZON_ORDER = ["scalp", "swing", "position", "hodl"]
CLASS_TAGS = {"btc_eth", "major_alts", "universe_crypto", "xyz_equities", "commodities", "indices", "pre_ipo"}
CRYPTO_CLASSES = {"btc_eth", "major_alts", "universe_crypto"}
XYZ_CLASSES = {"xyz_equities", "commodities", "indices", "pre_ipo"}
BELIEF_TO_ARCHETYPE = {
    "trend": "trend_following", "contrarian": "contrarian_fade", "copy": "copy_trading",
    "breakout": "breakout_momentum", "structural": "structural_neutral", "single_market": "single_market",
}
ARCHETYPES = set(BELIEF_TO_ARCHETYPE.values())
ARCH_ADJACENT = {"trend_following": {"breakout_momentum"}, "breakout_momentum": {"trend_following"}}
ARCH_OPPOSITE = {
    "trend_following": {"contrarian_fade"}, "breakout_momentum": {"contrarian_fade"},
    "contrarian_fade": {"trend_following", "breakout_momentum"},
}

# Synonym maps (lowercased NL -> canonical). Forgiving; unmapped -> unstated.
RISK_SYN = {**{v: v for v in RISK_ORDER},
            "safe": "conservative", "cautious": "conservative", "low": "conservative",
            "low-risk": "conservative", "careful": "conservative", "steady": "conservative",
            "slow": "conservative", "defensive": "conservative", "conservatively": "conservative",
            "balanced": "moderate", "medium": "moderate", "normal": "moderate", "middle": "moderate", "mid": "moderate",
            "high": "aggressive", "risky": "aggressive", "yolo": "aggressive", "big": "aggressive",
            "degen": "aggressive", "high-risk": "aggressive"}
BELIEF_SYN = {**{v: v for v in BELIEF_TO_ARCHETYPE},
              "ride": "trend", "riding": "trend", "momentum": "trend", "trending": "trend", "follow": "trend",
              "fade": "contrarian", "reversal": "contrarian", "reversion": "contrarian",
              "mean-reversion": "contrarian", "against": "contrarian", "dip": "contrarian",
              "mirror": "copy", "follow-traders": "copy", "copytrade": "copy", "copy-trading": "copy",
              "break": "breakout", "breakouts": "breakout", "jump": "breakout", "explosive": "breakout",
              "neutral": "structural", "market-neutral": "structural", "pairs": "structural", "spread": "structural"}
HORIZON_SYN = {**{v: v for v in HORIZON_ORDER},
               "quick": "scalp", "fast": "scalp", "small": "scalp", "short-term": "scalp", "short": "scalp",
               "swings": "swing", "days": "swing",
               "hold": "position", "long-term": "position", "longterm": "position",
               "set-and-forget": "hodl", "forever": "hodl"}
DIRECTION_SYN = {"long_only": "long_only", "long-only": "long_only", "longonly": "long_only", "long": "long_only",
                 "no-short": "long_only", "no-shorting": "long_only", "longs": "long_only",
                 "short_only": "short_only", "short-only": "short_only", "shorts": "short_only", "short": "short_only",
                 "any": "any", "both": "any"}
ASSET_SYN = {**{t: t for t in CLASS_TAGS},
             "btc": "btc_eth", "eth": "btc_eth", "bitcoin": "btc_eth", "ethereum": "btc_eth", "btceth": "btc_eth",
             "alts": "major_alts", "altcoins": "major_alts", "alt": "major_alts", "majors": "major_alts",
             "everything": "universe_crypto", "scan": "universe_crypto", "all": "universe_crypto", "universe": "universe_crypto",
             "stocks": "xyz_equities", "stock": "xyz_equities", "equities": "xyz_equities", "equity": "xyz_equities", "tech": "xyz_equities",
             "oil": "commodities", "gold": "commodities", "silver": "commodities", "metals": "commodities", "commodity": "commodities",
             "index": "indices", "sp500": "indices", "nasdaq": "indices",
             "pre-ipo": "pre_ipo", "preipo": "pre_ipo", "ipo": "pre_ipo", "spacex": "pre_ipo"}
# exclude token -> (dimension, canonical value)
EXCLUDE_SYN = {
    "copy": ("archetype", "copy_trading"), "copy_trading": ("archetype", "copy_trading"),
    "copy-trading": ("archetype", "copy_trading"), "mirror": ("archetype", "copy_trading"),
    "stocks": ("asset_class", "xyz_equities"), "equities": ("asset_class", "xyz_equities"),
    "crypto": ("asset_class", "__crypto__"), "commodities": ("asset_class", "commodities"),
    "oil": ("asset_class", "commodities"), "pre-ipo": ("asset_class", "pre_ipo"), "pre_ipo": ("asset_class", "pre_ipo"),
    "dca": ("sub_style", "dca"), "shorting": ("direction", "no_short"), "shorts": ("direction", "no_short"),
}

GOAL_SYN = {"accumulate": "dca", "accumulating": "dca", "dca": "dca"}
SCOPE_SYN = {"single": "single", "basket": "basket", "universe": "universe", "scan": "universe",
             "specific": "single", "one": "single"}


# ---------------------------------------------------------------- normalizer
def _canon(value, synmap, warnings, field):
    if value is None:
        return None
    key = str(value).strip().lower()
    for cand in (key, key.replace(" ", "-"), key.replace(" ", "_"), key.replace(" ", "")):
        if cand in synmap:
            canon = synmap[cand]
            if canon != key:
                warnings.append(f"normalized {field} '{value}' -> '{canon}'")
            return canon
    warnings.append(f"dropped {field}='{value}' (unrecognized) -> unstated")
    return None


def _parse_budget(value, warnings):
    if value is None:
        return None
    s = str(value).lower().replace(",", "").replace("$", "").replace("~", "")
    s = s.replace("around", "").replace("about", "").strip()
    nums = []
    for tok in re.finditer(r"(\d+(?:\.\d+)?)(\s*[kK])?", s):
        n = float(tok.group(1))
        if tok.group(2):
            n *= 1000
        nums.append(n)
    if not nums:
        warnings.append(f"dropped budget='{value}' (no number) -> unstated")
        return None
    if len(nums) >= 2 and "-" in s:   # a range like "500-2000" -> midpoint
        return (nums[0] + nums[1]) / 2.0
    return nums[0]


def _norm_assets(raw, warnings):
    """Split csv; each token -> ('class', tag) or ('named', TICKER)."""
    out = []
    if not raw:
        return out
    for tok in str(raw).split(","):
        t = tok.strip()
        if not t:
            continue
        low = t.lower()
        if low in ASSET_SYN:
            cls = ASSET_SYN[low]
            if ("class", cls) not in out:
                out.append(("class", cls))
        else:
            named = t.upper()
            if ("named", named) not in out:
                out.append(("named", named))
    return out


def _norm_exclude(raw, warnings):
    out = []
    if not raw:
        return out
    for tok in str(raw).split(","):
        t = tok.strip().lower()
        if not t:
            continue
        if t in EXCLUDE_SYN:
            pair = EXCLUDE_SYN[t]
            if pair not in out:
                out.append(pair)
        else:
            warnings.append(f"dropped exclude='{tok.strip()}' (unrecognized)")
    return out


def normalize_intent(args):
    w = []
    intent = {
        "risk": _canon(getattr(args, "risk", None), RISK_SYN, w, "risk"),
        "belief": _canon(getattr(args, "belief", None), BELIEF_SYN, w, "belief"),
        "horizon": _canon(getattr(args, "horizon", None), HORIZON_SYN, w, "horizon"),
        "direction": _canon(getattr(args, "direction", None), DIRECTION_SYN, w, "direction"),
        "market_scope": _canon(getattr(args, "market_scope", None), SCOPE_SYN, w, "market_scope"),
        "goal": _canon(getattr(args, "goal", None), GOAL_SYN, w, "goal"),
        "experience": (getattr(args, "experience", None) or None),
        "budget": _parse_budget(getattr(args, "budget", None), w),
        "assets": _norm_assets(getattr(args, "assets", None), w),
        "exclude": _norm_exclude(getattr(args, "exclude", None), w),
    }
    if intent["direction"] == "any":
        intent["direction"] = None
    if intent["experience"] not in (None, "new", "experienced"):
        w.append(f"dropped experience='{intent['experience']}'")
        intent["experience"] = None
    intent["_warnings"] = w
    return intent


# ---------------------------------------------------------------- helpers
def domain_of(classes):
    d = set()
    if any(c in CRYPTO_CLASSES for c in classes):
        d.add("crypto")
    if any(c in XYZ_CLASSES for c in classes):
        d.add("xyz")
    return d


def asset_matches(named, assets):
    nu = named.upper().replace("XYZ:", "")
    for a in (assets or []):
        au = str(a).upper()
        if au == nu or au == "XYZ:" + nu or au.split(":")[-1] == nu:
            return True
    return False


def adjacent_risk(a, b):
    if a in RISK_ORDER and b in RISK_ORDER:
        return abs(RISK_ORDER.index(a) - RISK_ORDER.index(b)) == 1
    return False


def adjacent_horizon(a, b):
    if a in HORIZON_ORDER and b in HORIZON_ORDER:
        return abs(HORIZON_ORDER.index(a) - HORIZON_ORDER.index(b)) == 1
    return False


def infer_class_for_named(named):
    n = named.upper().replace("XYZ:", "")
    if named.upper().startswith("XYZ:"):
        return None  # xyz domain but sub-class unknown; broaden = no class constraint
    if n in ("BTC", "ETH"):
        return "btc_eth"
    return "major_alts"


# ---------------------------------------------------------------- matcher (pure)
def _hard_reject(r, intent):
    user_classes = [v for k, v in intent["assets"] if k == "class"]
    if user_classes:
        ud, sd = domain_of(user_classes), domain_of(r.get("asset_classes") or [])
        if ud and sd and ud.isdisjoint(sd):
            return True
    named = [v for k, v in intent["assets"] if k == "named"]
    if named and not any(asset_matches(n, r.get("assets")) for n in named):
        return True
    d = intent["direction"]
    if d == "long_only" and r.get("direction") == "short_only":
        return True
    if d == "short_only" and r.get("direction") == "long_only":
        return True
    for dim, val in intent["exclude"]:
        if dim == "archetype" and r.get("archetype") == val:
            return True
        if dim == "asset_class":
            acs = r.get("asset_classes") or []
            if val == "__crypto__" and (set(acs) & CRYPTO_CLASSES):
                return True
            if val in acs:
                return True
        if dim == "sub_style" and r.get("sub_style") == val:
            return True
        if dim == "direction" and val == "no_short" and r.get("direction") in ("short_only", "long_short"):
            return True
    return False


def _score(r, intent):
    rel, reasons, caveats = 0, [], []
    user_classes = [v for k, v in intent["assets"] if k == "class"]
    named = [v for k, v in intent["assets"] if k == "named"]

    if intent["risk"] and r.get("risk_level"):
        if r["risk_level"] == intent["risk"]:
            rel += 1
            reasons.append({"dim": "risk", "value": r["risk_level"], "tolerant": False})
        elif adjacent_risk(r["risk_level"], intent["risk"]):
            rel += 1
            reasons.append({"dim": "risk", "value": r["risk_level"], "tolerant": True})
            more = RISK_ORDER.index(r["risk_level"]) > RISK_ORDER.index(intent["risk"])
            caveats.append("A notch more aggressive than you asked — the closest fit." if more
                           else "A notch more conservative than you asked — the closest fit.")

    if user_classes:
        acs = set(r.get("asset_classes") or [])
        if set(user_classes) & acs:
            rel += 1
            reasons.append({"dim": "asset", "value": sorted(set(user_classes) & acs), "tolerant": False})
        if "btc_eth" in user_classes and "btc_eth" not in acs and (acs & CRYPTO_CLASSES):
            caveats.append("Trades alts beyond just BTC/ETH.")
    for c in intent.get("_broadened_classes", []):
        if c in (r.get("asset_classes") or []):
            rel += 1
            reasons.append({"dim": "asset", "value": c, "tolerant": True})
            break
    if named and any(asset_matches(n, r.get("assets")) for n in named):
        rel += 1
        reasons.append({"dim": "asset", "value": named, "tolerant": False})

    if intent["belief"]:
        a, b = r.get("archetype"), BELIEF_TO_ARCHETYPE[intent["belief"]]
        if a == b:
            rel += 1
            reasons.append({"dim": "belief", "value": a, "tolerant": False})
        elif b in ARCH_ADJACENT.get(a, set()):
            rel += 1
            reasons.append({"dim": "belief", "value": a, "tolerant": True})
        elif b in ARCH_OPPOSITE.get(a, set()):
            rel -= 1

    if intent["direction"] in ("long_only", "short_only"):
        if r.get("direction") == intent["direction"]:
            rel += 1
            reasons.append({"dim": "direction", "value": r["direction"], "tolerant": False})
        elif r.get("direction") == "long_short" and intent["direction"] == "long_only":
            caveats.append("Can also take short positions.")

    if intent["horizon"] and r.get("time_horizon"):
        if r["time_horizon"] == intent["horizon"]:
            rel += 1
            reasons.append({"dim": "horizon", "value": r["time_horizon"], "tolerant": False})
        elif adjacent_horizon(r["time_horizon"], intent["horizon"]):
            rel += 1
            reasons.append({"dim": "horizon", "value": r["time_horizon"], "tolerant": True})

    if intent["market_scope"] and r.get("asset_scope") == intent["market_scope"]:
        rel += 1
        reasons.append({"dim": "scope", "value": r["asset_scope"], "tolerant": False})
    if intent["goal"] == "dca" and r.get("sub_style") == "dca":
        rel += 1
        reasons.append({"dim": "goal", "value": "dca", "tolerant": False})
    if intent["experience"] == "new" and r.get("tier") == "starter":
        rel += 1

    if (r.get("instance_count") or 1) > 1 and user_classes:
        split = "/".join(str(s) for s in (r.get("funding_split") or []))
        caveats.append(f"Splits across {r['instance_count']} wallets ({split}); your assets may sit mainly in one leg.")
    if intent["budget"] is not None and intent["budget"] < (r.get("min_budget") or 0):
        caveats.append(f"Needs ~${int(r['min_budget'])} to start; you mentioned ${int(intent['budget'])}.")
    return rel, reasons, caveats


def _suggested_budget(r, intent):
    mb = r.get("min_budget") or 100
    if intent["budget"] and intent["budget"] >= mb:
        return int(intent["budget"])
    return mb


def _intent_echo(intent):
    echo = {}
    for k in ("risk", "belief", "horizon", "direction", "market_scope", "goal", "experience", "budget"):
        if intent.get(k) is not None:
            v = intent[k]
            if k == "budget" and isinstance(v, float) and v.is_integer():
                v = int(v)
            echo[k] = v
    if intent["assets"]:
        echo["assets"] = [v for _, v in intent["assets"]]
    if intent["exclude"]:
        echo["exclude"] = [f"{d}:{v}" for d, v in intent["exclude"]]
    return echo


def _active_constraints(intent):
    c = []
    if [v for k, v in intent["assets"] if k == "class"]:
        c.append("asset-class")
    if [v for k, v in intent["assets"] if k == "named"]:
        c.append("named-asset")
    if intent["direction"] in ("long_only", "short_only"):
        c.append(f"direction:{intent['direction']}")
    for dim, val in intent["exclude"]:
        c.append(f"exclude:{dim}:{val}")
    return c


def match(intent, records, limit=8, offset=0):
    intent.setdefault("_broadened_classes", [])
    survivors = [r for r in records if not _hard_reject(r, intent)]
    widened = []

    named = [v for k, v in intent["assets"] if k == "named"]
    if not survivors and named:
        broadened = [c for c in (infer_class_for_named(n) for n in named) if c]
        i2 = dict(intent)
        i2["assets"] = [(k, v) for k, v in intent["assets"] if k != "named"]
        i2["_broadened_classes"] = broadened
        survivors = [r for r in records if not _hard_reject(r, i2)]
        if survivors:
            widened.append("named_asset")
            intent = i2

    build_custom = {"label": "Build a custom strategy", "route": "senpi-strategy-author"}
    if not survivors:
        return {"candidates": [], "build_custom": build_custom,
                "meta": {"widened": widened, "unmet": _active_constraints(intent),
                         "eligible_count": 0, "returned_n": 0, "offset": offset,
                         "intent_echo": _intent_echo(intent), "warnings": intent.get("_warnings", [])}}

    scored = []
    for r in survivors:
        rel, reasons, caveats = _score(r, intent)
        scored.append((rel, r, reasons, caveats))
    scored.sort(key=lambda x: (-x[0], 0 if x[1].get("tier") == "starter" else 1,
                               x[1].get("min_budget") or 0, x[1].get("sort_order") or 0, x[1].get("name") or ""))
    page = scored[offset:offset + limit]
    candidates = []
    for rel, r, reasons, caveats in page:
        cand = {
            "id": r.get("id"), "name": r.get("name"), "emoji": r.get("emoji"),
            "tagline": r.get("tagline"), "belief_plain": r.get("belief_plain"),
            "archetype_label": r.get("archetype_label"), "tier": r.get("tier"),
            "suggested_budget": _suggested_budget(r, intent), "relevance": rel,
            "match_reasons": reasons, "market_facts": [], "caveats": caveats,
        }
        if (r.get("instance_count") or 1) > 1:
            cand["funding_split"] = r.get("funding_split")
        candidates.append(cand)
    return {"candidates": candidates, "build_custom": build_custom,
            "meta": {"widened": widened, "eligible_count": len(scored), "returned_n": len(candidates),
                     "offset": offset, "intent_echo": _intent_echo(intent),
                     "warnings": intent.get("_warnings", [])}}


# ---------------------------------------------------------------- data layer (guarded I/O)
def load_catalog(path):
    with open(path) as f:
        return (json.load(f).get("skills") or [])


def _get_client():
    if RUNTIME_DIR not in sys.path:
        sys.path.insert(0, RUNTIME_DIR)
    from senpi_runtime_helpers import SenpiClient  # noqa
    return SenpiClient()


def _ok(resp):
    """Unwrap an MCP response; None if it failed (mcp_call returns success:False, not an exception)."""
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def fetch_user_context(client):
    ctx = {"budget": None, "holdings": [], "favored_assets": [], "favored_direction": None}
    try:
        data = _ok(client.mcp_call("account_get_portfolio", timeout=15))
        if data:
            ctx["budget"] = data.get("total_balance_usd") or data.get("total_usdc_in_hyperliquid")
            for pos in (data.get("positions") or []):
                sym = pos.get("coin") or pos.get("asset")
                if sym:
                    ctx["holdings"].append(sym)
        else:
            ctx["_unavailable"] = True
    except Exception as e:  # noqa
        ctx["_error"] = str(e)
    return ctx


def fetch_market_map(client, assets):
    """One batched pass: funding regime + per-asset regime/OI. Parallel; degrades to {} per asset."""
    regime = None
    try:
        regime = (_ok(client.mcp_call("market_get_funding_regime", timeout=10)) or {}).get("regime")
    except Exception:  # noqa
        pass

    uniq = []
    for a in assets:
        if a and a not in uniq:
            uniq.append(a)
    uniq = uniq[:12]

    def one(a):
        try:
            data = _ok(client.mcp_call("market_get_asset_data", asset=a, candle_intervals=["4h"],
                                       include_order_book=False, include_funding=False, timeout=12))
            oiv = (data or {}).get("oi_velocity") or {}
            return (a, {"asset": a, "oi_trend": oiv.get("oi_trend"), "funding_regime": regime})
        except Exception:  # noqa
            return (a, {"asset": a, "funding_regime": regime})

    try:
        from senpi_runtime_helpers import parallel
        results = parallel([(lambda a=a: one(a)) for a in uniq])  # -> [(ok, value), ...]
        pairs = [val for ok, val in results if ok and val]
    except Exception:  # noqa
        pairs = [one(a) for a in uniq]
    return dict(pairs), regime


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi strategy discovery engine")
    ap.add_argument("--risk")
    ap.add_argument("--assets")
    ap.add_argument("--belief")
    ap.add_argument("--horizon")
    ap.add_argument("--direction")
    ap.add_argument("--market-scope", dest="market_scope")
    ap.add_argument("--goal")
    ap.add_argument("--budget")
    ap.add_argument("--exclude")
    ap.add_argument("--experience")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--no-market", action="store_true", help="skip the live market enrichment pass")
    ap.add_argument("--context-only", action="store_true", help="return user context only, no match")
    args = ap.parse_args(argv)

    try:
        records = load_catalog(args.catalog)
    except Exception as e:  # noqa
        print(json.dumps({"candidates": [], "build_custom": {"label": "Build a custom strategy",
              "route": "senpi-strategy-author"}, "meta": {"error": f"catalog load failed: {e}"}}))
        return 1

    by_id = {r.get("id"): r for r in records}

    if args.context_only:
        try:
            uc = fetch_user_context(_get_client())
        except Exception as e:  # noqa
            uc = {"_error": str(e)}
        print(json.dumps({"user_context": uc}, ensure_ascii=False))
        return 0

    intent = normalize_intent(args)
    result = match(intent, records, limit=args.limit, offset=args.offset)

    # market enrichment (pass 2): ONE batched fetch over the union of the top-N's chosen assets
    if not args.no_market and result["candidates"]:
        user_named = [v for k, v in intent["assets"] if k == "named"]
        per_cand = {}
        union = []
        for cand in result["candidates"]:
            assets = by_id.get(cand["id"], {}).get("assets") or []
            pref = [a for a in assets if any(asset_matches(n, [a]) for n in user_named)] or assets
            per_cand[cand["id"]] = pref[:3]
            union.extend(pref[:3])
        try:
            client = _get_client()
            result["meta"]["user_context"] = fetch_user_context(client)
            fact_map, _ = fetch_market_map(client, union)
            for cand in result["candidates"]:
                cand["market_facts"] = [fact_map[a] for a in per_cand[cand["id"]] if a in fact_map]
        except Exception as e:  # noqa
            result["meta"].setdefault("warnings", []).append(f"market enrichment unavailable: {e}")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
