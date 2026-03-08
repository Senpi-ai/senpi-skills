# Trend Guard

Lightweight hourly trend classifier for Hyperliquid assets. One MCP call, < 3 seconds, outputs UP/DOWN/NEUTRAL with a strength score (0–100).

## Usage

```bash
# Classify trend
TREND_ASSET=HYPE python3 scripts/trend-guard.py

# Check alignment with a trade direction
TREND_ASSET=HYPE TREND_DIRECTION=LONG python3 scripts/trend-guard.py
```

## Output

```json
{
  "asset": "HYPE",
  "trend": "DOWN",
  "strength": 72,
  "ema5": 28.45,
  "ema13": 28.92,
  "aligned": false,
  "alignedDirection": "LONG",
  "candles_used": 24,
  "timestamp": "2026-03-05T12:00:00Z"
}
```

## Import in Another Skill

```python
import sys, os
sys.path.insert(0, "/path/to/trend-guard/scripts")
from trend_guard import classify_trend

result = classify_trend("HYPE", direction="LONG")
if not result.get("aligned", True) and result.get("strength", 0) > 50:
    # Counter-trend with conviction — skip or downgrade
    pass
```

## Rule

`aligned: false` + `strength > 50` = counter-trend with conviction. Skip the entry.

## Changelog

### v1.0
- Initial release
- EMA-5/13 crossover + swing high/low structure classification
- Strength score: EMA separation + swing consistency + volume confirmation
- Importable as Python module (`from trend_guard import classify_trend`)
- Standalone via `TREND_ASSET` env var
