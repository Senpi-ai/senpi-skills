import { describe, it, expect } from 'vitest';
import { dslTick } from '../src/primitives/exits/dsl-engine.js';
import { DslState, DslConfig, roundTo } from '../src/core/types.js';
import fixtures from './fixtures/dsl-states.json';

// Helper to create a state from fixture with overrides
function makeState(base: keyof typeof fixtures, overrides: Partial<DslState> = {}): DslState {
  return { ...(fixtures[base] as unknown as DslState), ...overrides };
}

const NOW = new Date('2026-03-08T10:30:00Z');
const CREATED = '2026-03-08T10:00:00Z';

describe('DSL Engine — dslTick()', () => {
  // ─── Phase 1 LONG: Basic Hold ───
  describe('Phase 1 LONG', () => {
    it('holds when price above entry and above floor', () => {
      const state = makeState('baseState');
      const result = dslTick(state, 101.0, NOW);

      expect(result.action).toBe('hold');
      expect(result.metrics.upnlPct).toBeGreaterThan(0);
      expect(result.metrics.breached).toBe(false);
      expect(result.metrics.breachCount).toBe(0);
    });

    it('holds when price below entry but above floor', () => {
      const state = makeState('baseState');
      const result = dslTick(state, 99.5, NOW);

      expect(result.action).toBe('hold');
      expect(result.metrics.upnlPct).toBeLessThan(0);
      expect(result.metrics.breached).toBe(false);
    });

    it('records a breach when price at floor', () => {
      const state = makeState('baseState');
      // absoluteFloor = 100 * (1 - 0.1/10) = 99.0
      const result = dslTick(state, 99.0, NOW);

      expect(result.metrics.breached).toBe(true);
      expect(result.metrics.breachCount).toBe(1);
      expect(result.action).toBe('hold'); // needs 3 breaches
    });

    it('records a breach when price below floor', () => {
      const state = makeState('baseState');
      const result = dslTick(state, 98.5, NOW);

      expect(result.metrics.breached).toBe(true);
      expect(result.metrics.breachCount).toBe(1);
      expect(result.action).toBe('hold'); // needs 3 breaches
    });

    it('closes after 3 consecutive breaches', () => {
      let state = makeState('baseState');

      // Breach 1
      let result = dslTick(state, 98.5, NOW);
      expect(result.metrics.breachCount).toBe(1);
      state = result.updatedState;

      // Breach 2
      result = dslTick(state, 98.5, new Date('2026-03-08T10:33:00Z'));
      expect(result.metrics.breachCount).toBe(2);
      state = result.updatedState;

      // Breach 3 — should close
      result = dslTick(state, 98.5, new Date('2026-03-08T10:36:00Z'));
      expect(result.metrics.breachCount).toBe(3);
      expect(result.action).toBe('close');
      expect(result.closeReason).toContain('DSL breach');
    });

    it('resets breach count on hard decay when price recovers', () => {
      let state = makeState('baseState');

      // Breach 1
      let result = dslTick(state, 98.5, NOW);
      expect(result.metrics.breachCount).toBe(1);
      state = result.updatedState;

      // Recovery — price above floor
      result = dslTick(state, 100.0, new Date('2026-03-08T10:33:00Z'));
      expect(result.metrics.breachCount).toBe(0); // hard reset
    });

    it('soft-decays breach count when configured', () => {
      let state = makeState('baseState', { breachDecay: 'soft', currentBreachCount: 2 });

      const result = dslTick(state, 100.0, NOW);
      expect(result.metrics.breachCount).toBe(1); // soft decay: 2-1=1
    });

    it('updates high-water mark when price rises', () => {
      const state = makeState('baseState');
      const result = dslTick(state, 105.0, NOW);

      expect(result.updatedState.highWaterPrice).toBe(105.0);
    });

    it('does not update HW when price drops (LONG)', () => {
      const state = makeState('baseState', { highWaterPrice: 105.0 });
      const result = dslTick(state, 103.0, NOW);

      expect(result.updatedState.highWaterPrice).toBe(105.0);
    });

    it('calculates uPnL correctly for LONG', () => {
      const state = makeState('baseState');
      // entry=100, size=10, leverage=10, price=105
      // upnl = (105 - 100) * 10 = 50
      // margin = 100 * 10 / 10 = 100
      // upnlPct = 50/100*100 = 50%
      const result = dslTick(state, 105.0, NOW);

      expect(result.metrics.upnl).toBe(50.0);
      expect(result.metrics.upnlPct).toBe(50.0);
    });

    it('auto-fixes absoluteFloor to correct value', () => {
      // entry=100, leverage=10, retraceThreshold=10
      // correctFloor = 100 * (1 - 0.1/10) = 100 * 0.99 = 99.0
      const state = makeState('baseState', {
        phase1: {
          retraceThreshold: 10,
          absoluteFloor: 99.5, // too high
          consecutiveBreachesRequired: 3,
        },
      });
      const result = dslTick(state, 100.0, NOW);

      // LONG: finalFloor = min(correctFloor, existingFloor) = min(99.0, 99.5) = 99.0
      expect(result.updatedState.phase1.absoluteFloor).toBe(99.0);
    });
  });

  // ─── Phase 1 SHORT ───
  describe('Phase 1 SHORT', () => {
    it('holds when price below entry (profitable SHORT)', () => {
      const state = makeState('shortBaseState');
      const result = dslTick(state, 49000.0, NOW);

      expect(result.action).toBe('hold');
      expect(result.metrics.upnlPct).toBeGreaterThan(0);
    });

    it('calculates uPnL correctly for SHORT', () => {
      const state = makeState('shortBaseState');
      // entry=50000, size=0.1, leverage=5, price=49000
      // upnl = (50000 - 49000) * 0.1 = 100
      // margin = 50000 * 0.1 / 5 = 1000
      // upnlPct = 100/1000*100 = 10%
      const result = dslTick(state, 49000.0, NOW);

      expect(result.metrics.upnl).toBe(100.0);
      expect(result.metrics.upnlPct).toBe(10.0);
    });

    it('updates HW downward for SHORT', () => {
      const state = makeState('shortBaseState');
      const result = dslTick(state, 48000.0, NOW);

      expect(result.updatedState.highWaterPrice).toBe(48000.0);
    });

    it('does not update HW when price rises (SHORT)', () => {
      const state = makeState('shortBaseState');
      const result = dslTick(state, 51000.0, NOW);

      expect(result.updatedState.highWaterPrice).toBe(50000.0);
    });

    it('breaches when price >= floor (SHORT)', () => {
      const state = makeState('shortBaseState');
      // absoluteFloor for SHORT = 50000 * (1 + 0.1/5) = 50000 * 1.02 = 51000
      const result = dslTick(state, 51000.0, NOW);

      expect(result.metrics.breached).toBe(true);
    });

    it('auto-fixes absoluteFloor for SHORT', () => {
      // entry=50000, leverage=5, retraceThreshold=10
      // correctFloor = 50000 * (1 + 0.1/5) = 50000 * 1.02 = 51000
      const state = makeState('shortBaseState', {
        phase1: {
          retraceThreshold: 10,
          absoluteFloor: 50500.0, // too low for SHORT
          consecutiveBreachesRequired: 3,
        },
      });
      const result = dslTick(state, 50000.0, NOW);

      // SHORT: finalFloor = max(correctFloor, existingFloor) = max(51000, 50500) = 51000
      expect(result.updatedState.phase1.absoluteFloor).toBe(51000.0);
    });
  });

  // ─── Tier Upgrades ───
  describe('Tier Upgrades', () => {
    it('upgrades to Tier 0 when uPnL% >= triggerPct', () => {
      const state = makeState('baseState', { highWaterPrice: 105.5 });
      // upnlPct = (105.5 - 100) * 10 / (100 * 10 / 10) * 100 = 55%
      // That exceeds tier 0 triggerPct of 5%
      const result = dslTick(state, 105.5, NOW);

      expect(result.tierChanged).toBe(true);
      expect(result.newTierIndex).toBeGreaterThanOrEqual(0);
    });

    it('calculates tier floor correctly for LONG', () => {
      // Tier 0: triggerPct=5, lockPct=50
      // Need upnlPct >= 5. With leverage=10, entry=100:
      // upnlPct = (price - 100) * 10 / 100 * 100
      // upnlPct >= 5 means price >= 100.5
      const state = makeState('baseState');
      const price = 100.6; // upnlPct = 6%
      const result = dslTick(state, price, NOW);

      expect(result.tierChanged).toBe(true);
      expect(result.newTierIndex).toBe(0);

      // Tier floor: entry + (hw - entry) * lockPct/100
      // HW should be updated to 100.6
      // tierFloor = 100 + (100.6 - 100) * 50/100 = 100 + 0.3 = 100.3
      expect(result.updatedState.tierFloorPrice).toBe(roundTo(100 + (100.6 - 100) * 50 / 100, 4));
    });

    it('calculates tier floor correctly for SHORT', () => {
      // SHORT entry=50000, leverage=5
      // Need upnlPct >= 5
      // upnlPct = (50000 - price) * 0.1 / (50000 * 0.1 / 5) * 100
      // = (50000 - price) / 10000 * 100
      // >= 5 means price <= 49500
      const state = makeState('shortBaseState');
      const price = 49400.0; // upnlPct = 6%
      const result = dslTick(state, price, NOW);

      expect(result.tierChanged).toBe(true);
      expect(result.newTierIndex).toBe(0);

      // Tier floor (SHORT): entry - (entry - hw) * lockPct/100
      // HW updated to 49400
      // tierFloor = 50000 - (50000 - 49400) * 50/100 = 50000 - 300 = 49700
      expect(result.updatedState.tierFloorPrice).toBe(
        roundTo(50000 - (50000 - 49400) * 50 / 100, 4),
      );
    });

    it('transitions Phase 1 → Phase 2 when tier >= phase2TriggerTier', () => {
      const state = makeState('baseState', { phase2TriggerTier: 0 });
      // Need to hit tier 0 (triggerPct=5)
      const price = 100.6; // upnlPct = 6%
      const result = dslTick(state, price, NOW);

      expect(result.phaseChanged).toBe(true);
      expect(result.newPhase).toBe(2);
      expect(result.updatedState.phase).toBe(2);
      expect(result.updatedState.currentBreachCount).toBe(0); // reset on phase transition
    });

    it('upgrades through multiple tiers in one tick', () => {
      // With very high profit, should upgrade through multiple tiers
      const state = makeState('baseState');
      // upnlPct = (price - 100) * 10 / 100 * 100
      // For tier 3 (triggerPct=20): price >= 102 -> upnlPct = 20%
      const price = 102.1; // upnlPct = 21%
      const result = dslTick(state, price, NOW);

      expect(result.tierChanged).toBe(true);
      expect(result.newTierIndex).toBe(3); // should reach tier 3
    });

    it('skips already-passed tiers', () => {
      const state = makeState('baseState', {
        currentTierIndex: 1,
        phase: 2,
        highWaterPrice: 101.5,
        tierFloorPrice: 100.5,
      });
      // upnlPct at price 101.5: (101.5-100)*10/100*100 = 15%
      // Should only check tier 2 (triggerPct=15) and tier 3 (triggerPct=20)
      const result = dslTick(state, 101.5, NOW);

      // tier 2 triggerPct=15, upnlPct=15 -> should trigger
      expect(result.newTierIndex).toBe(2);
    });
  });

  // ─── Phase 2 Behavior ───
  describe('Phase 2', () => {
    it('uses per-tier retrace in Phase 2', () => {
      const state = makeState('baseState', {
        phase: 2,
        currentTierIndex: 1,
        highWaterPrice: 102.0,
        tierFloorPrice: 101.0,
        tiers: [
          { triggerPct: 5, lockPct: 50, breaches: 3, retrace: 3 },
          { triggerPct: 10, lockPct: 65, breaches: 2, retrace: 4 },
          { triggerPct: 15, lockPct: 75, breaches: 2, retrace: 5 },
          { triggerPct: 20, lockPct: 85, breaches: 1, retrace: 6 },
        ],
      });

      // Use price 101.2 so upnlPct=12% stays within tier 1 (no upgrade to tier 2 at 15%)
      const result = dslTick(state, 101.2, NOW);
      // Current tier is 1, retrace = 4%
      // trailingFloor = hw * (1 - 4/100/10) = 102 * (1 - 0.004) = 102 * 0.996 = 101.592
      // effectiveFloor = max(tierFloor=101, trailingFloor=101.592) = 101.592
      expect(result.metrics.effectiveFloor).toBeCloseTo(101.592, 3);
    });

    it('uses phase2 default retrace when tier has no retrace', () => {
      const state = makeState('baseState', {
        phase: 2,
        currentTierIndex: 0,
        highWaterPrice: 101.0,
        tierFloorPrice: 100.3,
        phase2: { retraceFromHW: 5 },
      });

      const result = dslTick(state, 100.8, NOW);
      // tier 0 has no `retrace` field → falls back to phase2.retraceFromHW = 5
      // trailingFloor = 101 * (1 - 5/100/10) = 101 * (1 - 0.005) = 101 * 0.995 = 100.495
      expect(result.metrics.trailingFloor).toBeCloseTo(100.495, 3);
    });

    it('uses tier breachesRequired in Phase 2', () => {
      const state = makeState('baseState', {
        phase: 2,
        currentTierIndex: 0,
        currentBreachCount: 2,
        highWaterPrice: 101.0,
        tierFloorPrice: 100.3,
      });
      // Tier 0 has breaches: 3
      const result = dslTick(state, 98.0, NOW);

      expect(result.metrics.breachesNeeded).toBe(3);
      expect(result.metrics.breachCount).toBe(3);
      expect(result.action).toBe('close');
    });
  });

  // ─── Stagnation ───
  describe('Stagnation', () => {
    it('triggers stagnation when ROE high and HW stale', () => {
      const staleTime = new Date('2026-03-08T09:00:00Z'); // 1.5 hours ago
      const state = makeState('baseState', {
        highWaterPrice: 101.0,
        hwTimestamp: staleTime.toISOString().replace('.000Z', 'Z'),
        phase: 2,
        currentTierIndex: 0,
        tierFloorPrice: 100.3,
      });

      // Price near HW (within 1%), ROE >= 8%
      // upnlPct = (100.9 - 100) * 10 / 100 * 100 = 9%
      const result = dslTick(state, 100.9, NOW);

      expect(result.stagnationTriggered).toBe(true);
      expect(result.action).toBe('close');
      expect(result.closeReason).toContain('Stagnation TP');
    });

    it('does not trigger stagnation when ROE too low', () => {
      const staleTime = new Date('2026-03-08T09:00:00Z');
      const state = makeState('baseState', {
        highWaterPrice: 100.2,
        hwTimestamp: staleTime.toISOString().replace('.000Z', 'Z'),
      });

      // upnlPct = (100.1 - 100) * 10 / 100 * 100 = 1% < 8% minROE
      const result = dslTick(state, 100.1, NOW);

      expect(result.stagnationTriggered).toBe(false);
    });

    it('does not trigger stagnation when HW is fresh', () => {
      const freshTime = new Date('2026-03-08T10:25:00Z'); // 5 min ago
      const state = makeState('baseState', {
        highWaterPrice: 101.0,
        hwTimestamp: freshTime.toISOString().replace('.000Z', 'Z'),
        phase: 2,
        currentTierIndex: 0,
        tierFloorPrice: 100.3,
      });

      const result = dslTick(state, 100.9, NOW);

      expect(result.stagnationTriggered).toBe(false);
    });

    it('does not trigger stagnation when price moved far from HW', () => {
      const staleTime = new Date('2026-03-08T09:00:00Z');
      const state = makeState('baseState', {
        highWaterPrice: 101.0,
        hwTimestamp: staleTime.toISOString().replace('.000Z', 'Z'),
        phase: 2,
        currentTierIndex: 0,
        tierFloorPrice: 100.3,
      });

      // Price 2% away from HW (> 1% priceRangePct)
      // upnlPct still >= 8%
      const result = dslTick(state, 100.0, NOW);
      // priceMovePct = |100 - 101| / 101 * 100 = 0.99% — just within range
      // Actually this would trigger. Let's use a bigger gap.
      const result2 = dslTick(state, 99.0, NOW);
      // priceMovePct = |99 - 101| / 101 * 100 = 1.98% > 1%

      expect(result2.stagnationTriggered).toBe(false);
    });

    it('does not trigger stagnation when disabled', () => {
      const staleTime = new Date('2026-03-08T09:00:00Z');
      const state = makeState('baseState', {
        highWaterPrice: 101.0,
        hwTimestamp: staleTime.toISOString().replace('.000Z', 'Z'),
        stagnation: {
          enabled: false,
          minROE: 8,
          thresholdHours: 1,
          priceRangePct: 1,
        },
      });

      const result = dslTick(state, 100.9, NOW);

      expect(result.stagnationTriggered).toBe(false);
    });
  });

  // ─── Phase 1 Auto-Cut ───
  describe('Phase 1 Auto-Cut', () => {
    it('closes at 90 minute hard cap', () => {
      const created = new Date('2026-03-08T09:00:00Z'); // 90 min before NOW
      const state = makeState('baseState', {
        createdAt: created.toISOString().replace('.000Z', 'Z'),
      });

      const result = dslTick(state, 100.1, NOW);

      expect(result.phase1Autocut).toBe(true);
      expect(result.action).toBe('close');
      expect(result.closeReason).toContain('Phase 1 timeout');
      expect(result.closeReason).toContain('90min');
    });

    it('closes at custom max minutes', () => {
      const created = new Date('2026-03-08T09:50:00Z'); // 40 min ago
      const state = makeState('baseState', {
        createdAt: created.toISOString().replace('.000Z', 'Z'),
      });

      const dslConfig: DslConfig = { phase1MaxMinutes: 40 };
      const result = dslTick(state, 100.1, NOW, dslConfig);

      expect(result.phase1Autocut).toBe(true);
    });

    it('triggers weak peak early cut', () => {
      // 50 min elapsed, peak ROE 2.5%, now declining
      const created = new Date('2026-03-08T09:40:00Z'); // 50 min ago
      const state = makeState('baseState', {
        createdAt: created.toISOString().replace('.000Z', 'Z'),
        peakROE: 2.5,
      });

      // upnlPct = (100.1 - 100) * 10 / 100 * 100 = 1% < peakROE of 2.5% < threshold of 3.0
      const result = dslTick(state, 100.1, NOW);

      expect(result.phase1Autocut).toBe(true);
      expect(result.closeReason).toContain('Weak peak early cut');
    });

    it('does not trigger weak peak if ROE is still rising', () => {
      const created = new Date('2026-03-08T09:40:00Z');
      const state = makeState('baseState', {
        createdAt: created.toISOString().replace('.000Z', 'Z'),
        peakROE: 1.0,
      });

      // upnlPct = 2% > peakROE of 1% → still rising
      const result = dslTick(state, 100.2, NOW);

      expect(result.phase1Autocut).toBe(false);
    });

    it('does not trigger weak peak before weakPeakCutMinutes', () => {
      const created = new Date('2026-03-08T10:00:00Z'); // 30 min ago
      const state = makeState('baseState', {
        createdAt: created.toISOString().replace('.000Z', 'Z'),
        peakROE: 2.5,
      });

      const result = dslTick(state, 100.1, NOW);

      expect(result.phase1Autocut).toBe(false);
    });

    it('does not trigger autocut in Phase 2', () => {
      const created = new Date('2026-03-08T08:00:00Z'); // 150 min ago
      const state = makeState('baseState', {
        createdAt: created.toISOString().replace('.000Z', 'Z'),
        phase: 2,
        currentTierIndex: 1,
        highWaterPrice: 102.0,
        tierFloorPrice: 101.0,
      });

      const result = dslTick(state, 101.5, NOW);

      expect(result.phase1Autocut).toBe(false);
    });

    it('tracks peak ROE', () => {
      const state = makeState('baseState');
      // Use a price that stays in Phase 1 (upnlPct < tier 0 trigger of 5%)
      // upnlPct = (100.3 - 100) * 10 / 100 * 100 = 3%
      const result = dslTick(state, 100.3, NOW);

      expect(result.updatedState.peakROE).toBeCloseTo(3.0, 1);
    });
  });

  // ─── pendingClose ───
  describe('pendingClose', () => {
    it('closes when pendingClose is true', () => {
      const state = makeState('baseState', { pendingClose: true });
      const result = dslTick(state, 100.5, NOW);

      expect(result.action).toBe('close');
      expect(result.closeReason).toContain('pendingClose');
    });
  });

  // ─── Effective Floor Precision ───
  describe('Floor Price Precision', () => {
    it('absoluteFloor rounded to 6 decimal places', () => {
      const state = makeState('baseState', {
        entryPrice: 123.456789,
        leverage: 7,
        phase1: {
          retraceThreshold: 10,
          absoluteFloor: 0,
          consecutiveBreachesRequired: 3,
        },
      });

      const result = dslTick(state, 123.456789, NOW);
      const floor = result.updatedState.phase1.absoluteFloor;

      // Check precision: should have at most 6 decimal places
      const decimalPart = floor.toString().split('.')[1] || '';
      expect(decimalPart.length).toBeLessThanOrEqual(6);
    });

    it('effective floor rounded to 4 decimal places', () => {
      const state = makeState('baseState', { highWaterPrice: 100.123456 });
      const result = dslTick(state, 100.0, NOW);

      const floor = result.updatedState.floorPrice;
      const decimalPart = floor.toString().split('.')[1] || '';
      expect(decimalPart.length).toBeLessThanOrEqual(4);
    });

    it('tier floor rounded to 4 decimal places', () => {
      const state = makeState('baseState', { highWaterPrice: 100.777777 });
      const result = dslTick(state, 100.6, NOW); // triggers tier upgrade

      if (result.tierChanged) {
        const tf = result.updatedState.tierFloorPrice;
        const decimalPart = tf.toString().split('.')[1] || '';
        expect(decimalPart.length).toBeLessThanOrEqual(4);
      }
    });
  });

  // ─── Edge Cases ───
  describe('Edge Cases', () => {
    it('price exactly at floor should breach', () => {
      const state = makeState('baseState');
      // absoluteFloor = 99.0
      const result = dslTick(state, 99.0, NOW);

      expect(result.metrics.breached).toBe(true);
    });

    it('uPnL% exactly at tier trigger should upgrade', () => {
      const state = makeState('baseState');
      // Tier 0 trigger = 5%
      // Need upnlPct == 5.0 exactly
      // upnlPct = (price - 100) * 10 / 100 * 100 = 5 → price = 100.5
      const result = dslTick(state, 100.5, NOW);

      expect(result.tierChanged).toBe(true);
      expect(result.newTierIndex).toBe(0);
    });

    it('handles null currentTierIndex correctly', () => {
      const state = makeState('baseState', { currentTierIndex: null });
      const result = dslTick(state, 100.0, NOW);

      expect(result.action).toBe('hold');
    });

    it('handles zero tierFloorPrice', () => {
      const state = makeState('baseState', { tierFloorPrice: 0 });
      const result = dslTick(state, 100.0, NOW);

      expect(result.action).toBe('hold');
    });

    it('handles missing hwTimestamp', () => {
      const state = makeState('baseState');
      delete state.hwTimestamp;
      const result = dslTick(state, 100.0, NOW);

      expect(result.updatedState.hwTimestamp).toBeDefined();
    });

    it('handles missing createdAt (no autocut)', () => {
      const state = makeState('baseState');
      delete state.createdAt;
      const result = dslTick(state, 100.0, NOW);

      expect(result.phase1Autocut).toBe(false);
    });

    it('does not mutate input state', () => {
      const state = makeState('baseState');
      const original = JSON.parse(JSON.stringify(state));
      dslTick(state, 105.0, NOW);

      expect(state).toEqual(original);
    });
  });

  // ─── Aggressive vs Conservative Preset ───
  describe('Preset Behavior', () => {
    it('conservative preset has different tier thresholds', () => {
      const state = makeState('conservativeState');
      // Conservative tier 0: triggerPct=3
      // upnlPct at price 3018: (3018-3000)*1 / (3000*1/5) * 100 = 18/600*100 = 3%
      const result = dslTick(state, 3018.0, NOW);

      expect(result.tierChanged).toBe(true);
      expect(result.newTierIndex).toBe(0);
    });

    it('conservative preset requires more breaches', () => {
      const state = makeState('conservativeState');
      // Conservative Phase 1 needs 4 breaches
      expect(state.phase1.consecutiveBreachesRequired).toBe(4);

      let s = state;
      for (let i = 0; i < 3; i++) {
        const result = dslTick(s, 2930.0, new Date(NOW.getTime() + i * 180000));
        expect(result.action).toBe('hold');
        s = result.updatedState;
      }
      // 4th breach should close
      const finalResult = dslTick(s, 2930.0, new Date(NOW.getTime() + 3 * 180000));
      expect(finalResult.action).toBe('close');
    });
  });

  // ─── Trailing Floor Dynamics ───
  describe('Trailing Floor', () => {
    it('trailing floor rises with high-water (LONG Phase 1)', () => {
      // As price rises, HW rises, trailing floor rises
      let state = makeState('baseState');

      // Price 100 → trailing = 100 * (1 - 0.01) = 99.0
      let result = dslTick(state, 100.0, NOW);
      const floor1 = result.metrics.trailingFloor;

      // Price 101 → HW=101, trailing = 101 * (1 - 0.01) = 99.99
      result = dslTick(result.updatedState, 101.0, new Date(NOW.getTime() + 60000));
      const floor2 = result.metrics.trailingFloor;

      expect(floor2).toBeGreaterThan(floor1);
    });

    it('effective floor is max(absoluteFloor, trailingFloor) in Phase 1 LONG', () => {
      const state = makeState('baseState', { highWaterPrice: 103.0 });
      // trailing = 103 * (1 - 0.01) = 101.97
      // absoluteFloor = 99.0
      // effectiveFloor = max(99.0, 101.97) = 101.97
      const result = dslTick(state, 102.0, NOW);

      expect(result.metrics.effectiveFloor).toBeGreaterThan(99.0);
    });
  });

  // ─── Full Lifecycle ───
  describe('Full Lifecycle', () => {
    it('entry → tier upgrades → Phase 2 → breach close', () => {
      let state = makeState('baseState');
      let t = NOW.getTime();

      // 1. Entry price, no action
      let result = dslTick(state, 100.0, new Date(t));
      expect(result.action).toBe('hold');
      state = result.updatedState;

      // 2. Price rises, hits Tier 0 (5%), transitions to Phase 2
      t += 180000;
      result = dslTick(state, 100.6, new Date(t));
      expect(result.tierChanged).toBe(true);
      expect(result.newPhase).toBe(2);
      state = result.updatedState;

      // 3. Price continues up, hits Tier 1 (10%)
      t += 180000;
      result = dslTick(state, 101.1, new Date(t));
      expect(result.newTierIndex).toBeGreaterThanOrEqual(1);
      state = result.updatedState;

      // 4. Price drops below effective floor — breach
      t += 180000;
      result = dslTick(state, 99.5, new Date(t));
      expect(result.metrics.breached).toBe(true);
      state = result.updatedState;

      // 5. Still breached — 2nd breach (tier 1 needs 2)
      t += 180000;
      result = dslTick(state, 99.5, new Date(t));
      expect(result.action).toBe('close');
      expect(result.closeReason).toContain('DSL breach');
    });
  });

  // ─── Retrace Metrics ───
  describe('Retrace from HW', () => {
    it('calculates retrace from HW for LONG', () => {
      const state = makeState('baseState', { highWaterPrice: 110.0 });
      const result = dslTick(state, 105.0, NOW);

      // retraceFromHw = (1 - 105/110) * 100 = 4.545%
      expect(result.metrics.retraceFromHw).toBeCloseTo(4.55, 1);
    });

    it('calculates retrace from HW for SHORT', () => {
      const state = makeState('shortBaseState', { highWaterPrice: 45000.0 });
      const result = dslTick(state, 47000.0, NOW);

      // retraceFromHw = (47000/45000 - 1) * 100 = 4.44%
      expect(result.metrics.retraceFromHw).toBeCloseTo(4.44, 1);
    });
  });
});
