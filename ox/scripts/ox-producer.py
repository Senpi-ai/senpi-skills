#!/usr/bin/env python3
# Senpi OX Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""OX v1.0.0 Producer — risk parity / all-weather, two books, one script.

Ox is the fund line's CORE holding. Its distinctive mechanic is INVERSE-
VOLATILITY position sizing: each sleeve's margin is proportional to
1/realized_vol, normalized across the basket, so a low-vol sleeve (gold,
indices) carries MORE notional than a high-vol one (a crypto alt) and no single
asset class dominates portfolio risk — true risk parity. It is always invested,
low leverage, low turnover. ONE producer script serves both books; the OX_LEG
env var selects which:

  OX_LEG=core     All-Weather Core book (LONG only).
    Holds a vol-balanced basket across asset-class sleeves — crypto majors +
    equity indices + metals + energy + FX — inverse-vol weighted to a portfolio
    budget. Always invested; only declines to ADD a sleeve that is in a hard
    downtrend (knife guard). Wide let-it-hold DSL; rebalances slowly.

  OX_LEG=ballast  Defensive Ballast book (LONG only).
    Holds defensives (gold / silver / dollar / yen), inverse-vol weighted, at a
    base budget that SCALES UP when a light risk-off lean confirms (equities
    soft + gold/dollar bid) — the active downside cushion. Always-on, never
    shorts, never fully to cash.

The edge is RISK BALANCING + diversification — NOT a directional bet (Wolf), NOT
crisis convexity (Rhino), NOT per-asset trend (Elephant). NOT a copy-trader.
Each book sizes its own basket and pushes signals via SenpiClient.push_signal();
runtime owns the LLM gate (pass-through), DSL exits, and all risk.guard_rails.

NOTE ON SIZING: Ox emits a DIFFERENT marginUsd per sleeve (the inverse-vol
weight) — its risk parity depends on the runtime honoring per-signal
signal.data.marginUsd, not collapsing everything to a flat strategy.margin_pct.

Environment / config resolution:
  OX_LEG            — REQUIRED. "core" or "ballast".
  SENPI_AUTH_TOKEN  — REQUIRED. Bearer token for MCP + signal POST.
  OX_CORE_WALLET    — core-book strategy wallet (or config.wallet)
  OX_BALLAST_WALLET — ballast-book strategy wallet (or config.wallet)
  OX_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ox_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "core" | "ballast"
SCANNER_NAME = f"ox_{LEG}_signals"
SIGNAL_TYPE = "OX_ALLWEATHER_CORE" if LEG == "core" else "OX_DEFENSIVE_BALLAST"

# Max raw score ~ 7. Used only for the 0..1 ingest-ranking score.
NORM_DIV = 8.0

# All-Weather sleeve basket — spans risk sleeves (crypto / equities / energy /
# industrial metal) AND defensive sleeves (gold / dollar / yen) so the core is
# genuinely diversified, not just long-everything-correlated.
_CORE_SLEEVES = [
    "BTC", "ETH", "SOL",
    "xyz:SP500", "xyz:XYZ100",
    "xyz:GOLD", "xyz:COPPER",
    "xyz:BRENTOIL",
    "xyz:DXY", "xyz:JPY",
]
# Defensive sleeves for the ballast book.
_BALLAST_DEFENSIVES = ["xyz:GOLD", "xyz:SILVER", "xyz:DXY", "xyz:JPY"]

# Risk-off lean probes (ballast budget scaler). Each votes risk-off on its 4h trend.
_RISKOFF_PROBES = [
    {"asset": "xyz:XYZ100", "fallback": "xyz:SP500", "risk_off_when": "BEARISH", "label": "equities"},
    {"asset": "xyz:GOLD", "fallback": None, "risk_off_when": "BULLISH", "label": "gold"},
    {"asset": "xyz:DXY", "fallback": None, "risk_off_when": "BULLISH", "label": "dollar"},
]

