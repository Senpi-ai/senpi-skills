#!/usr/bin/env python3
# Senpi WOLF Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""WOLF v1.0.0 Producer — event-driven regime rotation, two books, one script.

Wolf trades THE TURN. It computes a single market-wide REGIME score from
cross-asset confirmation — equities, oil, gold, BTC, the dollar — and a book
only fires when the regime AGREES with its mandate. So the fund rotates: in a
confirmed risk-on regime the risk_on book works and the risk_off book stands
down; when the regime flips, they swap. No single asset can flip the book — the
whole macro complex has to agree. ONE producer script serves both books; the
WOLF_LEG env var selects which:

  WOLF_LEG=risk_on   Risk-On book — fires ONLY when regime == RISK_ON.
    Goes LONG beaten-down beta (crypto majors / high-beta alts + growth
    indices) that is turning up, riding the relief-rally rotation. Wide
    let-it-run DSL.

  WOLF_LEG=risk_off  Risk-Off book — fires ONLY when regime == RISK_OFF.
    Goes LONG defensives (gold / oil / dollar / yen) that are trending up and
    SHORT risk assets that are rolling over. Tighter DSL — risk-off moves are
    sharp and reverse fast on a headline.

The edge is the regime TRANSITION itself, detected across the whole complex —
NOT a fixed bet (Thesis Fund) and NOT per-asset trend (Elephant). NOT a
copy-trader. Each book scores its own universe and pushes signals via
SenpiClient.push_signal(); runtime owns the LLM gate (pass-through), DSL exits,
and all risk.guard_rails.

Environment / config resolution:
  WOLF_LEG             — REQUIRED. "risk_on" or "risk_off".
  SENPI_AUTH_TOKEN     — REQUIRED. Bearer token for MCP + signal POST.
  WOLF_RISK_ON_WALLET  — risk_on-book strategy wallet (or config.wallet)
  WOLF_RISK_OFF_WALLET — risk_off-book strategy wallet (or config.wallet)
  WOLF_DECISION_MODEL  — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL        — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wolf_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "risk_on" | "risk_off"
SCANNER_NAME = f"wolf_{LEG}_signals"
SIGNAL_TYPE = "WOLF_RISK_ON_ROTATION" if LEG == "risk_on" else "WOLF_RISK_OFF_ROTATION"
MY_REGIME = "RISK_ON" if LEG == "risk_on" else "RISK_OFF"

# Max raw score ~ 7 (base 2 + 1h confirm 2 + momentum 2 + rsi room 1).
NORM_DIV = 8.0

# Risk assets — the high-beta complex. LONG in risk_on, SHORT in risk_off.
_RISK_ASSETS = ["BTC", "ETH", "SOL", "HYPE", "SUI", "xyz:XYZ100", "xyz:SP500"]
# Defensives — the flight-to-safety complex. LONG in risk_off only.
_DEFENSIVES = ["xyz:GOLD", "xyz:SILVER", "xyz:BRENTOIL", "xyz:DXY", "xyz:JPY"]

# Cross-asset regime probes. Each maps a probe asset to the 4h-trend reading
# that votes RISK-ON. The opposite reading votes RISK-OFF; NEUTRAL abstains.
#   equities BULLISH -> on   | oil BEARISH (falling) -> on
#   gold BEARISH (falling) -> on | btc BULLISH -> on | dxy BEARISH (soft $) -> on
_REGIME_PROBES = [
    {"asset": "xyz:XYZ100", "fallback": "xyz:SP500", "risk_on_when": "BULLISH", "label": "equities"},
    {"asset": "xyz:BRENTOIL", "fallback": None, "risk_on_when": "BEARISH", "label": "oil"},
    {"asset": "xyz:GOLD", "fallback": None, "risk_on_when": "BEARISH", "label": "gold"},
    {"asset": "BTC", "fallback": None, "risk_on_when": "BULLISH", "label": "btc"},
    {"asset": "xyz:DXY", "fallback": None, "risk_on_when": "BEARISH", "label": "dollar"},
]

