import { describe, it, expect } from 'vitest';
import { buildDslState, DEFAULT_TIERS } from '../dsl-defaults.js';
import type { TigerPattern, CreateDslParams } from '../types.js';

const BASE_PARAMS: CreateDslParams = {
  asset: 'ETH',
  direction: 'LONG',
  entryPrice: 3500,
  size: 1.5,
  leverage: 10,
  wallet: '0xabc',
  pattern: 'COMPRESSION_BREAKOUT',
};

describe('dsl-defaults', () => {
  describe('DEFAULT_TIERS', () => {
    it('has 4 tiers', () => {
      expect(DEFAULT_TIERS).toHaveLength(4);
    });

    it('tiers have increasing triggerPct', () => {
      for (let i = 1; i < DEFAULT_TIERS.length; i++) {
        expect(DEFAULT_TIERS[i].triggerPct).toBeGreaterThan(
          DEFAULT_TIERS[i - 1].triggerPct,
        );
      }
    });

    it('tiers have decreasing retrace', () => {
      for (let i = 1; i < DEFAULT_TIERS.length; i++) {
        expect(DEFAULT_TIERS[i].retrace).toBeLessThan(
          DEFAULT_TIERS[i - 1].retrace,
        );
      }
    });
  });

  describe('buildDslState', () => {
    it('builds valid state with defaults', () => {
      const state = buildDslState(BASE_PARAMS);

      expect(state.active).toBe(true);
      expect(state.asset).toBe('ETH');
      expect(state.direction).toBe('LONG');
      expect(state.entryPrice).toBe(3500);
      expect(state.size).toBe(1.5);
      expect(state.leverage).toBe(10);
      expect(state.wallet).toBe('0xabc');
      expect(state.highWaterPrice).toBe(3500);
      expect(state.phase).toBe(1);
      expect(state.currentBreachCount).toBe(0);
      expect(state.currentTierIndex).toBe(-1);
      expect(state.tierFloorPrice).toBeNull();
      expect(state.pendingClose).toBe(false);
      expect(state.phase2TriggerTier).toBe(0);
      expect(state.tiers).toHaveLength(4);
      expect(state.lastPrice).toBe(3500);
      expect(state.consecutiveFetchFailures).toBe(0);
    });

    it('sets timestamps', () => {
      const before = new Date().toISOString();
      const state = buildDslState(BASE_PARAMS);
      const after = new Date().toISOString();

      expect(state.createdAt >= before).toBe(true);
      expect(state.createdAt <= after).toBe(true);
      expect(state.updatedAt).toBe(state.createdAt);
      expect(state.lastCheck).toBe(state.createdAt);
    });

    it('copies tiers (not references)', () => {
      const state = buildDslState(BASE_PARAMS);
      state.tiers[0].retrace = 999;
      expect(DEFAULT_TIERS[0].retrace).toBe(0.015);
    });
  });

  describe('absolute floor — LONG', () => {
    it('calculates 2% below entry for LONG', () => {
      const state = buildDslState({ ...BASE_PARAMS, direction: 'LONG', entryPrice: 1000 });
      expect(state.phase1.absoluteFloor).toBe(980); // 1000 * (1 - 0.02)
    });

    it('respects custom absolute floor override', () => {
      const state = buildDslState({
        ...BASE_PARAMS,
        direction: 'LONG',
        entryPrice: 1000,
        absoluteFloor: 950,
      });
      expect(state.phase1.absoluteFloor).toBe(950);
    });
  });

  describe('absolute floor — SHORT', () => {
    it('calculates 2% above entry for SHORT', () => {
      const state = buildDslState({ ...BASE_PARAMS, direction: 'SHORT', entryPrice: 1000 });
      expect(state.phase1.absoluteFloor).toBe(1020); // 1000 * (1 + 0.02)
    });

    it('respects custom absolute floor override', () => {
      const state = buildDslState({
        ...BASE_PARAMS,
        direction: 'SHORT',
        entryPrice: 1000,
        absoluteFloor: 1050,
      });
      expect(state.phase1.absoluteFloor).toBe(1050);
    });
  });

  describe('pattern tuning', () => {
    const PATTERNS: TigerPattern[] = [
      'COMPRESSION_BREAKOUT',
      'CORRELATION_LAG',
      'MOMENTUM_BREAKOUT',
      'MEAN_REVERSION',
      'FUNDING_ARB',
    ];

    it.each(PATTERNS)('produces valid state for %s', (pattern) => {
      const state = buildDslState({ ...BASE_PARAMS, pattern });
      expect(state.active).toBe(true);
      expect(state.phase1.retraceThreshold).toBeGreaterThan(0);
      expect(state.phase2.retraceThreshold).toBeGreaterThan(0);
      expect(state.breachDecay).toBe('soft');
      expect(state.phase1.consecutiveBreachesRequired).toBe(3);
      expect(state.phase2.consecutiveBreachesRequired).toBe(2);
    });

    it('COMPRESSION_BREAKOUT uses standard retrace', () => {
      const state = buildDslState({ ...BASE_PARAMS, pattern: 'COMPRESSION_BREAKOUT' });
      expect(state.phase1.retraceThreshold).toBe(0.015);
      expect(state.phase2.retraceThreshold).toBe(0.012);
    });

    it('MOMENTUM_BREAKOUT uses tighter retrace', () => {
      const state = buildDslState({ ...BASE_PARAMS, pattern: 'MOMENTUM_BREAKOUT' });
      expect(state.phase1.retraceThreshold).toBe(0.012);
      expect(state.phase2.retraceThreshold).toBe(0.010);
    });

    it('FUNDING_ARB uses wider retrace', () => {
      const state = buildDslState({ ...BASE_PARAMS, pattern: 'FUNDING_ARB' });
      expect(state.phase1.retraceThreshold).toBe(0.020);
      expect(state.phase2.retraceThreshold).toBe(0.015);
    });
  });
});
