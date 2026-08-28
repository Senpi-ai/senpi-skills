#!/usr/bin/env python3
"""
Market Regime Detector — reads candle data from Senpi to determine
directional bias per asset and overall market.

Analyzes: 1h, 4h, 1d candles
Outputs: JSON with per-asset and aggregate bias

Methodology:
- EMA crossovers (fast/slow) at each timeframe
- Higher highs / lower lows structure
- Trend strength (ADX-like via directional movement)
- Multi-timeframe alignment score

Bias output: BULLISH / BEARISH / NEUTRAL with confidence 0-100
"""

import subprocess, json, sys, time
from datetime import datetime, timezone

def get_candles(asset, dex=None):
    """Fetch 1h, 4h, 1d candles for an asset."""
    cmd = ['mcporter', 'call', 'senpi', 'market_get_asset_data',
           f'asset={asset}', 'candle_intervals=["1h","4h","1d"]',
           'include_order_book=false', 'include_funding=false']
    if dex:
        cmd.append(f'dex={dex}')
    
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout)
        if d.get('success'):
            return d['data'].get('candles', {})
    except Exception as e:
        print(f"Error fetching {asset}: {e}", file=sys.stderr)
    return None

def ema(values, period):
    """Calculate EMA."""
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result

def analyze_timeframe(candles, label):
    """Analyze a single timeframe's candles for trend."""
    if not candles or len(candles) < 20:
        return {"bias": "NEUTRAL", "confidence": 0, "reason": "insufficient data"}
    
    closes = [float(c['c']) for c in candles]
    highs = [float(c['h']) for c in candles]
    lows = [float(c['l']) for c in candles]
    
    signals = []
    
    # 1. EMA crossover (9/21)
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema_diff_pct = (ema9 - ema21) / ema21 * 100
    
    if ema9 > ema21:
        signals.append(("ema_cross", "BULLISH", min(abs(ema_diff_pct) * 20, 30)))
    else:
        signals.append(("ema_cross", "BEARISH", min(abs(ema_diff_pct) * 20, 30)))
    
    # 2. Price vs EMA21 (trend filter)
    current = closes[-1]
    if current > ema21:
        signals.append(("price_vs_ema", "BULLISH", 15))
    else:
        signals.append(("price_vs_ema", "BEARISH", 15))
    
    # 3. Higher highs / lower lows (structure)
    # Look at last 5 swing points
    recent_highs = highs[-10:]
    recent_lows = lows[-10:]
    
    hh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i-1])
    ll_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] < recent_lows[i-1])
    hl_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i-1])
    lh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] < recent_highs[i-1])
    
    bull_structure = hh_count + hl_count
    bear_structure = ll_count + lh_count
    
    if bull_structure > bear_structure + 2:
        signals.append(("structure", "BULLISH", 25))
    elif bear_structure > bull_structure + 2:
        signals.append(("structure", "BEARISH", 25))
    else:
        signals.append(("structure", "NEUTRAL", 5))
    
    # 4. Momentum (rate of change over last 5 candles)
    if len(closes) >= 6:
        roc = (closes[-1] - closes[-6]) / closes[-6] * 100
        if roc > 0.5:
            signals.append(("momentum", "BULLISH", min(abs(roc) * 5, 20)))
        elif roc < -0.5:
            signals.append(("momentum", "BEARISH", min(abs(roc) * 5, 20)))
        else:
            signals.append(("momentum", "NEUTRAL", 5))
    
    # 5. Candle body analysis (last 5 candles — are they mostly green or red?)
    recent = candles[-5:]
    green = sum(1 for c in recent if float(c['c']) > float(c['o']))
    red = sum(1 for c in recent if float(c['c']) < float(c['o']))
    if green >= 4:
        signals.append(("candle_color", "BULLISH", 10))
    elif red >= 4:
        signals.append(("candle_color", "BEARISH", 10))
    else:
        signals.append(("candle_color", "NEUTRAL", 3))
    
    # Aggregate
    bull_score = sum(conf for _, bias, conf in signals if bias == "BULLISH")
    bear_score = sum(conf for _, bias, conf in signals if bias == "BEARISH")
    
    if bull_score > bear_score + 10:
        bias = "BULLISH"
        confidence = min(bull_score, 100)
    elif bear_score > bull_score + 10:
        bias = "BEARISH"
        confidence = min(bear_score, 100)
    else:
        bias = "NEUTRAL"
        confidence = max(bull_score, bear_score)
    
    return {
        "bias": bias,
        "confidence": confidence,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "ema9": round(ema9, 4),
        "ema21": round(ema21, 4),
        "ema_diff_pct": round(ema_diff_pct, 4),
        "current_price": current,
        "signals": {name: {"bias": b, "weight": w} for name, b, w in signals}
    }

