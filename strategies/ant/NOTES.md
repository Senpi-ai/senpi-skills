# Ant — Funding Harvester: what it is, what it isn't

Ant is the **best cash-and-carry Senpi can run today**. It is deployable and tested,
but you should understand exactly what it does and does not give you.

## The request vs. what's achievable
The original ask was true **delta-neutral cash-and-carry**: buy spot on HyperEVM +
short the perp at equal notional, collect funding with ~no price risk, exit when
funding decays, rebalance daily. Mapping it to Senpi:

| Step | Senpi | Notes |
|---|---|---|
| Rank top perps by open interest | ✅ | volume shortlist → `market_get_asset_data` OI |
| Funding > threshold (longs pay shorts) | ✅ | `market_get_funding_history` |
| **Buy spot on HyperEVM** | ❌ | **no spot execution primitive** — the delta-neutral leg |
| Short the perp | ✅ | perp `SHORT` signal |
| Target 30% APR | ✅ | funding-APR gate |
| Exit when funding < 0.002% | ⚠️ | approximated (24h hard-timeout + no-reopen); no funding-triggered close |
| Rebalance daily | ⚠️ | approximated by the 24h rotation; no delta-rebalance action |

Senpi automates **perps**; it can bridge USDC to HyperEVM but cannot swap it into a
spot token. So the hedge that makes cash-and-carry riskless is exactly what it can't
place.

## What ant actually is
A **directional funding-carry short with an exhaustion gate.** It shorts high-OI
perps paying rich positive funding, **but only when the long crowd is exhausted**
(overbought RSI, 4h structure rolling over, 1h momentum fading) — never a name still
ripping (that's the steamroller a naive funding short gets flattened by). It collects
the hourly funding while a tight Phase-1 stop (8%) and low leverage (2–4×) bound the
squeeze risk.

**The residual risk you are NOT hedged against:** the perp going *up*. Positive
funding often clusters on names that squeeze; the exhaustion gate + tight stop reduce
this but do not remove it the way a long-spot leg would. Size accordingly — the
harvestable yield is real but it is **gross funding minus directional PnL minus
fees**, not the riskless basis of a true carry.

## How "exit on funding decay" and "rebalance daily" are handled
The runtime's exit engine is price-action DSL — it can't close a position because
funding dropped. Ant approximates the intent:
- **24h `hard_timeout`** force-closes every position daily.
- On re-scan, a name is only re-shorted if it **still clears the APR + persistence +
  exhaustion gates** — so a decayed-funding name simply isn't re-opened (rotate-by-
  attrition). Freed slots go to the current best funding-payers.

This is a daily rotation, not an instant funding-threshold close.

## Precision on the numbers
Hyperliquid funds **hourly**, not on an 8h cycle. `funding_apr = rate_hourly × 24 ×
365`. `targetApr: 30` means ~0.00342%/hr. Set thresholds in HL's hourly terms.

## What would make it the *real* (delta-neutral) strategy
Same primitives crane needs, plus spot:
1. **Spot execution** — buy/sell spot (HyperEVM swap or HL-native spot) from the
   scanner/runtime, so the long-spot hedge can be placed.
2. **Cross-venue joint position + coordinated close** — manage `{spot long, perp
   short}` as one delta-neutral unit and close both on funding decay.
3. **Funding/delta-triggered rebalance action** — a non-price-action exit/rebalance.

With (1)–(3), ant's `build_signal` (which already selects the names and directions)
feeds a true delta-neutral carry unchanged. Until then, this is the harvestable,
honestly-labeled directional version.
