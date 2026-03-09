/**
 * DSL Engine v4.1 — Pure Function Port
 *
 * Ports `process_position()` from `dsl-combined.py` as a pure function.
 * No I/O, no API calls, no state writes. Takes state + price + timestamp,
 * returns action + updated state.
 *
 * This is the single most critical piece of the plugin. DSL correctness
 * is real-money-critical.
 */

import { DslState, DslConfig, DslTickResult, DslTier, roundTo } from '../../core/types.js';

/**
 * Parse an ISO timestamp string to a Date object.
 * Handles the Z suffix and +00:00 formats.
 */
function parseTimestamp(ts: string): Date {
  return new Date(ts.replace('Z', '+00:00'));
}

/**
 * Get the number of breaches required from a tier definition.
 * Handles the multiple aliases used in the Python code.
 */
function getTierBreaches(tier: DslTier, fallback: number): number {
  return tier.breachesRequired ?? tier.breaches ?? tier.retraceClose ?? fallback;
}

/**
 * Get the per-tier retrace percentage.
 * Falls back to the phase2 default if the tier doesn't define one.
 */
function getTierRetrace(tier: DslTier, phase2Default: number): number {
  return tier.retrace ?? phase2Default;
}

/**
 * Run one DSL tick on a single position. Pure function — no side effects.
 *
 * @param state - Current DSL position state (will be deep-cloned internally)
 * @param currentPrice - Current market price for this asset
 * @param now - Current timestamp (UTC)
 * @param dslConfig - Strategy-level DSL config (phase1 timeout settings)
 * @returns DslTickResult with action, close reason, and updated state
 */
