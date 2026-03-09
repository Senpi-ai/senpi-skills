/**
 * Position Opener — Atomic open: lock → gate → size → MCP → DSL.
 *
 * Ports: open-position.py (441 lines)
 *
 * Uses async-mutex per strategy key (replaces fcntl file locks).
 * Serializes: gate check → slot check → rotation close → position open → DSL create → entry counter.
 */

import { Mutex } from 'async-mutex';
import {
  CreateOrderParams,
  DslState,
  DslTier,
  PositionOpenRequest,
  PositionOpenResult,
  StrategyConfig,
  ROTATION_COOLDOWN_MINUTES,
  roundTo,
} from '../../core/types.js';
import { StateManager } from '../../core/state-manager.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { RiskGuard } from '../risk/risk-guard.js';
import { calculateLeverage } from '../risk/position-sizer.js';
import { logger } from '../../core/logger.js';

const APPROX_GRACE_MINUTES = 10;

/** Per-strategy mutexes to prevent concurrent opens */
const strategyMutexes = new Map<string, Mutex>();

function getMutex(strategyKey: string): Mutex {
  let mutex = strategyMutexes.get(strategyKey);
  if (!mutex) {
    mutex = new Mutex();
    strategyMutexes.set(strategyKey, mutex);
  }
  return mutex;
}

/** Create a minimal valid DSL state for a new position */
function createDslState(params: {
  asset: string;
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  size: number;
  leverage: number;
  strategyKey: string;
  wallet: string;
  dex: string;
  tiers?: DslTier[];
  approximate?: boolean;
}): DslState {
  const now = new Date().toISOString();
  const isLong = params.direction === 'LONG';

  const tiers = params.tiers ?? [
    { triggerPct: 5, lockPct: 50, breaches: 3 },
    { triggerPct: 10, lockPct: 65, breaches: 2 },
    { triggerPct: 15, lockPct: 75, breaches: 2 },
    { triggerPct: 20, lockPct: 85, breaches: 1 },
  ];

  const retraceRoe = 10;
  const retracePrice = (retraceRoe / 100) / params.leverage;
  const absFloor = isLong
    ? roundTo(params.entryPrice * (1 - retracePrice), 6)
    : roundTo(params.entryPrice * (1 + retracePrice), 6);

  const state: DslState = {
    version: 2,
    asset: params.asset,
    direction: params.direction,
    entryPrice: params.entryPrice,
    size: params.size,
    leverage: params.leverage,
    active: true,
    highWaterPrice: params.entryPrice,
    phase: 1,
    currentBreachCount: 0,
    currentTierIndex: null,
    tierFloorPrice: 0,
    floorPrice: absFloor,
    tiers,
    phase1: {
      retraceThreshold: retraceRoe,
      absoluteFloor: absFloor,
      consecutiveBreachesRequired: 3,
    },
    phase2TriggerTier: 0,
    createdAt: now,
    lastCheck: now,
    strategyKey: params.strategyKey,
    wallet: params.wallet,
    dex: params.dex,
    createdBy: 'position_opener',
  };

  if (params.approximate) {
    state.approximate = true;
  }

  return state;
}

/** Count active DSL positions for a strategy */
function countActiveDsls(stateManager: StateManager, skillName: string, strategyKey: string): number {
  const states = stateManager.listActiveDslStates(skillName, strategyKey);
  const now = Date.now();

  return states.filter((s) => {
    // Skip stale approximate DSLs
    if (s.state.approximate && s.state.createdAt) {
      const created = new Date(s.state.createdAt).getTime();
      const ageMin = (now - created) / 60_000;
      if (ageMin > APPROX_GRACE_MINUTES) return false;
    }
    return true;
  }).length;
}

