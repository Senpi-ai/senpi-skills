import type { TierDef } from "./state-schema.js";

export function updateHighWater(
  state: Record<string, unknown>,
  price: number,
  isLong: boolean
): number {
  let hw = Number(state.highWaterPrice);
  if (isLong && price > hw) {
    hw = price;
    state.highWaterPrice = hw;
  } else if (!isLong && price < hw) {
    hw = price;
    state.highWaterPrice = hw;
  }
  return hw;
}

export function applyTierUpgrades(
  state: Record<string, unknown>,
  upnlPct: number,
  isLong: boolean,
  hw: number
): { tierIdx: number; tierFloor: number | null; tierChanged: boolean; previousTierIdx: number } {
  const tiers = (state.tiers ?? []) as TierDef[];
  let tierIdx = Number(state.currentTierIndex ?? -1);
  let tierFloor = state.tierFloorPrice as number | null;
  const phase = Number(state.phase ?? 1);
  const entry = Number(state.entryPrice);
  const previousTierIdx = tierIdx;
  let tierChanged = false;

  for (let i = 0; i < tiers.length; i++) {
    if (i <= tierIdx) continue;
    const tier = tiers[i];
    if (upnlPct >= tier.triggerPct) {
      tierIdx = i;
      tierChanged = true;
      const lockPct = tier.lockPct / 100;
      if (isLong) {
        tierFloor = Math.round((entry + (hw - entry) * lockPct) * 1e4) / 1e4;
      } else {
        tierFloor = Math.round((entry - (entry - hw) * lockPct) * 1e4) / 1e4;
      }
      const stored = state.tierFloorPrice as number | null | undefined;
      if (typeof stored === "number") {
        tierFloor = isLong ? Math.max(tierFloor, stored) : Math.min(tierFloor, stored);
      }
      state.currentTierIndex = tierIdx;
      state.tierFloorPrice = tierFloor;
      const phase2Trigger = Number(state.phase2TriggerTier ?? 0);
      if (phase === 1 && tierIdx >= phase2Trigger) {
        state.phase = 2;
        state.currentBreachCount = 0;
      }
    }
  }
  return { tierIdx, tierFloor, tierChanged, previousTierIdx };
}

export function computeEffectiveFloor(
  state: Record<string, unknown>,
  phase: number,
  tierIdx: number,
  tierFloor: number | null,
  hw: number,
  isLong: boolean
): { effectiveFloor: number; trailingFloor: number; breachesNeeded: number; retraceRoe: number } {
  const tiers = (state.tiers ?? []) as TierDef[];
  const leverage = Math.max(1, Number(state.leverage ?? 1));

  if (phase === 1) {
    const p1 = state.phase1 as Record<string, unknown>;
    const retraceRoe = Number(p1?.retraceThreshold ?? 0.03);
    const retracePrice = retraceRoe / leverage;
    const breachesNeeded = Number(p1?.consecutiveBreachesRequired ?? 1);
    const absFloor = Number(p1?.absoluteFloor ?? 0);
    let trailingFloor: number;
    let effectiveFloor: number;
    if (isLong) {
      trailingFloor = Math.round(hw * (1 - retracePrice) * 1e4) / 1e4;
      effectiveFloor = Math.max(absFloor, trailingFloor);
    } else {
      trailingFloor = Math.round(hw * (1 + retracePrice) * 1e4) / 1e4;
      effectiveFloor = Math.min(absFloor, trailingFloor);
    }
    return { effectiveFloor, trailingFloor, breachesNeeded, retraceRoe };
  }

  const p2 = state.phase2 as Record<string, unknown>;
  const defaultRetrace = Number(p2?.retraceThreshold ?? 0.015);
  const tier = tierIdx >= 0 ? tiers[tierIdx] : undefined;
  const retraceRoe = tier?.retrace ?? defaultRetrace;
  const retracePrice = retraceRoe / leverage;
  const breachesNeeded = Number(p2?.consecutiveBreachesRequired ?? 1);
  let trailingFloor: number;
  let effectiveFloor: number;
  if (isLong) {
    trailingFloor = Math.round(hw * (1 - retracePrice) * 1e4) / 1e4;
    effectiveFloor = Math.max(tierFloor ?? 0, trailingFloor);
  } else {
    trailingFloor = Math.round(hw * (1 + retracePrice) * 1e4) / 1e4;
    effectiveFloor = Math.min(tierFloor ?? Infinity, trailingFloor);
  }
  return { effectiveFloor, trailingFloor, breachesNeeded, retraceRoe };
}

export function updateBreachCount(
  state: Record<string, unknown>,
  breached: boolean,
  decayMode: string
): number {
  let count = Number(state.currentBreachCount ?? 0);
  if (breached) {
    count += 1;
  } else {
    count = decayMode === "soft" ? Math.max(0, count - 1) : 0;
  }
  state.currentBreachCount = count;
  return count;
}
