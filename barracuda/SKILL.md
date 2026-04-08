---
name: barracuda-strategy
description: >-
  BARRACUDA v1.0.1 — Funding Decay Collector. Finds assets where extreme funding
  has persisted for 6+ hours, confirmed by SM alignment from Hyperfeed and 4H
  trend structure. Enters to collect funding while riding the trend. Double edge:
  price appreciation + funding income from trapped traders on the other side.
  Fixes Croc's -42.7% failure by requiring SM and trend confirmation before entering.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0.1"
  platform: senpi
  exchange: hyperliquid
---

# 🐟 BARRACUDA v1.0.1 — Funding Decay Collector

Collect funding from trapped traders while riding the confirmed trend.

## Why BARRACUDA Exists

Croc tried pure funding arbitrage and lost -42.7% on 535 trades. The thesis (collect extreme funding) was sound — the execution was broken. Croc entered funding trades without checking SM direction or trend structure. When funding flipped, Croc was counter-trend and lost on both price AND funding.

Barracuda fixes this with hard gates: extreme funding must be persistent (6+ hours), SM must align, and 4H trend must confirm. You only collect funding when the market agrees with your direction.

## Six-Gate Entry Model

1. **Extreme funding** — annualized 30%+ (shorts paying longs, or vice versa)
2. **Persistent** — same direction for 6+ consecutive hours (not a spike)
3. **SM aligned** — Hyperfeed shows SM traders positioned in our direction
4. **4H trend confirms** — SMA20 slope agrees with entry direction
5. **RSI safe** — not entering overbought longs or oversold shorts
6. **Leverage >= 5x** — need enough leverage for funding to be meaningful

## The Double Edge

When all gates pass, you're collecting funding AND riding a trend:
- Funding income: at 30% annualized and 10x leverage, that's ~8.2% of margin per day
- Price appreciation: SM-confirmed trend provides directional alpha
- Trapped traders: the funding rate IS the signal — other side is paying to hold losing positions

## DSL Configuration

Patient stops — funding positions need time to accumulate yield:
- Phase 2 trigger at 5% ROE (earlier than momentum skills)
- Stagnation TP at 8% ROE / 120 min (very patient — funding accumulates)
- Dead weight cut at 30 min (don't hold a position that never goes green)
- Standard 7/12/15/20% tier locks

## Notification Policy (STRICT)

**ONLY alert:** Position OPENED (asset, direction, funding rate, daily yield, persistence hours), position CLOSED (DSL or thesis exit with reason), risk guardian triggered, critical error.

**NEVER alert:** Scanner found nothing, funding history updated, extreme funding detected but didn't qualify, scanner ran successfully, DSL routine check, any reasoning or analysis. If you didn't open or close a position, the user should not hear from you. Silence means working.

All crons MUST use `NO_REPLY` for idle cycles. Do not narrate scan results.

## Expected Trade Frequency

Barracuda is a PATIENT strategy. Extreme funding that persists for 6+ hours with SM alignment and trend confirmation is rare. Expected:
- **0-2 trades per day** in normal markets
- **0 trades for multiple days** is normal during low-funding periods
- **Max 3 entries per day** hard cap
- If you're taking 5+ trades per day, something is wrong — the persistence gate should prevent this

## Files

| File | Purpose |
|---|---|
| `scripts/barracuda-scanner.py` | Six-gate funding scanner with persistence tracking |
| `scripts/barracuda_config.py` | Config helper (from Tiger) |
| `scripts/barracuda_lib.py` | Technical analysis library (from Tiger) |
| `config/barracuda-config.json` | Parameters |

## License

MIT — Built by Senpi (https://senpi.ai).
