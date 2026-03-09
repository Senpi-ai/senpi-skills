/**
 * Position Sizer — Leverage calculation.
 *
 * Ports: wolf_config.py → calculate_leverage()
 *
 * leverage = maxLev × (rangeLow + (rangeHigh - rangeLow) × conviction)
 * Clamped to [1, maxLev].
 */

import { TradingRisk, RISK_LEVERAGE_RANGES } from '../../core/types.js';

/**
 * Calculate leverage as a fraction of max leverage, scaled by risk tier and conviction.
 *
 * @param maxLeverage - Asset's maximum allowed leverage
 * @param tradingRisk - Risk tier: conservative, moderate, or aggressive
 * @param conviction - 0.0 to 1.0, where within the risk range to land
 * @returns Integer leverage, clamped to [1, maxLeverage]
 */
export function calculateLeverage(
  maxLeverage: number,
  tradingRisk: TradingRisk = 'moderate',
  conviction = 0.5,
): number {
  const [minPct, maxPct] = RISK_LEVERAGE_RANGES[tradingRisk] ?? RISK_LEVERAGE_RANGES.moderate;
  const rangeMin = maxLeverage * minPct;
  const rangeMax = maxLeverage * maxPct;
  const leverage = rangeMin + (rangeMax - rangeMin) * conviction;
  return Math.min(Math.max(1, Math.round(leverage)), maxLeverage);
}
