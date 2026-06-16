#!/usr/bin/env python3
# Senpi RHINO Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""RHINO v1.0.0 Producer — tail-risk / crisis-alpha, two books, one script.

Rhino carries CHEAP CONVEXITY: it bleeds a little in calm and pays big in
shocks. ONE producer script serves both books; the RHINO_LEG env var selects
which:

  RHINO_LEG=hedge       Always-on Hedge book.
    A small, persistent LONG carry in the crisis-beneficiary complex (gold /
    oil / dollar / yen), entered only when a defensive is actually trending up
    (no falling-knife hedges). The standing insurance — small size, wide
    let-it-run DSL. NOT stress-gated: it holds the hedge through calm so you
    already own it when the shock hits.

  RHINO_LEG=escalation  Stress-gated Escalation book.
    Dormant in calm; fires HARD only when the shared STRESS detector confirms a
    shock (oil breaking out + equities breaking down + gold bid + BTC rolling
    over + vol/range expansion clear a threshold). Goes LONG the spiking crisis
    assets and SHORT the cratering risk assets — the convex add. Moderate-tight
    DSL that banks the spike (crises reverse violently).

The edge is CONVEXITY around a STRESS regime — NOT a fixed bet (Thesis Fund)
and NOT per-asset trend (Elephant). NOT a copy-trader. Each book scores its own
universe and pushes signals via SenpiClient.push_signal(); runtime owns the LLM
gate (pass-through), DSL exits, and all risk.guard_rails.

Environment / config resolution:
  RHINO_LEG               — REQUIRED. "hedge" or "escalation".
  SENPI_AUTH_TOKEN        — REQUIRED. Bearer token for MCP + signal POST.
  RHINO_HEDGE_WALLET      — hedge-book strategy wallet (or config.wallet)
  RHINO_ESCALATION_WALLET — escalation-book strategy wallet (or config.wallet)
  RHINO_DECISION_MODEL    — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL           — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rhino_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "hedge" | "escalation"
SCANNER_NAME = f"rhino_{LEG}_signals"
SIGNAL_TYPE = "RHINO_TAIL_HEDGE" if LEG == "hedge" else "RHINO_CRISIS_ESCALATION"

# Max raw score ~ 7 (base 2 + 1h confirm 2 + momentum 2 + rsi room 1).
NORM_DIV = 8.0

# Crisis longs — assets that SPIKE in geopolitical / macro stress.
_CRISIS_LONGS = ["xyz:GOLD", "xyz:SILVER", "xyz:BRENTOIL", "xyz:CL", "xyz:NATGAS", "xyz:DXY", "xyz:JPY"]
# Defensives the always-on hedge book carries (a calmer subset of crisis longs).
_DEFENSIVES = ["xyz:GOLD", "xyz:BRENTOIL", "xyz:DXY", "xyz:JPY"]
# Risk assets — the high-beta complex that CRATERS in stress. SHORT in escalation.
_RISK_ASSETS = ["BTC", "ETH", "SOL", "HYPE", "SUI", "xyz:XYZ100", "xyz:SP500"]

# Cross-asset STRESS probes. Each fires when its asset confirms the stress
# direction ("up" for crisis assets spiking, "down" for risk assets cratering),
# via 4h trend OR a 1h range break + ATR surge.
_STRESS_PROBES = [
    {"asset": "xyz:BRENTOIL", "fallback": None, "want": "up", "label": "oil"},
    {"asset": "xyz:XYZ100", "fallback": "xyz:SP500", "want": "down", "label": "equities"},
    {"asset": "xyz:GOLD", "fallback": None, "want": "up", "label": "gold"},
    {"asset": "BTC", "fallback": None, "want": "down", "label": "btc"},
]

_DEFAULTS = {
    "hedge": {
        "defensives": _DEFENSIVES,
        "crisisLongs": [],
        "riskAssets": [],
        "minScore": 5,
        "marginPct": 0.10,           # SMALL — cheap standing insurance
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "momThresholdPct": 1.0,
        "rsiOverbought": 80,
        "rsiOversold": 20,
        # stress params (used for telemetry on the hedge book)
        "stressThreshold": 2,
        "breakoutBars": 20,
        "baseBars": 30,
        "recentBars": 10,
        "surgeMod": 1.3,
        "volSurge": 1.5,
    },
    "escalation": {
        "defensives": [],
        "crisisLongs": _CRISIS_LONGS,   # LONG candidates when stress fires
        "riskAssets": _RISK_ASSETS,     # SHORT candidates when stress fires
        "minScore": 5,
        "marginPct": 0.22,           # LARGER — the convex add when stress confirms
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "momThresholdPct": 1.0,
        "rsiOverbought": 80,
        "rsiOversold": 20,
        "stressThreshold": 2,        # net stress probes that must fire to deploy
        "breakoutBars": 20,
        "baseBars": 30,
        "recentBars": 10,
        "surgeMod": 1.3,
        "volSurge": 1.5,
    },
}[LEG]


