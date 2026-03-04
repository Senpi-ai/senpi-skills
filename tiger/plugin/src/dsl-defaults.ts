import type { DslState, DslTier, TigerPattern, CreateDslParams } from './types.js';

// ============================================================
// Standard Tiers (same for all patterns)
// ============================================================

export const DEFAULT_TIERS: DslTier[] = [
  { triggerPct: 0.05, lockPct: 0.20, retrace: 0.015 },
  { triggerPct: 0.10, lockPct: 0.50, retrace: 0.012 },
  { triggerPct: 0.20, lockPct: 0.70, retrace: 0.010 },
  { triggerPct: 0.35, lockPct: 0.80, retrace: 0.008 },
];

// ============================================================
// Pattern-Specific DSL Tuning
// ============================================================

interface PatternTuning {
  phase1Retrace: number;
  phase2Retrace: number;
  breachDecay: 'soft' | 'hard';
}

const PATTERN_TUNING: Record<TigerPattern, PatternTuning> = {
  COMPRESSION_BREAKOUT: { phase1Retrace: 0.015, phase2Retrace: 0.012, breachDecay: 'soft' },
  CORRELATION_LAG:      { phase1Retrace: 0.015, phase2Retrace: 0.012, breachDecay: 'soft' },
  MOMENTUM_BREAKOUT:    { phase1Retrace: 0.012, phase2Retrace: 0.010, breachDecay: 'soft' },
  MEAN_REVERSION:       { phase1Retrace: 0.015, phase2Retrace: 0.012, breachDecay: 'soft' },
  FUNDING_ARB:          { phase1Retrace: 0.020, phase2Retrace: 0.015, breachDecay: 'soft' },
};

// ============================================================
// Absolute Floor Calculation
// ============================================================

const DEFAULT_ABSOLUTE_FLOOR_PCT = 0.02; // 2%

function calculateAbsoluteFloor(
  direction: 'LONG' | 'SHORT',
  entryPrice: number,
  absoluteFloorOverride?: number,
): number {
  if (absoluteFloorOverride !== undefined) return absoluteFloorOverride;
  return direction === 'LONG'
    ? entryPrice * (1 - DEFAULT_ABSOLUTE_FLOOR_PCT)
    : entryPrice * (1 + DEFAULT_ABSOLUTE_FLOOR_PCT);
}

// ============================================================
// Build DSL State
// ============================================================

export function buildDslState(params: CreateDslParams): DslState {
  const tuning = PATTERN_TUNING[params.pattern];
  const now = new Date().toISOString();
  const absoluteFloor = calculateAbsoluteFloor(
    params.direction,
    params.entryPrice,
    params.absoluteFloor,
  );

  return {
    active: true,
    asset: params.asset,
    direction: params.direction,
    entryPrice: params.entryPrice,
    size: params.size,
    leverage: params.leverage,
    wallet: params.wallet,
    highWaterPrice: params.entryPrice,
    phase: 1,
    currentBreachCount: 0,
    currentTierIndex: -1,
    tierFloorPrice: null,
    pendingClose: false,
    phase1: {
      retraceThreshold: tuning.phase1Retrace,
      consecutiveBreachesRequired: 3,
      absoluteFloor,
    },
    phase2: {
      retraceThreshold: tuning.phase2Retrace,
      consecutiveBreachesRequired: 2,
    },
    phase2TriggerTier: 0,
    tiers: DEFAULT_TIERS.map((t) => ({ ...t })),
    breachDecay: tuning.breachDecay,
    createdAt: now,
    updatedAt: now,
    lastCheck: now,
    lastPrice: params.entryPrice,
    consecutiveFetchFailures: 0,
  };
}