/** Extract position data from clearinghouse */
function extractPosition(
  data: Record<string, unknown>,
  coin: string,
  dex?: string,
): { entryPx: number; size: number; leverage: number; direction: 'LONG' | 'SHORT' } | null {
  const sectionKey = dex === 'xyz' ? 'xyz' : 'main';
  const section = (data as Record<string, Record<string, unknown>>)[sectionKey];
  if (!section) return null;

  const assetPositions = section.assetPositions as Array<Record<string, unknown>> | undefined;
  if (!assetPositions) return null;

  for (const ap of assetPositions) {
    const pos = ap.position as Record<string, unknown> | undefined;
    if (!pos) continue;
    if (pos.coin !== coin) continue;

    const szi = parseFloat(String(pos.szi ?? 0));
    if (szi === 0) continue;

    const marginUsed = parseFloat(String(pos.marginUsed ?? 0));
    const posValue = parseFloat(String(pos.positionValue ?? 0));

    return {
      entryPx: parseFloat(String(pos.entryPx ?? 0)),
      size: Math.abs(szi),
      leverage: marginUsed > 0 ? roundTo(posValue / marginUsed, 1) : 0,
      direction: szi < 0 ? 'SHORT' : 'LONG',
    };
  }

  return null;
}

export async function openPosition(
  req: PositionOpenRequest,
  config: {
    strategy: StrategyConfig;
    skillName: string;
    stateManager: StateManager;
    mcp: SenpiMcpClient;
    riskGuard: RiskGuard;
    maxLeverageData?: Record<string, number>;
  },
): Promise<PositionOpenResult> {
  const { strategy, skillName, stateManager, mcp, riskGuard, maxLeverageData } = config;
  const mutex = getMutex(req.strategyKey);

  return mutex.runExclusive(async () => {
    const wallet = strategy.wallet;
    if (!wallet) {
      throw new Error(`No wallet configured for strategy ${req.strategyKey}`);
    }

    const margin = req.marginOverride ?? strategy.marginPerSlot ?? 0;
    if (margin <= 0) {
      throw new Error(`Invalid margin for strategy ${req.strategyKey}: ${margin}`);
    }

    // ── Gate check ──
    const gateResult = riskGuard.checkGate(req.strategyKey, strategy.guardRails);
    if (gateResult.gate !== 'OPEN') {
      throw new Error(`Strategy ${req.strategyKey} gated: ${gateResult.gate} — ${gateResult.reason}`);
    }

    // ── Resolve leverage ──
    const cleanAsset = req.asset.replace('xyz:', '');
    const maxLevData = maxLeverageData ?? {};
    const lookupKey = req.asset in maxLevData ? req.asset : cleanAsset;
    const maxLev = maxLevData[lookupKey];

    let leverage: number;
    if (req.leverage) {
      leverage = maxLev ? Math.min(req.leverage, maxLev) : req.leverage;
    } else if (maxLev) {
      leverage = calculateLeverage(maxLev, strategy.tradingRisk, req.conviction);
    } else {
      leverage = strategy.defaultLeverage ?? 10;
    }

    // ── Rotation close ──
    if (req.rotateOut) {
      const rotateClean = req.rotateOut.replace('xyz:', '');
      const rotateDsl = stateManager.getDslState(skillName, req.strategyKey, rotateClean);

      // Check rotation cooldown
      if (rotateDsl?.createdAt) {
        const created = new Date(rotateDsl.createdAt).getTime();
        const ageMin = (Date.now() - created) / 60_000;
        if (ageMin < ROTATION_COOLDOWN_MINUTES) {
          throw new Error(
            `Rotation cooldown: ${rotateClean} is ${Math.round(ageMin)}min old, minimum is ${ROTATION_COOLDOWN_MINUTES}min`,
          );
        }
      }

      // Determine on-chain coin name for close
      const isXyz = req.rotateOut.startsWith('xyz:') ||
        (maxLevData[`xyz:${req.rotateOut}`] !== undefined && maxLevData[req.rotateOut] === undefined);
      const closeCoin = isXyz && !req.rotateOut.startsWith('xyz:')
        ? `xyz:${req.rotateOut}`
        : req.rotateOut;

      const closeResult = await mcp.closePosition(wallet, closeCoin, { reason: 'rotation_for_stronger_signal' });
      if (!closeResult.success) {
        throw new Error(`Rotation close failed: ${closeResult.result}`);
      }

      // Deactivate DSL
      if (rotateDsl) {
        rotateDsl.active = false;
        rotateDsl.closeReason = 'rotation_for_stronger_signal';
        rotateDsl.closedAt = new Date().toISOString();
        stateManager.setDslState(skillName, req.strategyKey, rotateClean, rotateDsl);
      }
    }

    // ── Slot availability ──
    const maxSlots = strategy.slots;
    const dslCount = countActiveDsls(stateManager, skillName, req.strategyKey);

    // Cross-check with on-chain positions
    let onChainCount = 0;
    const chData = await mcp.getClearinghouseState(wallet);
    if (chData) {
      for (const sectionKey of ['main', 'xyz'] as const) {
        const section = chData[sectionKey];
        if (!section?.assetPositions) continue;
        for (const ap of section.assetPositions) {
          const szi = parseFloat(String(ap.position?.szi ?? 0));
          if (szi !== 0) onChainCount++;
        }
      }
    }

    const activeCount = Math.max(dslCount, onChainCount);
    if (activeCount >= maxSlots) {
      throw new Error(
        `No slots available for ${req.strategyKey}: ${activeCount}/${maxSlots} (dsl=${dslCount}, onChain=${onChainCount})`,
      );
    }

    // ── Check no existing position ──
    const existingDsl = stateManager.getDslState(skillName, req.strategyKey, cleanAsset);
    if (existingDsl?.active) {
      throw new Error(`Position already exists: ${cleanAsset} in ${req.strategyKey}`);
    }

    // ── Determine dex ──
    const isXyz = req.asset.startsWith('xyz:') ||
      (maxLevData[`xyz:${req.asset}`] !== undefined && maxLevData[req.asset] === undefined);
    const dex = isXyz ? 'xyz' : 'hl';
    const coin = isXyz && !req.asset.startsWith('xyz:') ? `xyz:${req.asset}` : req.asset;

    // ── Open position via MCP ──
    const order: CreateOrderParams = {
      coin,
      direction: req.direction,
      leverage: Math.round(leverage),
      marginAmount: margin,
      orderType: 'MARKET',
    };
    if (isXyz) {
      order.leverageType = 'ISOLATED';
    }

    await mcp.createPosition(wallet, [order], {
      retries: 2,
      timeoutMs: 15_000,
    });

    // ── Fetch actual fill data ──
    let entryPrice = 0;
    let size = roundTo(margin * leverage, 6);
    let actualLeverage = leverage;
    let approximate = false;

    try {
      const chDataPost = await mcp.getClearinghouseState(wallet);
      if (chDataPost) {
        const posData = extractPosition(chDataPost as unknown as Record<string, unknown>, coin, isXyz ? 'xyz' : undefined);
        if (posData && posData.entryPx > 0) {
          entryPrice = posData.entryPx;
          size = posData.size;
          actualLeverage = posData.leverage || leverage;
        } else {
          approximate = true;
        }
      } else {
        approximate = true;
      }
    } catch {
      approximate = true;
    }

    // ── Create DSL state ──
    const dslTiers = strategy.dsl?.tiers;
    const dslState = createDslState({
      asset: cleanAsset,
      direction: req.direction,
      entryPrice,
      size,
      leverage: actualLeverage,
      strategyKey: req.strategyKey,
      wallet,
      dex,
      tiers: dslTiers,
      approximate,
    });
    stateManager.setDslState(skillName, req.strategyKey, cleanAsset, dslState);

    // ── Increment entry counter ──
    try {
      riskGuard.recordEntry(req.strategyKey, strategy.guardRails);
    } catch {
      // Never fail the open for counter bookkeeping
    }

    // ── Build notification ──
    const parts = [`\u{1F7E2} OPENED ${cleanAsset} ${req.direction} [${req.strategyKey}]`];
    parts.push(entryPrice ? `Entry: $${entryPrice.toPrecision(4)}` : 'Entry: pending fill');
    parts.push(`Size: ${size.toPrecision(4)}`);
    parts.push(`Leverage: ${actualLeverage}x`);

    logger.info(`Position opened: ${cleanAsset} ${req.direction}`, {
      strategyKey: req.strategyKey,
      leverage: actualLeverage,
      approximate,
    });

    return {
      success: true,
      asset: cleanAsset,
      direction: req.direction,
      entryPrice,
      size,
      leverage: actualLeverage,
      approximate,
      strategyKey: req.strategyKey,
      notification: parts.join(' | '),
    };
  });
}
