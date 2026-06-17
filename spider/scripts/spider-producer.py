#!/usr/bin/env python3
# Senpi SPIDER Producer v5.1.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""SPIDER v5.2.0 Producer — two autonomous style legs, one script.

v5.2.0: swing leg adds an ADAPTIVE RISK GOVERNOR (adaptive_governor.py) +
a market-regime gate. The governor is the primary, self-driving risk brake —
green-day entry scaling, an adaptive red-stop band, a trailing multi-day
drawdown halt, and per-asset cooldown by trade OUTCOME — replacing the
hardwired entry caps / bypass flag / fixed cooldowns (runtime guard_rails are
now a wide hard backstop). The regime gate stands the swing leg down on new
longs when the broad tape (BTC + an equity index, 4h) is risk-off.

Spider runs TWO concurrent strategy wallets, each a distinct trading
style. ONE producer script serves both; the SPIDER_LEG env var selects
which leg this process is:

  SPIDER_LEG=swing  Tech & AI multi-day momentum, LONG only.
    Universe = static crypto alts (cryptoAlts: SUI/ONDO/HYPE/NIL/GRASS/
    ZEC) + a DYNAMIC pool of XYZ equities rebuilt each tick from the live
    instrument board (build_universe). An XYZ name is eligible if liquid
    (dayNtlVlm >= xyzVolFloorUsd) AND either in the curated tech/AI/space
    include-set OR freshly listed (< xyzFreshDays) and not in the
    commodity/FX/index exclude-set — the fresh branch auto-catches new
    Pre-IPO Perpetuals / AI IPOs (e.g. CBRS/Cerebras, SPCX/SpaceX) with no
    code edit. Scores 4h + 1h trend-structure alignment, 24h relative-
    strength proxy, SM-consensus LONG bonus, RSI-room penalty, funding
    crowding. Multi-day horizon. Emits up to (maxSlots - held) signals/tick.

  SPIDER_LEG=scalp  Macro & majors fast mean-reversion, BOTH directions.
    Universe: majors (BTC/ETH/SOL/HYPE) + energy (xyz:BRENTOIL/xyz:CL).
    Fades short-TF stretch from a fast MA + RSI extreme (oversold->LONG,
    overbought->SHORT), with a 1h trend filter to avoid catching a
    falling knife against a strong trend. Strict 5x. Minutes-to-hour
    holds. Emits up to (maxSlots - held) signals/tick.

NOT a copy-trader. Each leg scores its own universe to a STYLE and
pushes signals via SenpiClient.push_signal(). The runtime owns the LLM
gate (pass-through), DSL exits, and all risk.guard_rails. Leverage is
clamped to the leg cap (swingMaxLeverage / scalpMaxLeverage) and then to
each asset's Hyperliquid venue max (from market_list_instruments) so we
never emit an unfillable order (e.g. GRASS/NIL cap at 3x).

Environment / config resolution:
  SPIDER_LEG            — REQUIRED. "swing" or "scalp".
  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  SPIDER_SWING_WALLET   — swing-leg strategy wallet (or config.wallet)
  SPIDER_SCALP_WALLET   — scalp-leg strategy wallet (or config.wallet)
  SPIDER_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL         — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spider_config as cfg
import adaptive_governor

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "5.2.0"
LEG = cfg.LEG
SCANNER_NAME = f"spider_{LEG}_signals"
SIGNAL_TYPE = "SPIDER_SWING_MOMENTUM" if LEG == "swing" else "SPIDER_SCALP_REVERSION"

# Score normalization divisor for the 0..1 ingest-ranking score. The
# RAW integer score is what the runtime decision_prompt gates on
# (score < minScore etc.); the normalized value only ranks signals.
NORM_DIV = 12.0 if LEG == "swing" else 7.0

