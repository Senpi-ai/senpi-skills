# Scanner Details

## 1. Compression Scanner (`compression-scanner.py`)

**Pattern**: COMPRESSION_BREAKOUT — BB squeeze with OI confirmation.

**Logic**: Finds assets where 4h BB width is below the `min_bb_squeeze_percentile` (tight bands = coiled spring). When price breaks above/below 1h BB bands, that's the breakout signal.

**Confluence factors** (weights):
- `bb_squeeze` (0.25): 4h BB width below threshold percentile
- `breakout` (0.25): Price breaking upper/lower 1h BB
- `oi_building` (0.20): OI rising >5% over 1h (accumulation)
- `oi_price_diverge` (0.15): OI rising but price flat (spring loading)
- `volume_surge` (0.15): Short-term volume >1.5x long-term average
- `rsi_not_extreme` (0.10): RSI between 30-70 (not already extended)
- `funding_aligned` (0.10): Funding rate favors trade direction
- `atr_expanding` (0.05): ATR >2% (volatility confirming)

**Output**: Reports signals in squeeze (watching) and breaking out (actionable). Direction from breakout direction.

## 2. Correlation Scanner (`correlation-scanner.py`)

**Pattern**: CORRELATION_LAG — Alts lagging behind a significant BTC move.

**Logic**: When BTC moves >2% in 1-4h, scans high-correlation alts to find those that haven't caught up. The "lag ratio" measures what fraction of BTC's move the alt missed. Higher lag ratio = better entry window.

**Window quality**:
- STRONG: lag_ratio > 0.7 (alt moved <30% of BTC's move)
- MODERATE: 0.5-0.7
- CLOSING: 0.4-0.5 (alt starting to catch up)

**Confluence factors** (weights):
- `btc_significant_move` (0.20): BTC moved past threshold
- `alt_lagging` (0.25): Lag ratio ≥ 0.5
- `volume_not_spiked` (0.15): Alt volume still quiet (move hasn't started)
- `rsi_safe` (0.10): RSI not at extremes
- `sm_aligned` (0.15): Smart money direction matches
- `high_correlation_alt` (0.10): Asset in known high-corr list
- `sufficient_leverage` (0.05): Enough leverage available

**Speed optimization**: Scans top 10 high-corr alts first, only extends to 20 if no strong signals found.

## 3. Momentum Scanner (`momentum-scanner.py`)

**Pattern**: MOMENTUM_BREAKOUT — Strong price moves with volume confirmation.

**Logic**: Finds assets with >1.5% 1h move or >2.5% 2h move, confirmed by volume surge and 4h trend alignment. Simpler than compression — doesn't need BB squeeze.

**Confluence factors** (weights):
- `strong_1h_move` (0.25): >1.5% in last hour
- `strong_2h_move` (0.15): >2.5% in last 2 hours
- `volume_surge` (0.20): Volume ratio >1.5x
- `trend_aligned_4h` (0.15): Move aligns with 4h direction
- `rsi_not_extreme` (0.10): Not overbought/oversold
- `sma_aligned` (0.10): Price on correct side of 20 SMA
- `good_atr` (0.05): ATR >1.5%

**DSL note**: Use tighter Phase 1 retrace (0.012) for momentum positions.

## 4. Reversion Scanner (`reversion-scanner.py`)

**Pattern**: MEAN_REVERSION — Overextended assets with exhaustion signals.

**Logic**: Finds assets with RSI extremes on 4h (>75 overbought, <25 oversold), then confirms with divergence, volume exhaustion, and BB band extremes. Trades counter-trend expecting reversion to mean.

**Confluence factors** (weights):
- `rsi_extreme_4h` (0.20): RSI at extreme (required filter)
- `rsi_extreme_1h` (0.15): 1h RSI also extreme
- `divergence` (0.20): RSI divergence aligned with reversal
- `price_extended` (0.10): >10% move in 24h
- `volume_exhaustion` (0.15): Declining volume on extension
- `at_extreme_bb` (0.10): Price beyond BB bands
- `oi_crowded` (0.15): OI 15%+ above average (crowded trade)
- `funding_pays_us` (0.10): We collect funding in our direction

## 5. Funding Scanner (`funding-scanner.py`)

**Pattern**: FUNDING_ARB — Extreme funding rate with directional alignment.

**Logic**: Finds assets with annualized funding >30%. Goes opposite to the crowd to collect funding (positive funding → short, negative → long). Checks technical alignment to avoid fighting trend.

**Confluence factors** (weights):
- `extreme_funding` (0.25): Funding above threshold (required filter)
- `trend_aligned` (0.20): SMA20 trend supports direction
- `rsi_safe` (0.15): RSI not extreme against us
- `oi_stable` (0.15): OI hasn't collapsed (funding source stable)
- `sm_aligned` (0.10): Smart money on our side
- `high_daily_yield` (0.10): >5% daily yield on margin
- `volume_healthy` (0.05): >$10M daily volume

**DSL note**: Use wider retrace tiers — funding positions need room; the edge is income over time, not price direction.

**Risk**: Monitor for funding reversal. `risk-guardian.py` auto-flags FUNDING_ARB positions if funding flips or weakens below 10% annualized.

## 6. OI Tracker (`oi-tracker.py`)

**Not a signal scanner** — data collection only. Samples OI for all liquid assets every 5 minutes, stores 24h history (288 entries per asset). Used by compression and reversion scanners for OI change analysis.
