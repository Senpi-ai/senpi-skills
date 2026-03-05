import { strategyGetOpenOrders } from "./mcp.js";
import { dexAndLookupSymbol } from "./paths.js";
import { readStateFile, writeStateFile, archiveStateFile, saveOrDeleteState } from "./state-io.js";
import { normalizeStatePhaseConfig } from "./state-schema.js";
import { fetchPriceMcp } from "./price.js";
import { updateHighWater, applyTierUpgrades, computeEffectiveFloor, updateBreachCount } from "./tiers.js";
import { syncSlToHyperliquid } from "./sync-sl.js";
import { tryClosePosition } from "./close.js";
import { buildMonitorOutput } from "./output.js";
import type { DslMonitorOutput } from "../types.js";

export async function processOnePosition(
  stateFilePath: string,
  stateDir: string,
  strategyId: string,
  now: string
): Promise<DslMonitorOutput | null> {
  const state = await readStateFile<Record<string, unknown>>(stateFilePath);
  if (!state) {
    return {
      status: "error",
      error: "state_file_read_failed",
      path: stateFilePath,
      strategy_id: strategyId,
      time: now,
    };
  }

  if (normalizeStatePhaseConfig(state)) {
    await writeStateFile(stateFilePath, state).catch(() => {});
  }

  if (!state.active && !state.pendingClose) {
    return {
      status: "inactive",
      asset: state.asset,
      strategy_id: strategyId,
      time: now,
    };
  }

  const direction = String(state.direction ?? "LONG").toUpperCase();
  const isLong = direction === "LONG";
  const asset = state.asset as string;
  const { dex, lookupSymbol } = dexAndLookupSymbol(asset);

  const { price, error: fetchError } = await fetchPriceMcp(dex, lookupSymbol);
  if (fetchError != null) {
    const fails = Number(state.consecutiveFetchFailures ?? 0) + 1;
    state.consecutiveFetchFailures = fails;
    state.lastCheck = now;
    const maxFailures = Number(state.maxFetchFailures ?? 10);
    if (fails >= maxFailures) {
      state.active = false;
      state.closeReason = `Auto-deactivated: ${fails} consecutive fetch failures`;
    }
    await writeStateFile(stateFilePath, state).catch(() => {});
    return {
      status: "error",
      error: `price_fetch_failed: ${fetchError}`,
      asset: state.asset,
      strategy_id: strategyId,
      consecutive_failures: fails,
      deactivated: fails >= maxFailures,
      pending_close: state.pendingClose ?? false,
      time: now,
    };
  }

  state.consecutiveFetchFailures = 0;
  state.lastPrice = price;
  const entry = Number(state.entryPrice);
  const size = Number(state.size);
  const leverage = Number(state.leverage);
  const hw = updateHighWater(state, price!, isLong);

  const margin = state.margin != null && Number(state.margin) > 0
    ? Number(state.margin)
    : (entry * size) / leverage;
  const upnl = isLong ? (price! - entry) * size : (entry - price!) * size;
  const upnlPct = (upnl / margin) * 100;

  const { tierIdx, tierFloor, tierChanged, previousTierIdx } = applyTierUpgrades(state, upnlPct, isLong, hw);
  const phase = Number(state.phase ?? 1);
  let breachCount = Number(state.currentBreachCount ?? 0);

  const { effectiveFloor, trailingFloor, breachesNeeded } = computeEffectiveFloor(
    state, phase, tierIdx, tierFloor, hw, isLong
  );
  state.floorPrice = Math.round(effectiveFloor * 1e4) / 1e4;

  const lastSynced = state.lastSyncedFloorPrice;
  if (state.slOrderId != null && lastSynced != null) {
    const { orders, error: ordersError } = await strategyGetOpenOrders(String(state.wallet ?? ""), dex);
    if (ordersError) {
      state.lastSlSyncError = `orders_fetch_failed: ${ordersError}`;
    } else {
      const oidsForCoin: number[] = [];
      for (const o of orders) {
        if (o.coin !== asset) continue;
        const oid = o.oid;
        if (oid != null) {
          const n = typeof oid === "number" ? oid : parseInt(String(oid), 10);
          if (!Number.isNaN(n)) oidsForCoin.push(n);
        }
      }
      if (!oidsForCoin.includes(Number(state.slOrderId))) {
        state.lastSyncedFloorPrice = undefined;
      }
    }
  }

  const hadSlOrderBefore = state.slOrderId != null;
  const effectiveFloorRounded = Math.round(effectiveFloor * 1e4) / 1e4;
  const needSync =
    state.lastSyncedFloorPrice == null ||
    Math.abs((Number(state.lastSyncedFloorPrice) || 0) - effectiveFloorRounded) > 1e-9;
  let slSyncedThisTick = false;
  if (needSync) {
    const syncResult = await syncSlToHyperliquid(state, effectiveFloor, now, dex);
    slSyncedThisTick = syncResult.slSyncedThisTick;
    if (!syncResult.success && syncResult.error) {
      state.lastSlSyncError = syncResult.error;
    }
  }
  const slInitialSync = slSyncedThisTick && !hadSlOrderBefore;

  const breached = isLong ? price! <= effectiveFloor : price! >= effectiveFloor;
  breachCount = updateBreachCount(state, breached, String(state.breachDecay ?? "hard"));
  const forceClose = Boolean(state.pendingClose);
  const shouldClose = breachCount >= breachesNeeded || forceClose;

  let closed = false;
  let closeResult: string | null = null;
  if (shouldClose) {
    const closeRetries = Number(state.closeRetries ?? 2);
    const closeRetryDelaySec = Number(state.closeRetryDelaySec ?? 3);
    const closeOut = await tryClosePosition(
      state, price!, phase, breachCount, breachesNeeded, effectiveFloor, now,
      closeRetries, closeRetryDelaySec
    );
    closed = closeOut.closed;
    closeResult = closeOut.closeResult;
    state.closeResult = closeResult;
  }

  const closeResultFinal = await saveOrDeleteState(
    state,
    stateFilePath,
    closed,
    stateDir,
    strategyId,
    writeStateFile,
    archiveStateFile
  );
  if (closeResultFinal != null) closeResult = closeResultFinal;

  const tiers = (state.tiers ?? []) as Array<{ triggerPct: number; lockPct: number }>;
  const out = buildMonitorOutput(state, {
    price: price!,
    direction,
    upnl,
    upnlPct,
    phase,
    hw,
    effectiveFloor,
    trailingFloor,
    tierFloor,
    tierIdx,
    tiers,
    tierChanged,
    previousTierIdx,
    breachCount,
    breachesNeeded,
    breached,
    shouldClose,
    closed,
    closeResult,
    now,
    slSynced: slSyncedThisTick,
    slInitialSync,
  });
  out.strategy_id = strategyId;
  return out as DslMonitorOutput;
}