# Per-leg defaults (config.json overrides every one of these).
_DEFAULTS = {
    "swing": {
        # Static crypto-alt pool (not on the XYZ board).
        "cryptoAlts": ["SUI", "ONDO", "HYPE", "NIL", "GRASS", "ZEC"],
        # Curated tech/AI/space core — always eligible if live + liquid.
        "xyzIncludeSet": ["NVDA", "AMD", "INTC", "MRVL", "MU", "TSM", "ASML",
                          "ARM", "SMSN", "SKHX", "DRAM", "SNDK", "DELL",
                          "LITE", "CRWV", "PLTR", "ORCL", "GOOGL", "META",
                          "MSFT", "AMZN", "AAPL", "NFLX", "IBM", "COIN",
                          "MSTR", "CRCL", "HOOD", "SPCX", "RKLB", "CBRS"],
        # Hard guard — commodities / FX / broad indices / ETFs never count
        # as "Tech & AI", even if freshly listed.
        "xyzExcludeSet": ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER",
                          "CL", "BRENTOIL", "NATGAS", "URANIUM", "URNM",
                          "EUR", "JPY", "GBP", "KRW", "DXY", "SP500", "JP225",
                          "KR200", "NIFTY", "IBOV", "XYZ100", "EWY", "EWJ",
                          "EWT", "EWZ", "XLE", "VIX", "PURRDAT", "BIRD"],
        "xyzVolFloorUsd": 5000000,
        "xyzFreshDays": 21,
        "xyzMaxNames": 20,
        # Static fallback list if the instrument board is unavailable.
        "allowedAssets": ["xyz:NVDA", "xyz:AMD", "xyz:INTC", "xyz:MRVL",
                          "xyz:MU", "xyz:TSM", "SUI", "ONDO", "HYPE",
                          "NIL", "GRASS", "ZEC"],
        "minScore": 5,
        "marginPct": 0.28,
        "maxLeverage": 10,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
    },
    "scalp": {
        "allowedAssets": ["BTC", "ETH", "SOL", "HYPE",
                          "xyz:BRENTOIL", "xyz:CL"],
        "minScore": 4,
        "marginPct": 0.15,
        "maxLeverage": 5,
        "maxSlots": 4,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 60,
    },
}[LEG]

_LEG_MAX_LEVERAGE_KEY = "swingMaxLeverage" if LEG == "swing" else "scalpMaxLeverage"


def _resolve_wallet():
    wallet, _ = cfg.get_wallet_and_strategy()
    return wallet


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Technical helpers (close=c, high=h, low=l, open=o, vol=v on HL candles)
# ═══════════════════════════════════════════════════════════════

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def simple_ma(closes, period):
    if not closes:
        return 0
    window = closes[-period:] if len(closes) >= period else closes
    return sum(window) / len(window)


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def get_universe_meta():
    """Return {name: {"max_leverage": int|None, "ctx": {...}}} for every
    live instrument on both the main and XYZ dexes. One call covers the
    whole universe; we read venue max leverage + funding/markPx/prevDayPx
    from here rather than paying a per-asset fetch."""
    data = cfg.mcp_call("market_list_instruments")
    out = {}
    if not data:
        return out
    insts = data.get("data", data)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = inst.get("name") or inst.get("context", {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
    return out


def get_sm_map():
    """Return {COIN: long_ratio_pct} from smart-money leaderboard markets.
    Swing-only bonus. XYZ equities have no SM data — absent => no bonus."""
    data = cfg.mcp_call("leaderboard_get_markets")
    out = {}
    if not data:
        return out
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", []))
    agg = {}
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if not token:
            continue
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)) or 0)
        a = agg.setdefault(token.upper(), {"long": 0.0, "short": 0.0})
        if direction == "long":
            a["long"] = pct
        elif direction == "short":
            a["short"] = pct
    for tok, a in agg.items():
        total = a["long"] + a["short"]
        if total > 0:
            out[tok] = a["long"] / total * 100
    return out


def fetch_candles(asset, intervals):
    """Pull candles for `asset` at the requested intervals. Returns
    {"candles": {iv: [...]}, "ctx": {...}} or None on failure."""
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=intervals,
        dex=_dex_for(asset),
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return None
    d = data.get("data", data)
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


# ═══════════════════════════════════════════════════════════════
# Regime gate (swing momentum) — don't chase breakouts in a risk-off tape
# ═══════════════════════════════════════════════════════════════

