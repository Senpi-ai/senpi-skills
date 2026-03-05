import type { TierDef } from "./state-schema.js";

export function buildMonitorOutput(
  state: Record<string, unknown>,
  params: {
    price: number;
    direction: string;
    upnl: number;
    upnlPct: number;
    phase: number;
    hw: number;
    effectiveFloor: number;
    trailingFloor: number;
    tierFloor: number | null;
    tierIdx: number;
    tiers: TierDef[];
    tierChanged: boolean;
    previousTierIdx: number;
    breachCount: number;
    breachesNeeded: number;
    breached: boolean;
    shouldClose: boolean;
    closed: boolean;
    closeResult: string | null;
    now: string;
    slSynced: boolean;
    slInitialSync: boolean;
  }
): Record<string, unknown> {
  const {
    price, direction, upnl, upnlPct, phase, hw, effectiveFloor, trailingFloor,
    tierFloor, tierIdx, tiers, tierChanged, previousTierIdx,
    breachCount, breachesNeeded, breached, shouldClose, closed, closeResult,
    now, slSynced, slInitialSync,
  } = params;
  const isLong = direction === "LONG";
  const entry = Number(state.entryPrice);
  const size = Number(state.size);

  const retraceFromHw = hw > 0
    ? (isLong ? (1 - price / hw) * 100 : (price / hw - 1) * 100)
    : 0;
  const tierName = tierIdx >= 0 && tiers[tierIdx]
    ? `Tier ${tierIdx + 1} (${tiers[tierIdx].triggerPct}%→lock ${tiers[tierIdx].lockPct}%)`
    : "None";
  let previousTierName: string | null = null;
  if (tierChanged) {
    previousTierName = previousTierIdx >= 0 && tiers[previousTierIdx]
      ? `Tier ${previousTierIdx + 1} (${tiers[previousTierIdx].triggerPct}%→lock ${tiers[previousTierIdx].lockPct}%)`
      : "None (Phase 1)";
  }
  const lockedProfit = tierFloor != null
    ? Math.round(((isLong ? tierFloor - entry : entry - tierFloor) * size) * 100) / 100
    : 0;
  let elapsedMinutes = 0;
  const createdAt = state.createdAt as string | undefined;
  if (createdAt) {
    try {
      const created = new Date(createdAt).getTime();
      elapsedMinutes = Math.round((Date.now() - created) / 60000);
    } catch {
      // ignore
    }
  }
  let distanceToNextTier: number | null = null;
  if (tierIdx + 1 < tiers.length) {
    distanceToNextTier = Math.round((tiers[tierIdx + 1].triggerPct - upnlPct) * 100) / 100;
  }
  const status = closed
    ? "closed"
    : state.pendingClose
      ? "pending_close"
      : breached
        ? "breached"
        : tierChanged
          ? "tier_changed"
          : "ok";
  const out: Record<string, unknown> = {
    status,
    asset: state.asset,
    direction,
    price,
    upnl: Math.round(upnl * 100) / 100,
    upnl_pct: Math.round(upnlPct * 100) / 100,
    phase,
    hw,
    floor: effectiveFloor,
    trailing_floor: trailingFloor,
    tier_floor: tierFloor,
    tier_name: tierName,
    locked_profit: lockedProfit,
    retrace_pct: Math.round(retraceFromHw * 100) / 100,
    breach_count: breachCount,
    breaches_needed: breachesNeeded,
    breached,
    should_close: shouldClose,
    closed,
    close_result: closeResult,
    time: now,
    tier_changed: tierChanged,
    previous_tier: previousTierName,
    elapsed_minutes: elapsedMinutes,
    distance_to_next_tier_pct: distanceToNextTier,
    pending_close: state.pendingClose ?? false,
    consecutive_failures: state.consecutiveFetchFailures ?? 0,
    sl_synced: slSynced,
    sl_initial_sync: slInitialSync,
    sl_order_id: state.slOrderId,
    preset: state.preset ?? "default",
  };
  return out;
}