_DEFAULTS = {
    "core": {
        "sleeves": _CORE_SLEEVES,
        "portfolioBudgetPct": 0.60,    # gross deployed; risk parity runs moderate gross + cash buffer
        "maxWeightPct": 0.22,          # cap any single sleeve at 22% of equity (low-vol sleeve guard)
        "maxLeverage": 3,              # LOW — a core, not a bet; inverse-vol controls risk
        "maxSlots": 10,                # hold the whole sleeve basket
        "minNotionalUsd": 150,
        "tickSeconds": 600,            # low turnover — rebalance slowly
        "volBars": 30,                 # 1h bars for realized-vol estimate
        "minScore": 5,
        "riskOffThreshold": 2,         # (unused by core; kept for shared shape)
        "riskOffMultiplier": 1.0,
    },
    "ballast": {
        "sleeves": _BALLAST_DEFENSIVES,
        "portfolioBudgetPct": 0.18,    # small base — the always-on cushion
        "maxWeightPct": 0.30,
        "maxLeverage": 3,
        "maxSlots": 4,
        "minNotionalUsd": 150,
        "tickSeconds": 600,
        "volBars": 30,
        "minScore": 5,
        "riskOffThreshold": 1,         # light lean: 1 probe is enough to scale up
        "riskOffMultiplier": 2.0,      # double the defensive budget when risk-off confirms
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


def realized_vol(closes, n):
    """Per-bar realized volatility = stdev of pct returns over the last n bars.
    Relative magnitude is what matters for inverse-vol weighting."""
    window = closes[-(n + 1):] if len(closes) >= n + 1 else closes
    rets = [(window[i] / window[i - 1] - 1.0)
            for i in range(1, len(window)) if window[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def inverse_vol_weights(vols):
    """Risk-parity weights: w_i = (1/vol_i) / sum_j(1/vol_j). All-equal fallback
    if every vol is zero/degenerate. `vols` is {asset: realized_vol}."""
    inv = {a: (1.0 / v) for a, v in vols.items() if v and v > 0}
    tot = sum(inv.values())
    if tot <= 0:
        n = len(vols)
        return {a: (1.0 / n) for a in vols} if n else {}
    return {a: inv[a] / tot for a in inv}


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def get_universe_meta():
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


# ═══════════════════════════════════════════════════════════════
# Risk-off lean (ballast budget scaler only — NOT a hard gate)
# ═══════════════════════════════════════════════════════════════

def risk_off_lean(config):
    """Light cross-asset risk-off read used to SCALE the ballast budget up.
    The ballast book is always-on; this only decides base vs scaled budget."""
    threshold = int(config.get("riskOffThreshold", _DEFAULTS["riskOffThreshold"]))
    probes = config.get("riskOffProbes", _RISKOFF_PROBES)
    votes, detail = 0, {}
    for p in probes:
        asset = p.get("asset")
        md = fetch_candles(asset, ["4h"])
        if (not md or len(md["candles"].get("4h", [])) < 6) and p.get("fallback"):
            asset = p["fallback"]
            md = fetch_candles(asset, ["4h"])
        c4 = md["candles"].get("4h", []) if md else []
        if len(c4) < 6:
            detail[p["label"]] = "no_data"
            continue
        trend4, _ = trend_structure(c4)
        if trend4 == p.get("risk_off_when", "BEARISH"):
            votes += 1
            detail[p["label"]] = "risk_off"
        else:
            detail[p["label"]] = "calm"
    return {"risk_off": votes >= threshold, "votes": votes,
            "threshold": threshold, "detail": detail}


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


def push_signal(coin, score, reasons, margin_usd, leverage, weight, held_assets, extra):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    if coin.upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": score,
        "leverage": leverage,
        "marginUsd": margin_usd,        # PER-SLEEVE inverse-vol weight — must be honored
        "direction": "LONG",
        "reasons": reasons,
        "heldAssets": held_assets,
        "weightPct": round(weight * 100, 2),
    }
    data_block.update(extra or {})

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction="LONG",
            score=min(score / NORM_DIV, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {coin}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# Basket — the sleeve list for this leg, intersected with the live board
# ═══════════════════════════════════════════════════════════════

def build_basket(config, meta_map):
    sleeves = config.get("sleeves", _DEFAULTS["sleeves"])
    out = []
    for name in sleeves:
        if isinstance(name, str) and (meta_map.get(name) or meta_map.get(name.upper())):
            out.append(name)
    return out


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_ox_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_ox_producer_version": VERSION})
        return

    budget_pct = float(config.get("portfolioBudgetPct", _DEFAULTS["portfolioBudgetPct"]))
    max_weight = float(config.get("maxWeightPct", _DEFAULTS["maxWeightPct"]))
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = config.get("minNotionalUsd", _DEFAULTS["minNotionalUsd"])
    vol_bars = int(config.get("volBars", _DEFAULTS["volBars"]))
    min_score = config.get("minScore", _DEFAULTS["minScore"])

    # Ballast scales its defensive budget up on a confirmed risk-off lean.
    lean = None
    if LEG == "ballast":
        lean = risk_off_lean(config)
        if lean["risk_off"]:
            budget_pct = min(budget_pct * float(config.get("riskOffMultiplier",
                              _DEFAULTS["riskOffMultiplier"])), 0.6)
    budget_usd = account_value * budget_pct

    meta_map = get_universe_meta()
    basket = build_basket(config, meta_map)

    # ── PASS 1: realized vol over the FULL basket (held + un-held) ──
    # Weights MUST be computed over the whole basket, not just the names being
    # entered this tick — otherwise a single re-entry would get weight≈1.0 and
    # be sized to the entire budget.
    vols, metas, trends = {}, {}, {}
    for name in basket:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        md = fetch_candles(name, ["1h", "4h"])
        if not md:
            continue
        c1 = md["candles"].get("1h", [])
        c4 = md["candles"].get("4h", [])
        closes = [_close(c) for c in c1]
        if len(closes) < vol_bars + 1 or len(c4) < 6:
            continue
        v = realized_vol(closes, vol_bars)
        if v <= 0:
            continue
        vols[name] = v
        metas[name] = meta
        trends[name] = trend_structure(c4)

    if not vols:
        cfg.output({"status": "ok", "leg": LEG, "scanned": len(basket),
                    "candidates": 0, "signals_pushed": 0,
                    "note": "WAITING — no sleeve returned usable vol data",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_ox_producer_version": VERSION})
        return

    weights = inverse_vol_weights(vols)
    open_slots = max_slots - len(held_assets)

    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "basket full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "risk_off": (lean or {}).get("risk_off"),
                    "_ox_producer_version": VERSION})
        return

    # ── PASS 2: emit the un-held sleeves at their full-basket inverse-vol weight ──
    pushed, emitted, recently_skipped = 0, [], []
    # enter the largest-weight (lowest-vol) sleeves first
    for name in sorted(vols, key=lambda n: weights.get(n, 0), reverse=True):
        if pushed >= open_slots:
            break
        if name.upper() in held_set:
            continue
        if cfg.was_recently_signaled(name):
            recently_skipped.append(name)
            continue
        trend4, s4 = trends[name]
        # knife guard: don't ADD a sleeve in a hard downtrend (it stays in the
        # basket and is added once it stabilizes; the DSL holds existing ones)
        if trend4 == "BEARISH" and s4 >= 0.8:
            continue
        w = weights.get(name, 0)
        margin_usd = round(min(budget_usd * w, account_value * max_weight), 2)
        leverage = clamp_leverage(max_lev, metas[name])
        if margin_usd <= 0 or leverage <= 0 or margin_usd * leverage < min_notional:
            continue
        score = 6 + (1 if trend4 == "BULLISH" else 0)
        if score < min_score:
            continue
        reasons = [f"riskparity_w_{w:.0%}", f"vol_{vols[name]:.4f}", f"4h_{trend4.lower()}"]
        extra = {"vol": round(vols[name], 5)}
        if LEG == "ballast":
            extra["riskOff"] = bool((lean or {}).get("risk_off"))
        if push_signal(name, score, reasons, margin_usd, leverage, w, held_assets, extra):
            pushed += 1
            cfg.record_signal(name)
            emitted.append({"coin": name, "direction": "LONG", "score": score,
                            "leverage": leverage, "margin_usd": margin_usd,
                            "weight_pct": round(w * 100, 1)})

    out = {
        "status": "ok", "leg": LEG,
        "scanned": len(basket), "sized": len(vols),
        "open_slots": open_slots, "signals_pushed": pushed, "emitted": emitted,
        "budget_pct": round(budget_pct, 3), "budget_usd": round(budget_usd, 2),
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_ox_producer_version": VERSION,
    }
    if lean is not None:
        out["risk_off"] = lean["risk_off"]
        out["risk_off_detail"] = lean["detail"]
    if pushed == 0 and not emitted:
        out["note"] = "WAITING — basket already held or no un-held sleeve cleared sizing"
    cfg.output(out)


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"ox-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