def market_regime_ok(config):
    """Swing-momentum go/no-go: stand down on NEW longs when the broad tape is
    risk-off. Reads the 4h trend of a crypto proxy (BTC) + an equity index
    (xyz:XYZ100, SP500 fallback). Default: ANY probe 4h-bearish => risk-off
    (a momentum-long leg shouldn't chase breakouts when either crypto or
    equities are rolling over). Fails OPEN on a data outage. Returns (ok, detail)."""
    probes = config.get("regimeProbes", [
        {"asset": "BTC", "fallback": None},
        {"asset": "xyz:XYZ100", "fallback": "xyz:SP500"},
    ])
    require_all_non_bearish = bool(config.get("regimeRequireAllNonBearish", True))
    detail, bearish, seen = {}, 0, 0
    for p in probes:
        a = p.get("asset")
        md = fetch_candles(a, ["4h"])
        if (not md or len(md["candles"].get("4h", [])) < 6) and p.get("fallback"):
            a = p["fallback"]
            md = fetch_candles(a, ["4h"])
        c4 = md["candles"].get("4h", []) if md else []
        if len(c4) < 6:
            detail[p.get("asset")] = "no_data"
            continue
        t, _ = trend_structure(c4)
        detail[p.get("asset")] = t.lower()
        seen += 1
        if t == "BEARISH":
            bearish += 1
    if seen == 0:
        return True, detail  # data outage → fail open (governor + DSL still protect)
    ok = (bearish == 0) if require_all_non_bearish else (bearish < seen)
    return ok, detail


# ═══════════════════════════════════════════════════════════════
# SWING scoring — Tech & AI multi-day momentum, LONG only
# ═══════════════════════════════════════════════════════════════

def score_swing(asset, meta, sm_map, config):
    md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]

    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)

    ctx = meta.get("ctx", {})
    funding = float(ctx.get("funding", 0) or 0)
    markpx = float(ctx.get("markPx", price) or price)
    prevday = float(ctx.get("prevDayPx", 0) or 0)
    rs24 = ((markpx - prevday) / prevday * 100) if prevday > 0 else price_momentum(c1, min(24, len(c1) - 1))

    direction = "LONG"
    score = 0
    reasons = []

    # 4h trend structure: the multi-day backbone. Bearish kills it.
    if trend4 == "BULLISH":
        score += 3
        reasons.append(f"4h_bullish_{s4:.0%}")
    elif trend4 == "BEARISH":
        score -= 4
        reasons.append("4h_bearish")

    # 1h trend confirmation
    if trend1 == "BULLISH":
        score += 2
        reasons.append(f"1h_bullish_{s1:.0%}")
    elif trend1 == "BEARISH":
        score -= 1
        reasons.append("1h_bearish")

    # 24h relative-strength proxy
    if rs24 >= 8:
        score += 3
        reasons.append(f"rs_{rs24:+.1f}%")
    elif rs24 >= 4:
        score += 2
        reasons.append(f"rs_{rs24:+.1f}%")
    elif rs24 >= 1:
        score += 1
        reasons.append(f"rs_{rs24:+.1f}%")
    elif rs24 < 0:
        score -= 1
        reasons.append(f"rs_neg_{rs24:+.1f}%")

    # RSI room (overbought penalty / room bonus)
    rsi_max = config.get("rsiMaxLong", 78)
    if rsi > rsi_max:
        score -= 2
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif rsi < 50:
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # Funding: negative funding (shorts pay) favors a LONG; very crowded
    # long funding is a small penalty.
    if funding < 0:
        score += 1
        reasons.append(f"funding_neg_{funding:+.4f}")
    elif funding > 0.0002:
        score -= 1
        reasons.append("funding_crowded")

    # Smart-money consensus bonus (crypto alts only; XYZ has none)
    sm_pct = 0.0
    sm_ratio = sm_map.get(asset.upper())
    if sm_ratio is not None:
        sm_pct = sm_ratio
        if sm_ratio > 58:
            score += 2
            reasons.append(f"sm_long_{sm_ratio:.0f}%")
        elif sm_ratio < 42:
            score -= 2
            reasons.append(f"sm_short_{sm_ratio:.0f}%")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "trend1h": trend1, "rs": rs24,
        "smPct": sm_pct, "funding": funding,
    }


