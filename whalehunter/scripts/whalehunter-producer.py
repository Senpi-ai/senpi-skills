#!/usr/bin/env python3
# Senpi WHALEHUNTERHEDGE Producer v1.2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""WHALEHUNTERHEDGE v1.2.0 Producer — patient-whale conviction copy, long/short.

Follows the SINGLE highest-conviction trades of CONSISTENT + PATIENT winners on
Senpi Discover and mirrors them on a wide DSL. The thesis: a trader tagged ELITE
(consistency) AND PATIENT (activity) sits on their hands for long stretches — they
have almost no routine trades to copy. So when one of them opens a NEW position
that is a large share of their OWN balance, that is their highest-conviction read.
That rare, big, patient-winner strike is the signal. The rarity is the alpha.

ONE producer, two INDEPENDENT sleeves on SEPARATE wallets (WHALEHUNTER_LEG):

  WHALEHUNTER_LEG=long   mirrors whales' high-conviction LONG strikes.
  WHALEHUNTER_LEG=short  mirrors whales' high-conviction SHORT strikes.

Separate wallets => the book can hold CONFLICTING positions on one asset (one whale
high-conviction long ETH + a different whale high-conviction short ETH => long
sleeve holds ETH-long, short sleeve holds ETH-short). Net: a whale-conviction
long/short hedge; funding default 50/50.

Three gates before a strike (all from Senpi Discover):
  1. consistency tag == ELITE          (discovery_get_top_traders)
  2. activity tag    == PATIENT         (discovery_get_top_traders)
  3. new position with marginUsed / accountValue >= convictionPct   (trader_state)

NOT execution code. The producer pushes signals; the runtime owns the LLM gate
(pass-through), the WIDE DSL, and risk. Pool refreshes daily; baseline-seed guard
prevents firing on pre-existing positions at startup.

Environment / config resolution:
  WHALEHUNTER_LEG            — REQUIRED. "long" or "short".
  SENPI_AUTH_TOKEN          — REQUIRED. USER-scoped token (discovery_* needs a user id).
  WHALEHUNTER_LONG_WALLET   — long-sleeve wallet (or config.wallet)
  WHALEHUNTER_SHORT_WALLET  — short-sleeve wallet (or config.wallet)
  WHALEHUNTER_DECISION_MODEL— bare LLM model name; resolved into runtime.yaml