export function dslTick(
  state: DslState,
  currentPrice: number,
  now: Date,
  dslConfig: DslConfig = {},
): DslTickResult {
  // Deep clone state to avoid mutation of input
  const s: DslState = JSON.parse(JSON.stringify(state));
  const nowStr = now.toISOString().replace('.000Z', 'Z').replace(/\.\d{3}Z$/, 'Z');

  const direction = (s.direction || 'LONG').toUpperCase() as 'LONG' | 'SHORT';
  const isLong = direction === 'LONG';
  const breachDecayMode = s.breachDecay || 'hard';

  const entry = s.entryPrice;
  const size = s.size;
  const leverage = s.leverage;
  let hw = s.highWaterPrice;
  let phase = s.phase;
  let breachCount = s.currentBreachCount;
  let tierIdx = s.currentTierIndex;
  let tierFloor = s.tierFloorPrice;
  const tiers = s.tiers;
  const forceClose = s.pendingClose || false;

  // ── Stagnation config ──
  const stagCfg = s.stagnation;
  const stagEnabled = stagCfg?.enabled !== undefined ? stagCfg.enabled : true;
  const stagMinRoe = stagCfg?.minROE ?? 8.0;
  const stagStaleHours = stagCfg?.thresholdHours ?? 1.0;
  const stagRangePct = stagCfg?.priceRangePct ?? 1.0;

  // ── 1. Auto-fix absoluteFloor ──
  const retraceRoe = Math.abs(s.phase1.retraceThreshold);
  const retraceDecimal = retraceRoe / 100;
  const retracePrice = retraceDecimal / leverage;

  let correctFloor: number;
  if (isLong) {
    correctFloor = roundTo(entry * (1 - retracePrice), 6);
  } else {
    correctFloor = roundTo(entry * (1 + retracePrice), 6);
  }

  const existingFloor = s.phase1.absoluteFloor ?? correctFloor;

  let finalFloor: number;
  if (isLong) {
    finalFloor = existingFloor > 0 ? Math.min(correctFloor, existingFloor) : correctFloor;
  } else {
    finalFloor = existingFloor > 0 ? Math.max(correctFloor, existingFloor) : correctFloor;
  }

  s.phase1.absoluteFloor = finalFloor;
  s.floorPrice = finalFloor;

  // ── 2. uPnL calculation ──
  let upnl: number;
  if (isLong) {
    upnl = (currentPrice - entry) * size;
  } else {
    upnl = (entry - currentPrice) * size;
  }
  const margin = (entry * size) / leverage;
  const upnlPct = (upnl / margin) * 100;

  // ── 3. High-water mark update ──
  let hwUpdated = false;
  if (isLong && currentPrice > hw) {
    hw = currentPrice;
    s.highWaterPrice = hw;
    hwUpdated = true;
  } else if (!isLong && currentPrice < hw) {
    hw = currentPrice;
    s.highWaterPrice = hw;
    hwUpdated = true;
  }

  if (hwUpdated || !s.hwTimestamp) {
    s.hwTimestamp = nowStr;
  }

  // ── 4. Tier upgrades ──
  const previousTierIdx = tierIdx;
  let tierChanged = false;

  for (let i = 0; i < tiers.length; i++) {
    if (tierIdx !== null && tierIdx !== undefined && i <= tierIdx) {
      continue;
    }
    if (upnlPct >= tiers[i].triggerPct) {
      tierIdx = i;
      tierChanged = true;

      if (isLong) {
        tierFloor = roundTo(entry + (hw - entry) * tiers[i].lockPct / 100, 4);
      } else {
        tierFloor = roundTo(entry - (entry - hw) * tiers[i].lockPct / 100, 4);
      }

      s.currentTierIndex = tierIdx;
      s.tierFloorPrice = tierFloor;

      // Phase transition
      if (phase === 1) {
        const phase2Trigger = s.phase2TriggerTier ?? 0;
        if (tierIdx >= phase2Trigger) {
          phase = 2;
          s.phase = 2;
          breachCount = 0;
          s.currentBreachCount = 0;
        }
      }
    }
  }

  // ── 5. Effective floor calculation ──
  let effectiveFloor: number;
  let trailingFloor: number;
  let breachesNeeded: number;

  if (phase === 1) {
    const p1Retrace = Math.abs(s.phase1.retraceThreshold);
    const p1RetracePrice = (p1Retrace / 100) / leverage;
    breachesNeeded = s.phase1.consecutiveBreachesRequired;
    const absFloor = s.phase1.absoluteFloor;

    if (isLong) {
      trailingFloor = roundTo(hw * (1 - p1RetracePrice), 4);
      effectiveFloor = Math.max(absFloor, trailingFloor);
    } else {
      trailingFloor = roundTo(hw * (1 + p1RetracePrice), 4);
      effectiveFloor = Math.min(absFloor, trailingFloor);
    }
  } else {
    // Phase 2
    const p2RetracePct = s.phase2?.retraceFromHW ?? 5;

    let tRetracePct: number;
    if (tierIdx !== null && tierIdx !== undefined && tierIdx >= 0) {
      tRetracePct = getTierRetrace(tiers[tierIdx], p2RetracePct);
    } else {
      tRetracePct = p2RetracePct;
    }

    const tRetracePrice = tRetracePct / 100 / leverage;

    if (tierIdx !== null && tierIdx !== undefined && tierIdx >= 0) {
      breachesNeeded = getTierBreaches(tiers[tierIdx], s.phase2?.consecutiveBreachesRequired ?? 2);
    } else {
      breachesNeeded = s.phase2?.consecutiveBreachesRequired ?? 2;
    }

    if (isLong) {
      trailingFloor = roundTo(hw * (1 - tRetracePrice), 4);
      effectiveFloor = Math.max(tierFloor || 0, trailingFloor);
    } else {
      trailingFloor = roundTo(hw * (1 + tRetracePrice), 4);
      effectiveFloor = Math.min(
        tierFloor !== null && tierFloor !== undefined ? tierFloor : Infinity,
        trailingFloor,
      );
    }
  }

  s.floorPrice = roundTo(effectiveFloor, 4);

  // ── 6. Stagnation check ──
  let stagnationTriggered = false;
  let stagHoursStale = 0;

  if (stagEnabled && upnlPct >= stagMinRoe && s.hwTimestamp) {
    try {
      const hwTime = parseTimestamp(s.hwTimestamp);
      stagHoursStale = (now.getTime() - hwTime.getTime()) / (1000 * 3600);
      if (stagHoursStale >= stagStaleHours) {
        const hwPrice = s.highWaterPrice;
        if (hwPrice > 0) {
          const priceMovePct = (Math.abs(currentPrice - hwPrice) / hwPrice) * 100;
          if (priceMovePct <= stagRangePct) {
            stagnationTriggered = true;
          }
        }
      }
    } catch {
      // Invalid timestamp — skip stagnation check
    }
  }

  // ── 7. Phase 1 auto-cut ──
  const phase1MaxMinutes = dslConfig.phase1MaxMinutes ?? 90;
  const weakPeakCutMinutes = dslConfig.weakPeakCutMinutes ?? 45;
  const weakPeakThreshold = dslConfig.weakPeakThreshold ?? 3.0;

  let phase1Autocut = false;
  let phase1AutocutReason: string | undefined;
  let elapsedMinutes = 0;

  if (s.createdAt) {
    try {
      const created = parseTimestamp(s.createdAt);
      elapsedMinutes = (now.getTime() - created.getTime()) / (1000 * 60);
    } catch {
      // Invalid timestamp
    }
  }

  let peakROE = s.peakROE ?? upnlPct;

  if (phase === 1 && elapsedMinutes > 0) {
    // Peak ROE tracking
    if (upnlPct > peakROE) {
      peakROE = upnlPct;
    }
    s.peakROE = peakROE;

    // Hard cap
    if (elapsedMinutes >= phase1MaxMinutes) {
      phase1Autocut = true;
      const tier1Pct = tiers.length > 0 ? tiers[0].triggerPct : 5;
      phase1AutocutReason = `Phase 1 timeout: ${Math.round(elapsedMinutes)}min, ROE never hit Tier 1 (${tier1Pct}%)`;
    }
    // Weak peak early cut
    else if (
      elapsedMinutes >= weakPeakCutMinutes &&
      peakROE < weakPeakThreshold &&
      upnlPct < peakROE
    ) {
      phase1Autocut = true;
      phase1AutocutReason = `Weak peak early cut: ${Math.round(elapsedMinutes)}min, peak ROE ${roundTo(peakROE, 1)}%, now declining`;
    }
  }

  // ── 8. Breach check ──
  let breached: boolean;
  if (isLong) {
    breached = currentPrice <= effectiveFloor;
  } else {
    breached = currentPrice >= effectiveFloor;
  }

  if (breached) {
    breachCount += 1;
  } else {
    if (breachDecayMode === 'soft') {
      breachCount = Math.max(0, breachCount - 1);
    } else {
      breachCount = 0;
    }
  }
  s.currentBreachCount = breachCount;

  // ── 9. Close decision ──
  const shouldClose =
    breachCount >= breachesNeeded ||
    forceClose ||
    stagnationTriggered ||
    phase1Autocut;

  // ── Build close reason ──
  let closeReason: string | undefined;
  if (shouldClose) {
    if (stagnationTriggered) {
      closeReason = `Stagnation TP: ROE ${roundTo(upnlPct, 1)}%, stale ${roundTo(stagHoursStale, 1)}h`;
    } else if (phase1Autocut) {
      closeReason = phase1AutocutReason;
    } else if (forceClose) {
      closeReason = 'Pending close (pendingClose flag)';
    } else {
      closeReason = `DSL breach: Phase ${phase}, ${breachCount}/${breachesNeeded}, price ${currentPrice}, floor ${effectiveFloor}`;
    }
  }

  // ── Update state timestamps ──
  s.lastCheck = nowStr;
  s.lastPrice = currentPrice;

  // ── Compute retrace from HW ──
  let retraceFromHw: number;
  if (isLong) {
    retraceFromHw = hw > 0 ? (1 - currentPrice / hw) * 100 : 0;
  } else {
    retraceFromHw = hw > 0 ? (currentPrice / hw - 1) * 100 : 0;
  }

  // ── Return result ──
  return {
    action: shouldClose ? 'close' : 'hold',
    closeReason,
    tierChanged,
    previousTierIndex: previousTierIdx,
    newTierIndex: tierIdx,
    phaseChanged: phase !== state.phase,
    newPhase: phase,
    stagnationTriggered,
    phase1Autocut,
    phase1AutocutReason,
    updatedState: s,
    metrics: {
      upnl: roundTo(upnl, 2),
      upnlPct: roundTo(upnlPct, 2),
      retraceFromHw: roundTo(retraceFromHw, 2),
      effectiveFloor: roundTo(effectiveFloor, 4),
      trailingFloor: roundTo(trailingFloor, 4),
      tierFloor: tierFloor || 0,
      breachCount,
      breachesNeeded,
      breached,
      elapsedMinutes: roundTo(elapsedMinutes, 1),
      peakROE: roundTo(peakROE, 2),
      highWater: hw,
      phase,
    },
  };
}