# ═══════════════════════════════════════════════════════════════
# SCALP scoring — Macro & majors fast mean-reversion, BOTH directions
# ═══════════════════════════════════════════════════════════════

def score_scalp(asset, meta, config):
    md = fetch_candles(asset, ["5m", "15m", "1h"])
    if not md:
        return None
    c15 = md["candles"].get("15m", [])
    c1 = md["candles"].get("1h", [])
    if len(c15) < 20 or len(c1) < 6:
        return None
    closes15 = [_close(c) for c in c15]
    price = closes15[-1]

    ma = simple_ma(closes15, 20)
    stretch = ((price - ma) / ma * 100) if ma > 0 else 0
    rsi = calc_rsi(closes15)
    trend1, _ = trend_structure(c1)

    ctx = meta.get("ctx", {})
    funding = float(ctx.get("funding", 0) or 0)

    rsi_os = config.get("rsiOversold", 30)
    rsi_ob = config.get("rsiOverbought", 70)
    stretch_thresh = config.get("stretchThresholdPct", 0.8)

    # Which side is more extreme? Fade it.
    oversold_mag = max(rsi_os - rsi, 0) / max(rsi_os, 1) + max(-stretch, 0) / stretch_thresh
    overbought_mag = max(rsi - rsi_ob, 0) / max(100 - rsi_ob, 1) + max(stretch, 0) / stretch_thresh
    if oversold_mag <= 0 and overbought_mag <= 0:
        return None
    direction = "LONG" if oversold_mag >= overbought_mag else "SHORT"

    score = 0
    reasons = []

    if direction == "LONG":
        if rsi <= 20:
            score += 3
            reasons.append(f"rsi_{rsi:.0f}_deep_oversold")
        elif rsi <= 25:
            score += 2
            reasons.append(f"rsi_{rsi:.0f}_oversold")
        elif rsi <= rsi_os:
            score += 1
            reasons.append(f"rsi_{rsi:.0f}_oversold")
        if -stretch >= 2 * stretch_thresh:
            score += 2
            reasons.append(f"stretch_{stretch:+.2f}%")
        elif -stretch >= stretch_thresh:
            score += 1
            reasons.append(f"stretch_{stretch:+.2f}%")
        if trend1 == "BULLISH":
            score += 1
            reasons.append("1h_uptrend_dip")
        elif trend1 == "BEARISH":
            score -= 2
            reasons.append("1h_downtrend_knife")
        if funding < 0:
            score += 1
            reasons.append(f"funding_neg_{funding:+.4f}")
    else:  # SHORT
        if rsi >= 80:
            score += 3
            reasons.append(f"rsi_{rsi:.0f}_deep_overbought")
        elif rsi >= 75:
            score += 2
            reasons.append(f"rsi_{rsi:.0f}_overbought")
        elif rsi >= rsi_ob:
            score += 1
            reasons.append(f"rsi_{rsi:.0f}_overbought")
        if stretch >= 2 * stretch_thresh:
            score += 2
            reasons.append(f"stretch_{stretch:+.2f}%")
        elif stretch >= stretch_thresh:
            score += 1
            reasons.append(f"stretch_{stretch:+.2f}%")
        if trend1 == "BEARISH":
            score += 1
            reasons.append("1h_downtrend_rip")
        elif trend1 == "BULLISH":
            score -= 2
            reasons.append("1h_uptrend_knife")
        if funding > 0:
            score += 1
            reasons.append(f"funding_pos_{funding:+.4f}")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend1h": trend1, "stretchPct": stretch, "funding": funding,
    }


# ═══════════════════════════════════════════════════════════════
# Leverage clamp + emit
# ═══════════════════════════════════════════════════════════════

