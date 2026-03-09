import { describe, it, expect } from 'vitest';
import { calculateLeverage } from '../src/primitives/risk/position-sizer.js';
import { RISK_LEVERAGE_RANGES } from '../src/core/types.js';

describe('Position Sizer — calculateLeverage()', () => {
  it('returns integer leverage', () => {
    const result = calculateLeverage(20, 'aggressive', 0.7);
    expect(Number.isInteger(result)).toBe(true);
  });

  it('clamps to minimum of 1', () => {
    const result = calculateLeverage(1, 'conservative', 0.0);
    expect(result).toBeGreaterThanOrEqual(1);
  });

  it('clamps to maximum leverage', () => {
    const result = calculateLeverage(5, 'aggressive', 1.0);
    expect(result).toBeLessThanOrEqual(5);
  });

  it('aggressive uses higher leverage than conservative', () => {
    const aggressive = calculateLeverage(20, 'aggressive', 0.5);
    const conservative = calculateLeverage(20, 'conservative', 0.5);
    expect(aggressive).toBeGreaterThan(conservative);
  });

  it('higher conviction gives higher leverage', () => {
    const low = calculateLeverage(20, 'moderate', 0.1);
    const high = calculateLeverage(20, 'moderate', 0.9);
    expect(high).toBeGreaterThanOrEqual(low);
  });

  it('moderate is between conservative and aggressive', () => {
    const con = calculateLeverage(20, 'conservative', 0.5);
    const mod = calculateLeverage(20, 'moderate', 0.5);
    const agg = calculateLeverage(20, 'aggressive', 0.5);
    expect(mod).toBeGreaterThanOrEqual(con);
    expect(mod).toBeLessThanOrEqual(agg);
  });

  it('produces correct leverage for FIRST_JUMP aggressive signal', () => {
    // max_lev=20, aggressive, conviction=0.9 (FIRST_JUMP)
    // range: [0.50, 0.75] of 20 = [10, 15]
    // leverage = 10 + (15-10) * 0.9 = 10 + 4.5 = 14.5 → round = 15
    const result = calculateLeverage(20, 'aggressive', 0.9);
    expect(result).toBe(15);
  });

  it('produces correct leverage for DEEP_CLIMBER conservative signal', () => {
    // max_lev=20, conservative, conviction=0.5 (DEEP_CLIMBER)
    // range: [0.15, 0.25] of 20 = [3, 5]
    // leverage = 3 + (5-3) * 0.5 = 3 + 1 = 4
    const result = calculateLeverage(20, 'conservative', 0.5);
    expect(result).toBe(4);
  });

  it('defaults to moderate when unknown risk tier', () => {
    // @ts-expect-error testing invalid input
    const result = calculateLeverage(20, 'unknown', 0.5);
    // Falls back to moderate: [0.25, 0.50] of 20 = [5, 10]
    // leverage = 5 + (10-5) * 0.5 = 7.5 → round = 8
    expect(result).toBe(8);
  });

  it('leverage range constants are correct', () => {
    expect(RISK_LEVERAGE_RANGES.conservative).toEqual([0.15, 0.25]);
    expect(RISK_LEVERAGE_RANGES.moderate).toEqual([0.25, 0.50]);
    expect(RISK_LEVERAGE_RANGES.aggressive).toEqual([0.50, 0.75]);
  });
});
