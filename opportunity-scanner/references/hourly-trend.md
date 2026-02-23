### v5: Hourly Trend Classification

The 1h candle data is analyzed for swing structure to classify the hourly trend:

```python
def classify_hourly_trend(candles_1h):
    """
    Analyze last 12-24 hourly candles for higher-high/higher-low
    or lower-high/lower-low structure.
    
    Returns: "UP", "DOWN", or "NEUTRAL"
    """
    if len(candles_1h) < 8:
        return "NEUTRAL"
    
    # Find swing highs and lows (lookback=3)
    highs = [float(c["h"]) for c in candles_1h]
    lows = [float(c["l"]) for c in candles_1h]
    
    swing_highs = []
    swing_lows = []
    for i in range(3, len(candles_1h) - 3):
        if highs[i] == max(highs[i-3:i+4]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-3:i+4]):
            swing_lows.append((i, lows[i]))
    
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL"
    
    # Check last 2-3 swing points
    recent_highs = [h for _, h in swing_highs[-3:]]
    recent_lows = [l for _, l in swing_lows[-3:]]
    
    higher_highs = all(recent_highs[i] > recent_highs[i-1] 
                       for i in range(1, len(recent_highs)))
    higher_lows = all(recent_lows[i] > recent_lows[i-1] 
                      for i in range(1, len(recent_lows)))
    lower_highs = all(recent_highs[i] < recent_highs[i-1] 
                      for i in range(1, len(recent_highs)))
    lower_lows = all(recent_lows[i] < recent_lows[i-1] 
                     for i in range(1, len(recent_lows)))
    
    if higher_highs and higher_lows:
        return "UP"
    elif lower_highs and lower_lows:
        return "DOWN"
    elif higher_highs or higher_lows:
        return "UP"  # Weak uptrend
    elif lower_highs or lower_lows:
        return "DOWN"  # Weak downtrend
    else:
        return "NEUTRAL"
```