def _resolve_wallet():
    wallet, _ = cfg.get_wallet_and_strategy()
    return wallet


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Technical helpers
# ═══════════════════════════════════════════════════════════════

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def trend_structure(candles, lookback=6):
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


def true_range(c, prev_close):
    h, l = _high(c), _low(c)
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr(candles, period):
    """Average true range over the last `period` bars."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i], _close(candles[i - 1])))
    w = trs[-period:] if len(trs) >= period else trs
    return sum(w) / len(w) if w else 0.0


def range_break(candles, look):
    """Return 'up' / 'down' / None for a close beyond the prior `look`-bar
    range (excluding the current bar)."""
    if len(candles) < look + 2:
        return None
    highs = [_high(c) for c in candles]
    lows = [_low(c) for c in candles]
    price = _close(candles[-1])
    prior_high = max(highs[-(look + 1):-1])
    prior_low = min(lows[-(look + 1):-1])
    if price > prior_high:
        return "up"
    if price < prior_low:
        return "down"
    return None


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def get_universe_meta():
    data = cfg.mcp_call("market_list_instruments")
    out, canonical = {}, []
    if not data:
        return out, canonical
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
        canonical.append(name)
    return out, canonical


def fetch_candles(asset, intervals):
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


def ret_24h(meta):
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        mark = float(ctx.get("markPx", 0) or 0)
        prev = float(ctx.get("prevDayPx", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if prev <= 0 or mark <= 0:
        return 0.0
    return (mark - prev) / prev * 100.0


# ═══════════════════════════════════════════════════════════════
# STRESS DETECTOR — the shared brain (escalation gate + hedge telemetry)
# ═══════════════════════════════════════════════════════════════

def _stress_probe(p, config):
    """True if probe `p`'s asset confirms its stress direction, via 4h trend
    OR a 1h range break + ATR surge."""
    look = int(config.get("breakoutBars", _DEFAULTS["breakoutBars"]))
    base_bars = int(config.get("baseBars", _DEFAULTS["baseBars"]))
    surge_mod = float(config.get("surgeMod", _DEFAULTS["surgeMod"]))
    asset, want = p.get("asset"), p.get("want", "up")
    md = fetch_candles(asset, ["1h", "4h"])
    if (not md or len(md["candles"].get("4h", [])) < 6) and p.get("fallback"):
        asset = p["fallback"]
        md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return False, "no_data"
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c4) < 6:
        return False, "no_data"
    want_struct = "BULLISH" if want == "up" else "BEARISH"
    trend4, _ = trend_structure(c4)
    if trend4 == want_struct:
        return True, f"4h_{trend4.lower()}"
    brk = range_break(c1, look) if len(c1) >= look + 2 else None
    if brk == want:
        a_base = atr(c1[-(base_bars + 1):], base_bars)
        last_tr = true_range(c1[-1], _close(c1[-2]))
        surge = (last_tr / a_base) if a_base > 0 else 0.0
        if surge >= surge_mod:
            return True, f"break_{want}_{surge:.1f}x"
    return False, "calm"


def _vol_ratio(asset, config):
    """recent-ATR / baseline-ATR on `asset` 1h candles — a vol-expansion proxy."""
    base_bars = int(config.get("baseBars", _DEFAULTS["baseBars"]))
    recent_bars = int(config.get("recentBars", _DEFAULTS["recentBars"]))
    md = fetch_candles(asset, ["1h"])
    if not md:
        return 0.0
    c1 = md["candles"].get("1h", [])
    if len(c1) < base_bars + 2:
        return 0.0
    a_recent = atr(c1[-(recent_bars + 1):], recent_bars)
    a_base = atr(c1[-(base_bars + 1):], base_bars)
    return (a_recent / a_base) if a_base > 0 else 0.0


def detect_stress(config):
    """Tally cross-asset stress probes + a vol-expansion flag. STRESS is
    declared when the count clears `stressThreshold`."""
    threshold = int(config.get("stressThreshold", _DEFAULTS["stressThreshold"]))
    probes = config.get("stressProbes", _STRESS_PROBES)
    vol_surge = float(config.get("volSurge", _DEFAULTS["volSurge"]))
    fired, detail = 0, {}
    for p in probes:
        ok, reason = _stress_probe(p, config)
        detail[p["label"]] = reason
        if ok:
            fired += 1
    volr = _vol_ratio("BTC", config)
    if volr >= vol_surge:
        fired += 1
        detail["vol"] = f"expanding_{volr:.2f}x"
    else:
        detail["vol"] = f"calm_{volr:.2f}x"
    return {"stress": fired >= threshold, "fired": fired,
            "threshold": threshold, "detail": detail}


# ═══════════════════════════════════════════════════════════════
# Directional scoring — score a clean trend in the WANTED direction
# ═══════════════════════════════════════════════════════════════

def score_directional(asset, meta, config, want):
    """Score a name for a mandated `want` direction (LONG/SHORT). The 4h
    structure must BACK the wanted direction or we skip."""
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
    own = ret_24h(meta)
    mom = float(config.get("momThresholdPct", _DEFAULTS["momThresholdPct"]))
    rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
    rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))

    want_struct = "BULLISH" if want == "LONG" else "BEARISH"
    opp_struct = "BEARISH" if want == "LONG" else "BULLISH"
    if trend4 != want_struct:
        return None

    score = 2
    reasons = [f"4h_{trend4.lower()}_{s4:.0%}"]

    if trend1 == want_struct:
        score += 2
        reasons.append(f"1h_confirm_{s1:.0%}")
    elif trend1 == opp_struct:
        score -= 1
        reasons.append("1h_against")

    if want == "LONG":
        if own >= mom:
            score += 2
            reasons.append(f"mom_{own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"rsi_room_{rsi:.0f}")
    else:
        if own <= -mom:
            score += 2
            reasons.append(f"mom_{own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if rsi > rsi_os:
            score += 1
            reasons.append(f"rsi_room_{rsi:.0f}")

    return {
        "coin": asset, "direction": want, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "own24h": own,
    }


# ═══════════════════════════════════════════════════════════════
# Leverage clamp + emit
# ═══════════════════════════════════════════════════════════════

def clamp_leverage(desired, meta):
    venue = (meta or {}).get("max_leverage")
    try:
        venue = int(venue)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def push_signal(thesis, margin_usd, leverage, held_assets, stress):
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
        "trend4h": thesis.get("trend4h"),
        "own24h": round(thesis.get("own24h", 0), 2),
        "stress": stress.get("stress"),
        "stressFired": stress.get("fired"),
    }

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
# Universe — (name, wanted_direction) pairs for this book
# ═══════════════════════════════════════════════════════════════

def build_targets(config, meta_map):
    """Return [(name, want_direction)] for this book, intersected with the live
    board. hedge: long the defensives. escalation: long the crisis complex +
    short the risk complex."""
    defensives = config.get("defensives", _DEFAULTS["defensives"])
    crisis = config.get("crisisLongs", _DEFAULTS["crisisLongs"])
    risk = config.get("riskAssets", _DEFAULTS["riskAssets"])
    pairs = []
    if LEG == "hedge":
        for n in defensives:
            pairs.append((n, "LONG"))
    else:
        for n in crisis:
            pairs.append((n, "LONG"))
        for n in risk:
            pairs.append((n, "SHORT"))
    out = []
    for name, want in pairs:
        if not isinstance(name, str):
            continue
        if meta_map.get(name) or meta_map.get(name.upper()):
            out.append((name, want))
    return out


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_rhino_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_rhino_producer_version": VERSION})
        return

    stress = detect_stress(config)

    # ── ESCALATION GATE — the escalation book only fires under stress ──
    if LEG == "escalation" and not stress["stress"]:
        cfg.output({
            "status": "ok", "leg": LEG, "signals_pushed": 0,
            "stress": False, "stress_fired": stress["fired"],
            "stress_threshold": stress["threshold"], "stress_detail": stress["detail"],
            "note": f"DORMANT — no stress (fired {stress['fired']}/"
                    f"{stress['threshold']}); hedge book carries the standing insurance",
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_rhino_producer_version": VERSION,
        })
        return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)), float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue minimum order value

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "stress": stress["stress"], "held_assets": held_assets,
                    "max_slots": max_slots, "_rhino_producer_version": VERSION})
        return

    meta_map, _canonical = get_universe_meta()
    targets = build_targets(config, meta_map)

    candidates = []
    recently_skipped = []
    for name, want in targets:
        if name.upper() in held_set:
            continue
        if cfg.was_recently_signaled(name):
            recently_skipped.append(name)
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        thesis = score_directional(name, meta, config, want)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        note = (f"WAITING — STRESS confirmed but no crisis/risk name cleared min score {min_score}"
                if LEG == "escalation"
                else f"WAITING — no defensive trending up cleared min score {min_score}")
        cfg.output({
            "status": "ok", "leg": LEG,
            "stress": stress["stress"], "stress_fired": stress["fired"],
            "stress_detail": stress["detail"],
            "scanned": len(targets), "candidates": 0, "signals_pushed": 0,
            "min_score": min_score, "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped, "note": note,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_rhino_producer_version": VERSION,
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
    to_emit = candidates[:min(open_slots, affordable)]

    pushed = 0
    emitted = []
    for th in to_emit:
        leverage = clamp_leverage(max_lev, th["_meta"])
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        if push_signal(th, margin_usd, leverage, held_assets, stress):
            pushed += 1
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "direction": th["direction"],
                "score": th["score"], "leverage": leverage,
                "margin_usd": margin_usd, "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "stress": stress["stress"], "stress_fired": stress["fired"],
        "stress_detail": stress["detail"],
        "scanned": len(targets), "candidates": len(candidates),
        "open_slots": open_slots, "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_rhino_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"rhino-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