_DEFAULTS = {
    "risk_on": {
        "riskAssets": _RISK_ASSETS,
        "defensives": [],
        "minScore": 5,
        "marginPct": 0.20,
        "maxLeverage": 5,
        "maxSlots": 3,
        "minNotionalUsd": 200,
        "tickSeconds": 300,
        "regimeThreshold": 2,        # net cross-asset votes to declare a regime
        "momThresholdPct": 1.0,
        "rsiOverbought": 78,
        "rsiOversold": 22,
    },
    "risk_off": {
        "riskAssets": _RISK_ASSETS,   # SHORT candidates
        "defensives": _DEFENSIVES,    # LONG candidates
        "minScore": 5,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 3,
        "minNotionalUsd": 200,
        "tickSeconds": 300,
        "regimeThreshold": 2,
        "momThresholdPct": 1.0,
        "rsiOverbought": 78,
        "rsiOversold": 22,
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
# REGIME DETECTOR — the shared brain (both books read it)
# ═══════════════════════════════════════════════════════════════

def detect_regime(config, meta_map):
    """Compute a market-wide regime from cross-asset 4h trend confirmation.

    Each probe votes RISK_ON or RISK_OFF (or abstains on NEUTRAL). The regime
    is declared only when the NET vote clears `regimeThreshold` — so no single
    asset can flip the book; the whole complex has to lean one way. Returns
    a dict with the regime label, the per-probe votes, and the net score.
    """
    threshold = int(config.get("regimeThreshold", _DEFAULTS["regimeThreshold"]))
    probes = config.get("regimeProbes", _REGIME_PROBES)
    on_votes, off_votes, detail = 0, 0, {}
    for p in probes:
        asset = p.get("asset")
        on_when = p.get("risk_on_when", "BULLISH")
        label = p.get("label", asset)
        md = fetch_candles(asset, ["4h"])
        if (not md or len(md["candles"].get("4h", [])) < 6) and p.get("fallback"):
            asset = p["fallback"]
            md = fetch_candles(asset, ["4h"])
        c4 = md["candles"].get("4h", []) if md else []
        if len(c4) < 6:
            detail[label] = "no_data"
            continue
        trend4, _ = trend_structure(c4)
        off_when = "BEARISH" if on_when == "BULLISH" else "BULLISH"
        if trend4 == on_when:
            on_votes += 1
            detail[label] = "risk_on"
        elif trend4 == off_when:
            off_votes += 1
            detail[label] = "risk_off"
        else:
            detail[label] = "neutral"
    net = on_votes - off_votes
    if net >= threshold:
        regime = "RISK_ON"
    elif net <= -threshold:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"
    return {"regime": regime, "net": net, "on_votes": on_votes,
            "off_votes": off_votes, "threshold": threshold, "detail": detail}


# ═══════════════════════════════════════════════════════════════
# Directional scoring — score a clean trend in the WANTED direction
# (direction is set by the regime + asset class, not picked here)
# ═══════════════════════════════════════════════════════════════

def score_directional(asset, meta, config, want):
    """Score a name for a regime-mandated `want` direction (LONG/SHORT).
    The 4h structure must BACK the wanted direction or we skip — the regime
    is the green light, but the name itself must be turning the right way."""
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


def push_signal(thesis, margin_usd, leverage, held_assets, regime):
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
        "regime": regime.get("regime"),
        "regimeNet": regime.get("net"),
        "own24h": round(thesis.get("own24h", 0), 2),
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
    board. risk_on: long the risk complex. risk_off: long defensives + short
    the risk complex."""
    risk = config.get("riskAssets", _DEFAULTS["riskAssets"])
    defensives = config.get("defensives", _DEFAULTS["defensives"])
    pairs = []
    if LEG == "risk_on":
        for n in risk:
            pairs.append((n, "LONG"))
    else:
        for n in defensives:
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
                    "_wolf_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_wolf_producer_version": VERSION})
        return

    meta_map, _canonical = get_universe_meta()
    regime = detect_regime(config, meta_map)

    # ── ROTATION GATE — this book only trades in its regime ──
    if regime["regime"] != MY_REGIME:
        cfg.output({
            "status": "ok", "leg": LEG, "signals_pushed": 0,
            "regime": regime["regime"], "regime_net": regime["net"],
            "regime_detail": regime["detail"],
            "note": f"STANDING DOWN — regime is {regime['regime']}, "
                    f"this book trades only in {MY_REGIME}",
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_wolf_producer_version": VERSION,
        })
        return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = config.get("minNotionalUsd", _DEFAULTS["minNotionalUsd"])

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "regime": regime["regime"], "held_assets": held_assets,
                    "max_slots": max_slots, "_wolf_producer_version": VERSION})
        return

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
        cfg.output({
            "status": "ok", "leg": LEG,
            "regime": regime["regime"], "regime_net": regime["net"],
            "scanned": len(targets), "candidates": 0, "signals_pushed": 0,
            "min_score": min_score, "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — {MY_REGIME} confirmed but no name cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_wolf_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    to_emit = candidates[:open_slots]
    margin_usd = round(account_value * margin_pct, 2)

    pushed = 0
    emitted = []
    for th in to_emit:
        leverage = clamp_leverage(max_lev, th["_meta"])
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        if push_signal(th, margin_usd, leverage, held_assets, regime):
            pushed += 1
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "direction": th["direction"],
                "score": th["score"], "leverage": leverage,
                "margin_usd": margin_usd, "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "regime": regime["regime"], "regime_net": regime["net"],
        "regime_detail": regime["detail"],
        "scanned": len(targets), "candidates": len(candidates),
        "open_slots": open_slots, "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_wolf_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"wolf-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