"""

import hashlib
import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import whalehunter_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "2.0.0"
# Bump whenever the cohort-BUILDING logic changes (sampling, bucketing, paging) so a
# stale on-disk cohorts.json from older logic is auto-invalidated on the next tick —
# the daily cache must never outlive a code change to how cohorts are built.
# v1 = single top-N pull ; v2 = paged-by-offset crowd sampling + per-cohort cap.
COHORT_CACHE_VERSION = 2
LEG = cfg.LEG  # "long" | "short"
DIRECTION = "LONG" if LEG == "long" else "SHORT"
SCANNER_NAME = f"whalehunter_{LEG}_signals"
SIGNAL_TYPE = "WHALEHUNTER_STRIKE_LONG" if LEG == "long" else "WHALEHUNTER_STRIKE_SHORT"

_DEFAULTS = {
    # Pool + tiered sizing — consistency x style. KEY = "<CONSISTENCY>+<STYLE>",
    # VALUE = tier weight (scales BOTH base margin and the per-position cap, so a
    # higher tier always has a higher ceiling). Only these combos are followed;
    # every other combo (Streaky/Choppy, Degen/Active) is excluded entirely.
    # The pool is queried once per combo so each trader is tagged exactly.
    "tagWeights": {
        "ELITE+PATIENT": 1.0,
        "ELITE+TACTICAL": 0.75,
        "RELIABLE+PATIENT": 0.50,
        "RELIABLE+TACTICAL": 0.40,
    },
    "poolTimeFrame": "MONTHLY",
    "poolSortBy": "GAIN_TO_PAIN_RATIO",
    "poolOverfetch": 80,
    "poolSize": 50,             # v1.2: 30->50 (more whales -> more chances one swings big)
    "poolMinTraderAgeDays": 21,
    "poolRefreshHours": 24,
    # Strike — the conviction gate.
    "convictionPct": 0.18,   # v1.2: lowered 0.25->0.18 (18% of book is real conviction; 25% was rarely cleared)          # new position's marginUsed / accountValue must clear this
    "maxEntryAgeSec": 21600,        # 6h — patient whales aren't time-sensitive (baseline diff is the primary newness test)
    # Mirror sizing (MY budget, conviction-scaled).
    "marginPct": 0.12,              # base margin per strike, % of MY equity
    "maxConvictionScale": 2.0,      # margin multiplier ceiling for very-high-conviction / consensus
    "maxMarginPct": 0.25,           # hard per-position cap, % of MY equity
    "maxLeverage": 5,
    "stdLeverage": 3,
    "maxSlots": 6,
    "venueMinNotionalUsd": 10,
    "minNotionalPctOfEquity": 0.01,
    "tickSeconds": 300,
    # ── v2.0 COHORT-DIVERGENCE ENGINE (smart money vs the crowd) ──
    "cohortEngine": True,            # v2.0 primary signal
    "enableIndividualCopy": False,   # v1.x per-whale copy retained but OFF by default (superseded)
    "smartMinRealizedUsd": 1_000_000,    # "smartest money" cohort: lifetime realized gains >= $1M
    "crowdMinRealizedUsd": 10_000,       # "crowd" cohort: $10k ..
    "crowdMaxRealizedUsd": 100_000,      #            .. $100k realized
    "cohortFetchLimit": 1000,            # top_traders page size (max 1000) — the ranking is PAGED by offset
    "cohortMaxPages": 6,                 # page this deep into the realized-PnL ranking to REACH the crowd band
    "cohortSampleCap": 250,              # cap EACH cohort's sample (bounds per-tick trader_state load)
    "cohortRefreshHours": 24,            # cohort MEMBERSHIP changes slowly — cache daily
    "cohortMinMembers": 5,               # need at least this many in a cohort to trust its net bias
    "biasThreshold": 0.50,               # smart cohort must be >= 50% net-directional on a coin to signal
    "crowdDivergenceMin": 0.20,          # crowd >= 20% net-OPPOSITE = a confirmation booster (not required)
    "requireGrowing": True,              # the smart net must be GROWING vs the ledger ("adding daily")
    "ledgerLookbackDays": 3,             # compare today's smart net to ~this many days ago
    "cohortMinScore": 4,                 # divergence score floor to emit
}


def _resolve_wallet():
    wallet, _ = cfg.get_wallet_and_strategy()
    return wallet


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Pool — consistent + patient winners (daily cache, shared by both sleeves)
# ═══════════════════════════════════════════════════════════════

def _f(x, *keys, default=0.0):
    for k in keys:
        v = x.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def refresh_pool(config, force=False):
    cached = cfg.load_pool()
    now = time.time()
    age_h = (now - cached.get("refreshed_at", 0)) / 3600
    if not force and age_h < float(config.get("poolRefreshHours", _DEFAULTS["poolRefreshHours"])) and cached.get("traders"):
        return cached["traders"]

    weights = config.get("tagWeights", _DEFAULTS["tagWeights"])
    min_age = float(config.get("poolMinTraderAgeDays", _DEFAULTS["poolMinTraderAgeDays"]))
    overfetch = int(config.get("poolOverfetch", _DEFAULTS["poolOverfetch"]))
    by_addr, any_resp = {}, False
    # One query per consistency+style combo so every trader is tagged exactly to
    # its tier (don't depend on per-trader label field names). The combo's weight
    # is the sizing tier; combos absent from tagWeights are never followed.
    for combo, weight in weights.items():
        try:
            cons, style = (s.strip().upper() for s in combo.split("+"))
        except ValueError:
            continue
        resp = cfg.mcp_call(
            "discovery_get_top_traders",
            time_frame=config.get("poolTimeFrame", _DEFAULTS["poolTimeFrame"]),
            sort_by=config.get("poolSortBy", _DEFAULTS["poolSortBy"]),
            consistency=[cons], activity_labels=[style],
            open_position_filter=False, limit=overfetch,
        )
        if not resp or not resp.get("success", True):
            continue
        any_resp = True
        raw = resp.get("data", resp)
        if isinstance(raw, dict):
            raw = raw.get("traders", raw.get("data", []))
        if not isinstance(raw, list):
            continue
        for t in raw:
            if not isinstance(t, dict):
                continue
            addr = (t.get("address") or t.get("trader_address") or "").lower()
            if not addr:
                continue
            age_days = _f(t, "trader_age_days", "traderAgeDays") or _f(t, "traderAgeSeconds") / 86400
            if age_days < min_age:
                continue
            if addr in by_addr and by_addr[addr]["tag_weight"] >= float(weight):
                continue   # a trader has one tag pair; if seen, keep the higher tier
            by_addr[addr] = {
                "address": addr,
                "username": t.get("username") or t.get("userName") or "",
                "consistency": cons, "style": style, "combo": combo,
                "tag_weight": float(weight),
                "gain_to_pain": _f(t, "gain_to_pain_ratio", "gainToPainRatio"),
                "roi": _f(t, "return_on_investment", "returnOnInvestment", "roi"),
                "age_days": round(age_days, 1),
            }
    if not any_resp:
        return cached.get("traders", [])
    # rank by tier first, then risk-adjusted return
    out = sorted(by_addr.values(), key=lambda x: (x["tag_weight"], x["gain_to_pain"]), reverse=True)
    out = out[:int(config.get("poolSize", _DEFAULTS["poolSize"]))]
    cfg.save_pool({"refreshed_at": now, "refreshed_iso": cfg.now_iso(),
                   "size": len(out), "traders": out})
    return out


# ═══════════════════════════════════════════════════════════════
# State — each pool whale's open positions + account value
# ═══════════════════════════════════════════════════════════════

def _pos_dir(p):
    szi = _f(p, "szi", "size")
    return "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)


def fetch_pool_states(pool):
    """Return {address: {"account_value": float, "positions": [pos...]}}."""
    addrs = [t["address"] for t in pool]
    if not addrs:
        return {}
    resp = cfg.mcp_call("discovery_get_trader_state", trader_addresses=addrs,
                        include_position_age=True)
    if not resp:
        return {}
    data = resp.get("data", resp)
    traders = data.get("traders", []) if isinstance(data, dict) else []
    out = {}
    for t in traders:
        if not isinstance(t, dict):
            continue
        addr = (t.get("address") or "").lower()
        cms = t.get("crossMarginSummary") or t.get("cross_margin_summary") or {}
        acct = _f(cms, "accountValue", "account_value") or _f(t, "accountValue")
        out[addr] = {"account_value": acct, "positions": t.get("openPositions") or t.get("open_positions") or []}
    return out


def detect_strikes(pool, states, last_seen, config):
    """A strike = a NEW position (vs baseline) in THIS sleeve's direction whose
    marginUsed / accountValue clears the conviction gate, recently opened."""
    now = time.time()
    conv_min = float(config.get("convictionPct", _DEFAULTS["convictionPct"]))
    max_age = float(config.get("maxEntryAgeSec", _DEFAULTS["maxEntryAgeSec"]))
    strikes = []
    new_seen, below_gate, max_conv = 0, [], 0.0   # near-miss observability
    for tr in pool:
        addr = tr["address"]
        st = states.get(addr)
        if not st:
            continue
        acct = st["account_value"]
        if acct <= 0:
            continue
        prev_keys = set(tuple(k) for k in last_seen.get(addr, []))
        for p in st["positions"]:
            if not isinstance(p, dict):
                continue
            d = _pos_dir(p)
            if d != DIRECTION:                      # this sleeve only follows its own direction
                continue
            coin = p.get("coin") or p.get("asset")
            if not coin:
                continue
            if [coin, d] in [list(k) for k in prev_keys] or (coin, d) in prev_keys:
                continue                            # already seen — not new
            dur = _f(p, "durationInSeconds", "duration_in_seconds")
            if dur and dur > max_age:
                continue                            # opened too long ago to chase
            margin = _f(p, "marginUsed", "margin_used")
            conv = margin / acct if acct > 0 else 0.0
            new_seen += 1                            # a NEW, fresh, right-direction position (any conviction)
            if conv > max_conv:
                max_conv = conv
            if conv < conv_min:
                # near-miss: a new position that didn't clear the conviction gate. Logged
                # so we can see whether the throttle is the GATE (these pile up) or the
                # NEWNESS (new_seen stays ~0 = patient whales just aren't opening anything).
                below_gate.append({"coin": coin, "conviction_pct": round(conv, 4),
                                   "from": tr.get("username") or addr[:8]})
                continue
            strikes.append({
                "trader": tr, "coin": coin, "direction": d,
                "conviction_pct": round(conv, 4),
                "their_margin": round(margin, 2),
                "entry_price": _f(p, "entryPx", "entry_price"),
                "age_sec": int(dur),
            })
    diag = {"new_dir_positions": new_seen, "max_conviction_seen": round(max_conv, 4),
            "below_gate": below_gate[:8]}
    return strikes, diag


def add_consensus(strikes, states):
    """How many OTHER pool whales also hold a conviction position in the same
    coin+direction (any size). More agreement = stronger signal."""
    by_key = {}
    for addr, st in states.items():
        for p in st.get("positions", []):
            if not isinstance(p, dict):
                continue
            d = _pos_dir(p)
            coin = p.get("coin") or p.get("asset")
            if coin and d:
                by_key.setdefault((coin, d), set()).add(addr)
    for s in strikes:
        peers = by_key.get((s["coin"], s["direction"]), set()) - {s["trader"]["address"]}
        s["consensus"] = len(peers)
    return strikes


# ═══════════════════════════════════════════════════════════════
# Sizing + emit
# ═══════════════════════════════════════════════════════════════

def mirror_margin(account_value, strike, config):
    """MY margin, tiered by the whale's tag pair then scaled by their conviction
    and pool consensus. The TIER WEIGHT scales BOTH the base AND the cap, so a
    higher tier (e.g. ELITE+PATIENT) always has a higher ceiling than a lower one
    (e.g. RELIABLE+TACTICAL) — conviction/consensus only scale WITHIN a tier's
    range. Conviction shows in SIZE, not leverage."""
    tw = float(strike["trader"].get("tag_weight", 1.0))
    base = float(config.get("marginPct", _DEFAULTS["marginPct"])) * tw
    cap = float(config.get("maxMarginPct", _DEFAULTS["maxMarginPct"])) * tw
    conv_min = float(config.get("convictionPct", _DEFAULTS["convictionPct"]))
    smax = float(config.get("maxConvictionScale", _DEFAULTS["maxConvictionScale"]))
    conv = strike["conviction_pct"]
    conv_scale = 1.0 + min(1.0, max(0.0, (conv - conv_min) / max(conv_min, 1e-9)))  # +1.0 at 2x threshold
    cons_scale = 1.0 + min(0.5, 0.15 * strike.get("consensus", 0))                  # up to +0.5 from agreement
    scale = min(smax, conv_scale * cons_scale)
    return round(min(account_value * base * scale, account_value * cap), 2)


def clamp_lev(desired, max_lev):
    return max(1, min(int(desired), int(max_lev)))


def push_signal(strike, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: wallet not resolved")
        return False
    if strike["coin"].upper() in {h.upper() for h in held_assets}:
        return False
    tr = strike["trader"]
    data_block = {
        "leverage": leverage, "marginUsd": margin_usd, "direction": strike["direction"],
        "heldAssets": held_assets,
        "sourceTrader": tr["address"], "sourceUsername": tr.get("username", ""),
        "sourceConsistency": tr.get("consistency"), "sourceStyle": tr.get("style"),
        "tagTier": tr.get("combo"), "tagWeight": tr.get("tag_weight"),
        "sourceGainToPain": tr.get("gain_to_pain"), "sourceRoi": tr.get("roi"),
        "convictionPct": strike["conviction_pct"], "theirMarginUsd": strike["their_margin"],
        "consensus": strike.get("consensus", 0), "entryAgeSec": strike["age_sec"],
        "reasons": [f"{tr.get('combo', 'TIER')} {tr.get('username') or tr['address'][:8]}",
                    f"conviction {strike['conviction_pct']:.0%} of their book",
                    f"tier weight {tr.get('tag_weight')}",
                    f"consensus {strike.get('consensus', 0)}"],
    }
    # confidence: conviction beyond the gate + consensus, normalized to 0..1
    conf = min(1.0, 0.5 + strike["conviction_pct"] + 0.05 * strike.get("consensus", 0))
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS, scanner=SCANNER_NAME, asset=strike["coin"],
            direction=strike["direction"], score=conf, signal_type=SIGNAL_TYPE, data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {strike['coin']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {strike['coin']}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# v2.0 COHORT-DIVERGENCE ENGINE — smart money vs the crowd
# Define cohorts by lifetime realized gains, aggregate each cohort's NET
# positioning per asset, track whether the smart cohort is ADDING daily, and
# position WITH the smart money when it diverges from the crowd.
# ═══════════════════════════════════════════════════════════════

def _realized(t):
    return _f(t, "profit_and_loss_realized", "realizedPnl", "realized_pnl",
              "pnlRealized", "pnl", "profitAndLoss", "profit_and_loss", default=0.0)


def build_cohorts(config, force=False):
    """Smart cohort (lifetime realized >= smartMinRealizedUsd) + crowd cohort
    (crowdMin..crowdMax), from the ALL_TIME realized-PnL ranking. Cached daily.

    The ranking is sorted DESCENDING by realized PnL, so the smart cohort sits at the
    top (well-sampled in page 0) but the $10k-$100k crowd lives thousands of ranks
    DEEPER — a single top-N pull catches only the weak tail of the top page (the
    inverted 'crowd << smart' symptom). So we PAGE by offset until the crowd is
    representatively sampled or the ranking drops below the crowd floor, capping each
    cohort to bound per-tick trader_state load."""
    cached = cfg.load_cohorts()
    now = time.time()
    if (not force and cached.get("smart")
            and cached.get("cache_version") == COHORT_CACHE_VERSION   # busts a cache written by older cohort logic
            and (now - cached.get("refreshed_at", 0)) / 3600 < float(config.get("cohortRefreshHours", _DEFAULTS["cohortRefreshHours"]))):
        return cached.get("smart", []), cached.get("crowd", [])
    smin = float(config.get("smartMinRealizedUsd", _DEFAULTS["smartMinRealizedUsd"]))
    cmin = float(config.get("crowdMinRealizedUsd", _DEFAULTS["crowdMinRealizedUsd"]))
    cmax = float(config.get("crowdMaxRealizedUsd", _DEFAULTS["crowdMaxRealizedUsd"]))
    cap = int(config.get("cohortSampleCap", _DEFAULTS["cohortSampleCap"]))
    page_size = int(config.get("cohortFetchLimit", _DEFAULTS["cohortFetchLimit"]))
    max_pages = int(config.get("cohortMaxPages", _DEFAULTS["cohortMaxPages"]))
    smart, crowd, seen, pages = [], [], set(), 0
    for page in range(max_pages):
        resp = cfg.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME",
                            sort_by="PROFIT_AND_LOSS_REALIZED", open_position_filter=False,
                            limit=page_size, offset=page * page_size)
        if not resp or not resp.get("success", True):
            break
        raw = resp.get("data", resp)
        if isinstance(raw, dict):
            raw = raw.get("traders", raw.get("data", []))
        if not isinstance(raw, list) or not raw:
            break
        pages += 1
        page_min = None
        for t in raw:
            if not isinstance(t, dict):
                continue
            addr = (t.get("address") or t.get("trader_address") or "").lower()
            if not addr or addr in seen:
                continue
            rp = _realized(t)
            page_min = rp if page_min is None else min(page_min, rp)
            if rp >= smin:
                if len(smart) < cap:
                    smart.append(addr); seen.add(addr)
            elif cmin <= rp <= cmax:
                if len(crowd) < cap:
                    crowd.append(addr); seen.add(addr)
        if len(smart) >= cap and len(crowd) >= cap:
            break                                  # both cohorts fully sampled
        if page_min is not None and page_min < cmin:
            break                                  # ranking dropped below the crowd floor — deeper pages are smaller
    if not smart and not crowd:
        return cached.get("smart", []), cached.get("crowd", [])
    cfg.save_cohorts({"refreshed_at": now, "refreshed_iso": cfg.now_iso(),
                      "cache_version": COHORT_CACHE_VERSION,
                      "smart": smart, "crowd": crowd, "pages": pages})
    return smart, crowd


def _signed_notional(p):
    szi = _f(p, "szi", "size")
    val = _f(p, "positionValue", "notional", "position_value")
    if val <= 0:
        val = abs(szi) * _f(p, "entryPx", "markPx", "entry_price")
    return (1.0 if szi > 0 else (-1.0 if szi < 0 else 0.0)) * abs(val)


def cohort_net_bias(addrs):
    """Aggregate a cohort's NET positioning per coin: bias = net/gross in [-1,+1]
    (+1 = all long, -1 = all short), plus member counts. Batches of 50."""
    per = {}
    for i in range(0, len(addrs), 50):
        resp = cfg.mcp_call("discovery_get_trader_state", trader_addresses=addrs[i:i + 50])
        if not resp:
            continue
        data = resp.get("data", resp)
        traders = data.get("traders", []) if isinstance(data, dict) else []
        for t in traders:
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
    return per


def _utc_day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def update_ledger_and_growth(smart_per, config):
    """Append today's smart-cohort net-per-coin to the daily ledger; return the
    growth vs the earliest snapshot in the window (the 'adding daily' signal).
    Returns {} on day 1 (no prior snapshot) — requireGrowing then blocks until ~day 2."""
    led = cfg.load_ledger()
    days = led.get("days", {})
    today = _utc_day()
    days[today] = {c: round(d["net"], 2) for c, d in smart_per.items()}
    for stale in sorted(days)[:-10]:        # keep ~10 days
        days.pop(stale, None)
    prior_dates = [d for d in sorted(days) if d < today]
    growth = {}
    if prior_dates:
        prior = days[prior_dates[0]]        # earliest snapshot we hold (>= lookback ago once warmed)
        for coin, net in days[today].items():
            growth[coin] = round(net - float(prior.get(coin, 0.0)), 2)
    cfg.save_ledger({"days": days, "updated": cfg.now_iso()})
    return growth


def cohort_signals(smart_per, crowd_per, growth, config):
    """For THIS sleeve's direction: the smart cohort is net-directional past the
    bias threshold, growing (adding), with the crowd ideally diverging. Score it."""
    bt = float(config.get("biasThreshold", _DEFAULTS["biasThreshold"]))
    cdiv = float(config.get("crowdDivergenceMin", _DEFAULTS["crowdDivergenceMin"]))
    req_grow = bool(config.get("requireGrowing", _DEFAULTS["requireGrowing"]))
    min_members = int(config.get("cohortMinMembers", _DEFAULTS["cohortMinMembers"]))
    floor = int(config.get("cohortMinScore", _DEFAULTS["cohortMinScore"]))
    want_long = (DIRECTION == "LONG")
    out = []
    for coin, sd in smart_per.items():
        if sd["n_long"] + sd["n_short"] < min_members:
            continue
        bias = sd["bias"]
        if (want_long and bias < bt) or (not want_long and bias > -bt):
            continue                                    # smart cohort not net in our direction
        g = growth.get(coin)
        growing = (g is not None) and ((g > 0) if want_long else (g < 0))
        if req_grow and not growing:
            continue                                    # not "adding" — skip
        crowd_bias = crowd_per.get(coin, {}).get("bias", 0.0)
        crowd_div = (crowd_bias <= -cdiv) if want_long else (crowd_bias >= cdiv)
        score = 3 + (1 if abs(bias) >= 0.7 else 0) + (1 if growing else 0) + (1 if crowd_div else 0)
        if score < floor:
            continue
        n_confirm = sd["n_long"] if want_long else sd["n_short"]
        out.append({"coin": coin, "direction": DIRECTION, "score": score,
                    "smart_bias": bias, "crowd_bias": round(crowd_bias, 3),
                    "growth": g, "n_confirm": n_confirm,
                    "reasons": [f"smart cohort net {bias:+.2f} on {coin}",
                                f"{'ADDING' if growing else 'flat'} (Δnet {g})",
                                f"crowd {crowd_bias:+.2f}{' DIVERGES' if crowd_div else ''}",
                                f"{n_confirm} smart whales {DIRECTION}"]})
    out.sort(key=lambda s: (s["score"], abs(s["smart_bias"])), reverse=True)
    return out


def cohort_margin(account_value, score, config):
    base = float(config.get("marginPct", _DEFAULTS["marginPct"]))
    cap = float(config.get("maxMarginPct", _DEFAULTS["maxMarginPct"]))
    smax = float(config.get("maxConvictionScale", _DEFAULTS["maxConvictionScale"]))
    floor = int(config.get("cohortMinScore", _DEFAULTS["cohortMinScore"]))
    scale = min(smax, 1.0 + 0.25 * max(0, score - floor))   # +25% per point above the floor
    return round(min(account_value * base * scale, account_value * cap), 2)


def push_cohort_signal(s, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS or s["coin"].upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {"leverage": leverage, "marginUsd": margin_usd, "direction": s["direction"],
                  "heldAssets": held_assets, "signalKind": "COHORT_DIVERGENCE",
                  "smartBias": s["smart_bias"], "crowdBias": s["crowd_bias"],
                  "smartGrowth": s["growth"], "smartMembers": s["n_confirm"], "reasons": s["reasons"]}
    conf = min(1.0, 0.5 + abs(s["smart_bias"]) * 0.4 + 0.05 * s["n_confirm"])
    try:
        cfg._wrapper_client.push_signal(address=STRATEGY_ADDRESS, scanner=SCANNER_NAME,
                                        asset=s["coin"], direction=s["direction"], score=conf,
                                        signal_type=SIGNAL_TYPE, data=data_block)
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {s['coin']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {s['coin']}: {type(e).__name__}: {e}")
        return False


def cohort_main(config):
    run_start = time.time()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_whalehunter_producer_version": VERSION})
        return
    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_whalehunter_producer_version": VERSION})
        return
    force = os.environ.get("REFRESH_POOL", "").lower() in ("1", "true", "yes")
    smart, crowd = build_cohorts(config, force=force)
    if len(smart) < int(config.get("cohortMinMembers", _DEFAULTS["cohortMinMembers"])):
        cfg.output({"status": "ok", "leg": LEG, "smart_n": len(smart), "crowd_n": len(crowd),
                    "note": "smart cohort too small (no >$1M-realized traders, or token lacks user scope)",
                    "_whalehunter_producer_version": VERSION})
        return
    smart_per = cohort_net_bias(smart)
    crowd_per = cohort_net_bias(crowd)
    growth = update_ledger_and_growth(smart_per, config)
    strikes = cohort_signals(smart_per, crowd_per, growth, config)

    max_slots = int(config.get("maxSlots", _DEFAULTS["maxSlots"]))
    open_slots = max_slots - len(held_assets)
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)),
                       float(config.get("venueMinNotionalUsd", 10)))
    max_lev = int(config.get("maxLeverage", _DEFAULTS["maxLeverage"]))
    std_lev = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    pushed, emitted = 0, []
    seen = {(h.upper(), DIRECTION) for h in held_assets}
    slots_left = open_slots
    for s in strikes:
        if slots_left <= 0:
            break
        key = (s["coin"].upper(), s["direction"])
        if key in seen:
            continue
        margin_usd = cohort_margin(account_value, s["score"], config)
        leverage = clamp_lev(std_lev, max_lev)
        if margin_usd * leverage < min_notional or margin_usd * 1.1 > free_margin:
            continue
        if push_cohort_signal(s, margin_usd, leverage, held_assets):
            seen.add(key)
            pushed += 1
            slots_left -= 1
            free_margin -= margin_usd
            emitted.append({"coin": s["coin"], "direction": s["direction"], "score": s["score"],
                            "smart_bias": s["smart_bias"], "crowd_bias": s["crowd_bias"],
                            "growth": s["growth"], "margin_usd": margin_usd})
    # Human-readable insight (top divergences this tick — surfaced even if not all emitted)
    insight = [f"{s['coin']}: smart {s['smart_bias']:+.2f} vs crowd {s['crowd_bias']:+.2f}, Δ{s['growth']} → {s['direction']}"
               for s in strikes[:5]]
    cfg.output({"status": "ok", "leg": LEG, "engine": "cohort",
                "smart_n": len(smart), "crowd_n": len(crowd),
                "candidates": len(strikes), "signals_pushed": pushed, "emitted": emitted,
                "insight": insight,
                "note": (None if strikes else "WAITING — no smart/crowd divergence cleared the gate"),
                "account_value": round(account_value, 2),
                "elapsed_sec": round(time.time() - run_start, 2),
                "_whalehunter_producer_version": VERSION})


def main():
    """v2.0 dispatcher: the cohort-divergence engine is primary; the v1.x per-whale
    copy runs only if explicitly re-enabled (enableIndividualCopy)."""
    config = cfg.load_config()
    if config.get("cohortEngine", _DEFAULTS["cohortEngine"]):
        cohort_main(config)
    if config.get("enableIndividualCopy", _DEFAULTS["enableIndividualCopy"]):
        individual_main()


# ═══════════════════════════════════════════════════════════════
# MAIN (v1.x per-whale copy — retained, OFF by default)
# ═══════════════════════════════════════════════════════════════

def individual_main():
    """v1.x per-whale conviction copy. Retained but OFF by default in v2.0 (the
    cohort-divergence engine supersedes it). Enable via config.enableIndividualCopy."""
    run_start = time.time()
    config = cfg.load_config()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_whalehunter_producer_version": VERSION})
        return

    force = os.environ.get("REFRESH_POOL", "").lower() in ("1", "true", "yes")
    pool = refresh_pool(config, force=force)
    if not pool:
        cfg.output({"status": "ok", "leg": LEG, "note": "pool empty (no ELITE+PATIENT winners or token lacks user scope)",
                    "_whalehunter_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_whalehunter_producer_version": VERSION})
        return

    max_slots = int(config.get("maxSlots", _DEFAULTS["maxSlots"]))
    open_slots = max_slots - len(held_assets)

    states = fetch_pool_states(pool)
    last_seen = cfg.load_last_seen()

    # Baseline-seed guard: on first run (empty baseline) NEVER treat existing
    # positions as new strikes — just record the baseline and fire nothing.
    if not last_seen:
        strikes, diag = [], {"new_dir_positions": 0, "max_conviction_seen": 0.0, "below_gate": [], "note": "baseline seed"}
    else:
        strikes, diag = detect_strikes(pool, states, last_seen, config)
        strikes = add_consensus(strikes, states)

    # Persist current baseline (this sleeve's-direction positions per whale).
    new_baseline = {}
    for addr, st in states.items():
        keys = []
        for p in st.get("positions", []):
            d = _pos_dir(p)
            coin = p.get("coin") or p.get("asset")
            if coin and d == DIRECTION:
                keys.append([coin, d])
        if keys:
            new_baseline[addr] = keys
    cfg.save_last_seen(new_baseline)

    if not strikes or open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "pool_size": len(pool),
                    "strikes": len(strikes), "open_slots": open_slots, "signals_pushed": 0,
                    "new_dir_positions": diag.get("new_dir_positions"),
                    "max_conviction_seen": diag.get("max_conviction_seen"),
                    "below_gate": diag.get("below_gate"),
                    "note": "WAITING — no consistent+patient whale opened a high-conviction "
                            + DIRECTION + " strike" if not strikes else "slots full",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_whalehunter_producer_version": VERSION})
        return

    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)),
                       float(config.get("venueMinNotionalUsd", 10)))
    max_lev = int(config.get("maxLeverage", _DEFAULTS["maxLeverage"]))
    std_lev = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))

    # strongest conviction first
    strikes.sort(key=lambda s: (s["conviction_pct"], s.get("consensus", 0)), reverse=True)

    pushed, emitted = 0, []
    slots_left = open_slots
    seen_keys = {(h.upper(), DIRECTION) for h in held_assets}   # dedup vs what I already hold + within this tick
    for s in strikes:
        if slots_left <= 0:
            break
        key = (s["coin"].upper(), s["direction"])
        if key in seen_keys:
            continue                                            # two whales on the same asset = consensus (already scaled), not two positions
        margin_usd = mirror_margin(account_value, s, config)
        leverage = clamp_lev(std_lev, max_lev)
        notional = margin_usd * leverage
        if notional < min_notional or margin_usd * 1.1 > free_margin:
            continue
        if push_signal(s, margin_usd, leverage, held_assets):
            seen_keys.add(key)
            pushed += 1
            slots_left -= 1
            free_margin -= margin_usd
            emitted.append({"coin": s["coin"], "direction": s["direction"],
                            "from": s["trader"].get("username") or s["trader"]["address"][:8],
                            "conviction_pct": s["conviction_pct"], "consensus": s.get("consensus", 0),
                            "leverage": leverage, "margin_usd": margin_usd})

    cfg.output({"status": "ok", "leg": LEG, "pool_size": len(pool), "strikes": len(strikes),
                "signals_pushed": pushed, "emitted": emitted,
                "new_dir_positions": diag.get("new_dir_positions"),
                "max_conviction_seen": diag.get("max_conviction_seen"),
                "below_gate": diag.get("below_gate"),
                "account_value": round(account_value, 2),
                "elapsed_sec": round(time.time() - run_start, 2),
                "_whalehunter_producer_version": VERSION})


if __name__ == "__main__":
    _lock_id = hashlib.sha256((STRATEGY_ADDRESS or LEG).lower().encode()).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    _kwargs = {"fn": main, "interval_seconds": _tick,
               "name": f"whalehunter-{LEG}-producer-{_lock_id}",
               "tick_timeout": min(180, max(30, _tick - 10))}
    try:
        _params = inspect.signature(producer_daemon).parameters
    except (TypeError, ValueError):
        _params = {}
    if "wallet" in _params:
        _kwargs["wallet"] = STRATEGY_ADDRESS
    if "scanner" in _params:
        _kwargs["scanner"] = SCANNER_NAME
    producer_daemon(**_kwargs)