def analyze_asset(asset, dex=None):
    """Full multi-timeframe analysis for one asset."""
    candles = get_candles(asset, dex)
    if not candles:
        return {"asset": asset, "error": "no data"}
    
    results = {}
    tf_weights = {"1h": 0.2, "4h": 0.35, "1d": 0.45}  # Higher TF = more weight
    
    for tf in ["1h", "4h", "1d"]:
        if tf in candles and candles[tf]:
            results[tf] = analyze_timeframe(candles[tf], tf)
        else:
            results[tf] = {"bias": "NEUTRAL", "confidence": 0}
    
    # Weighted composite
    composite_bull = 0
    composite_bear = 0
    for tf, weight in tf_weights.items():
        if tf in results:
            r = results[tf]
            if r["bias"] == "BULLISH":
                composite_bull += r["confidence"] * weight
            elif r["bias"] == "BEARISH":
                composite_bear += r["confidence"] * weight
    
    # Alignment bonus: if all timeframes agree, boost confidence
    biases = [results[tf]["bias"] for tf in ["1h", "4h", "1d"] if tf in results]
    aligned = len(set(biases)) == 1 and biases[0] != "NEUTRAL"
    
    if aligned:
        composite_bull *= 1.3
        composite_bear *= 1.3
    
    if composite_bull > composite_bear + 5:
        overall = "BULLISH"
        overall_conf = min(round(composite_bull), 100)
    elif composite_bear > composite_bull + 5:
        overall = "BEARISH"
        overall_conf = min(round(composite_bear), 100)
    else:
        overall = "NEUTRAL"
        overall_conf = round(max(composite_bull, composite_bear))
    
    return {
        "asset": asset,
        "dex": dex,
        "overall_bias": overall,
        "overall_confidence": overall_conf,
        "aligned": aligned,
        "composite_bull": round(composite_bull, 1),
        "composite_bear": round(composite_bear, 1),
        "timeframes": results
    }

def main():
    # Analyze key market assets for overall regime
    # Plus any specific assets passed as args
    
    # Core market indicators
    core_assets = [
        ("BTC", None),
        ("ETH", None),
    ]
    
    # Additional assets from args
    extra = []
    for arg in sys.argv[1:]:
        if ":" in arg:
            parts = arg.split(":")
            extra.append((parts[1], parts[0]))  # xyz:SILVER -> (SILVER, xyz)
        else:
            extra.append((arg, None))
    
    all_assets = core_assets + extra
    
    results = []
    for asset, dex in all_assets:
        r = analyze_asset(asset, dex)
        results.append(r)
        time.sleep(0.2)  # Rate limit
    
    # Overall market regime (BTC + ETH weighted)
    btc = next((r for r in results if r["asset"] == "BTC"), None)
    eth = next((r for r in results if r["asset"] == "ETH"), None)
    
    market_bull = 0
    market_bear = 0
    if btc and "error" not in btc:
        market_bull += btc["composite_bull"] * 0.6
        market_bear += btc["composite_bear"] * 0.6
    if eth and "error" not in eth:
        market_bull += eth["composite_bull"] * 0.4
        market_bear += eth["composite_bear"] * 0.4
    
    if market_bull > market_bear + 5:
        market_regime = "BULLISH"
    elif market_bear > market_bull + 5:
        market_regime = "BEARISH"
    else:
        market_regime = "NEUTRAL"
    
    output = {
        "time": datetime.now(timezone.utc).isoformat(),
        "market_regime": market_regime,
        "market_bull": round(market_bull, 1),
        "market_bear": round(market_bear, 1),
        "assets": results,
        "direction_recommendation": {
            "LONG": "ALLOWED" if market_regime in ["BULLISH", "NEUTRAL"] else "CAUTION",
            "SHORT": "ALLOWED" if market_regime in ["BEARISH", "NEUTRAL"] else "CAUTION",
            "note": f"Market regime is {market_regime}. {'Counter-trend trades need 2x score threshold.' if market_regime != 'NEUTRAL' else 'No directional bias — both sides OK.'}"
        }
    }
    
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