def clamp_leverage(desired, meta):
    """Clamp desired leverage to the asset's Hyperliquid venue max."""
    venue = meta.get("max_leverage")
    try:
        venue = int(venue)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    if thesis["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "heldAssets": held_assets,
    }
    if LEG == "swing":
        data_block.update({
            "trend4h": thesis.get("trend4h"),
            "rs": round(thesis.get("rs", 0), 2),
            "smPct": round(thesis.get("smPct", 0), 1),
        })
    else:
        data_block.update({
            "trend1h": thesis.get("trend1h"),
            "rsi": round(thesis.get("rsi", 0), 1),
            "stretchPct": round(thesis.get("stretchPct", 0), 3),
        })

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=thesis["coin"],
            direction=thesis["direction"],
            score=min(thesis["score"] / NORM_DIV, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {thesis['coin']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {thesis['coin']}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# Universe resolution (swing = dynamic XYZ pool + static crypto alts)
# ═══════════════════════════════════════════════════════════════

def build_universe(config, meta_map):
    """Resolve the asset list to score this tick.

    SCALP: static config.allowedAssets (majors + energy).
    SWING: static crypto alts + a DYNAMIC pool of XYZ equities rebuilt
      from the live instrument board. An XYZ name qualifies if it is
      liquid (dayNtlVlm >= xyzVolFloorUsd) AND either (a) in the curated
      tech/AI/space include-set, or (b) freshly listed (younger than
      xyzFreshDays) and not in the commodity/FX/index exclude-set — the
      (b) branch auto-catches the next Pre-IPO Perp / AI IPO with no code
      edit. Qualifiers are capped to the top xyzMaxNames by 24h volume to
      bound per-tick candle fetches. The score gate (4h-bullish required)
      does the final quality filtering downstream.
    """
    if LEG == "scalp":
        return list(config.get("allowedAssets", _DEFAULTS["allowedAssets"]))

    crypto = list(config.get("cryptoAlts", _DEFAULTS["cryptoAlts"]))
    include = {t.upper() for t in config.get("xyzIncludeSet", _DEFAULTS["xyzIncludeSet"])}
    exclude = {t.upper() for t in config.get("xyzExcludeSet", _DEFAULTS["xyzExcludeSet"])}
    vol_floor = float(config.get("xyzVolFloorUsd", _DEFAULTS["xyzVolFloorUsd"]))
    fresh_days = float(config.get("xyzFreshDays", _DEFAULTS["xyzFreshDays"]))
    max_names = int(config.get("xyzMaxNames", _DEFAULTS["xyzMaxNames"]))

    # Fallback if the instrument board is unavailable: static include-set.
    if not meta_map:
        return crypto + [f"xyz:{t}" for t in sorted(include)]

    # Canonical live XYZ names only (meta_map double-keys name + UPPER;
    # originals are lower-prefixed "xyz:", the alias is "XYZ:").
    xyz_names = sorted(n for n in meta_map if isinstance(n, str) and n.startswith("xyz:"))

    now = cfg.now_ts()
    fresh_window = fresh_days * 86400
    first_seen = cfg.read_first_seen()
    first_run = not first_seen
    backdate = now - fresh_window - 1  # mark pre-existing names as already-old
    changed = False
    for n in xyz_names:
        if n not in first_seen:
            first_seen[n] = backdate if first_run else now
            changed = True
    if changed:
        cfg.write_first_seen(first_seen)

    qualifiers = []
    for n in xyz_names:
        ctx = (meta_map.get(n) or {}).get("ctx", {})
        try:
            vol = float(ctx.get("dayNtlVlm", 0) or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol < vol_floor:
            continue
        bare = n.split(":", 1)[1].upper()
        is_fresh = (now - first_seen.get(n, backdate)) < fresh_window
        if bare in include or (is_fresh and bare not in exclude):
            qualifiers.append((n, vol))

    qualifiers.sort(key=lambda x: x[1], reverse=True)
    return crypto + [n for n, _ in qualifiers[:max_names]]


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_spider_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_spider_producer_version": VERSION})
        return

    now = time.time()
    # ── Adaptive governor (instantiated only when config has a "governor" block;
    # primary smart brake — the runtime guard_rails are a wide hard backstop) ──
    gov_cfg = config.get("governor")
    gov = (adaptive_governor.AdaptiveGovernor(cfg.STATE_DIR / f"governor-{LEG}.json", gov_cfg)
           if gov_cfg else None)
    gsnap = gov.observe(account_value, positions, now) if gov else None
    if gsnap and gsnap.get("halted"):
        cfg.output({
            "status": "ok", "leg": LEG, "signals_pushed": 0,
            "note": f"HALTED — trailing drawdown {gsnap['trailing_dd_pct']:.0%}; "
                    "new entries paused until equity recovers (DSL still owns open exits)",
            "governor": gsnap, "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_spider_producer_version": VERSION,
        })
        return

    # ── Regime gate (swing momentum): stand down on new longs in a risk-off tape ──
    regime_detail = None
    if LEG == "swing" and config.get("regimeGateEnabled", True):
        regime_ok, regime_detail = market_regime_ok(config)
        if not regime_ok:
            cfg.output({
                "status": "ok", "leg": LEG, "signals_pushed": 0,
                "note": "STANDING DOWN — risk-off regime (broad tape 4h-bearish); "
                        "no new momentum entries (DSL still owns open exits)",
                "regime": regime_detail, "governor": gsnap, "held_assets": held_assets,
                "elapsed_sec": round(time.time() - run_start, 2),
                "_spider_producer_version": VERSION,
            })
            return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    leg_max_lev = config.get(_LEG_MAX_LEVERAGE_KEY, _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)), float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue minimum order value

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "_spider_producer_version": VERSION})
        return

    meta_map = get_universe_meta()
    sm_map = get_sm_map() if (LEG == "swing" and config.get("useSmBonus", True)) else {}
    allowed = build_universe(config, meta_map)

    candidates = []
    recently_skipped = []
    for asset in allowed:
        au = asset.upper()
        if au in held_set:
            continue
        if cfg.was_recently_signaled(asset):
            recently_skipped.append(asset)
            continue
        if gov and gov.asset_blocked(asset, now):   # outcome-based cooldown (loss → back off)
            recently_skipped.append(asset)
            continue
        meta = meta_map.get(asset) or meta_map.get(au)
        if not meta:
            continue
        if LEG == "swing":
            thesis = score_swing(asset, meta, sm_map, config)
        else:
            thesis = score_scalp(asset, meta, config)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok", "leg": LEG,
            "scanned": len(allowed), "candidates": 0, "signals_pushed": 0,
            "min_score": min_score, "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no thesis cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_spider_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    margin_usd = round(account_value * margin_pct, 2)
    # Cap emissions to what the wallet can actually FUND — never emit an entry we
    # can't afford. Without this, an open slot with no free margin re-emits an
    # un-fillable order every tick (insufficient-funds create_position spam).
    # free margin = equity minus on-chain committed margin (marginUsed).
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    affordable = int(free_margin / (margin_usd * 1.1)) if margin_usd > 0 else 0  # 1.1 = fee/slippage headroom
    # Governor sets the dynamic per-day entry budget (green scales up, red stops);
    # the final cap is the tightest of slots / affordability / governor.
    caps = [open_slots, affordable]
    gov_max = gov.max_entries() if gov else None
    if gov_max is not None:
        caps.append(gov_max)
    to_emit = candidates[:max(0, min(caps))]

    pushed = 0
    emitted = []
    for th in to_emit:
        leverage = clamp_leverage(leg_max_lev, th["_meta"])
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        if push_signal(th, margin_usd, leverage, held_assets):
            pushed += 1
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "direction": th["direction"],
                "score": th["score"], "leverage": leverage,
                "margin_usd": margin_usd, "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "scanned": len(allowed), "candidates": len(candidates),
        "open_slots": open_slots, "gov_max_entries": gov_max,
        "signals_pushed": pushed,
        "emitted": emitted, "held_assets": held_assets,
        "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "governor": gsnap, "regime": regime_detail,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_spider_producer_version": VERSION,
    })


if __name__ == "__main__":
    # Long-lived daemon. producer_daemon owns the per-tick scanner_lock
    # with stale-PID auto-recovery. Simplified signature (fn /
    # interval_seconds / name / tick_timeout) — the lock id encodes leg +
    # wallet so the swing and scalp daemons never collide.
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"spider-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
