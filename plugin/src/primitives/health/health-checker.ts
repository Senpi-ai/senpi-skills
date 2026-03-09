/**
 * Health Checker — State reconciliation, orphan detection.
 *
 * Ports: job-health-check.py (653 lines)
 *
 * In-process validation is simpler — the plugin KNOWS which positions
 * it opened (no orphan guesswork). Handles:
 * - DSL ↔ clearinghouse reconciliation
 * - Approximate DSL fill data resolution
 * - Orphan DSL detection and auto-deactivation
 * - Schema validation and auto-repair
 */

import { BackgroundService, DslState, DslTier } from '../../core/types.js';
import { StateManager } from '../../core/state-manager.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { NotificationService } from '../../core/notifications.js';
import { logger } from '../../core/logger.js';
import { roundTo } from '../../core/types.js';

interface HealthIssue {
  level: 'CRITICAL' | 'WARNING' | 'INFO';
  type: string;
  strategyKey: string;
  asset?: string;
  action: string;
  message: string;
}

/** Required keys for a valid DSL state */
const DSL_REQUIRED_KEYS = [
  'asset', 'direction', 'entryPrice', 'size', 'leverage',
  'highWaterPrice', 'phase', 'currentBreachCount',
  'currentTierIndex', 'tierFloorPrice', 'tiers', 'phase1',
];

const PHASE1_REQUIRED_KEYS = ['retraceThreshold', 'consecutiveBreachesRequired'];

function validateDslState(state: DslState): { valid: boolean; error?: string } {
  if (typeof state !== 'object' || state === null) {
    return { valid: false, error: 'state is not an object' };
  }

  const missing = DSL_REQUIRED_KEYS.filter((k) => !(k in state));
  if (missing.length > 0) {
    return { valid: false, error: `missing keys: ${missing.join(', ')}` };
  }

  if (typeof state.phase1 !== 'object' || state.phase1 === null) {
    return { valid: false, error: 'phase1 is not an object' };
  }

  const missingP1 = PHASE1_REQUIRED_KEYS.filter((k) => !(k in state.phase1));
  if (missingP1.length > 0) {
    return { valid: false, error: `phase1 missing keys: ${missingP1.join(', ')}` };
  }

  if (!Array.isArray(state.tiers)) {
    return { valid: false, error: 'tiers is not an array' };
  }

  return { valid: true };
}

function pctDiff(a: number, b: number): number {
  if (b === 0) return a !== 0 ? Infinity : 0;
  return (Math.abs(a - b) / Math.abs(b)) * 100;
}

export class HealthCheckerService implements BackgroundService {
  name = 'health-checker';
  intervalMs = 600_000; // 10 minutes

  private stateManager: StateManager;
  private mcp: SenpiMcpClient;
  private notifications: NotificationService;
  private skillName: string;
  private strategies: Map<string, { wallet: string; tiers?: DslTier[] }>;

  constructor(config: {
    stateManager: StateManager;
    mcp: SenpiMcpClient;
    notifications: NotificationService;
    skillName: string;
    strategies: Map<string, { wallet: string; tiers?: DslTier[] }>;
  }) {
    this.stateManager = config.stateManager;
    this.mcp = config.mcp;
    this.notifications = config.notifications;
    this.skillName = config.skillName;
    this.strategies = config.strategies;
  }

  async tick(): Promise<void> {
    const allIssues: HealthIssue[] = [];

    for (const [strategyKey, stratConfig] of this.strategies) {
      if (!stratConfig.wallet) continue;

      const issues = await this.checkStrategy(strategyKey, stratConfig);
      allIssues.push(...issues);
    }

    // Send notifications for critical auto-fixes
    const notifyActions = new Set(['auto_created', 'auto_replaced']);
    for (const issue of allIssues) {
      if (notifyActions.has(issue.action)) {
        await this.notifications.send(
          `\u{1F527} ${issue.action.toUpperCase()} [${issue.strategyKey}] ${issue.asset ?? ''}: ${issue.message}`,
        );
      }
    }

    if (allIssues.length > 0) {
      logger.info('Health check complete', {
        issues: allIssues.length,
        critical: allIssues.filter((i) => i.level === 'CRITICAL').length,
      });
    }

    await this.stateManager.flushAll();
  }

  private async checkStrategy(
    strategyKey: string,
    stratConfig: { wallet: string; tiers?: DslTier[] },
  ): Promise<HealthIssue[]> {
    const issues: HealthIssue[] = [];
    const now = new Date();

    // Fetch clearinghouse (one call for crypto + xyz)
    const chData = await this.mcp.getClearinghouseState(stratConfig.wallet);
    const hadFetchError = !chData;

    if (hadFetchError) {
      issues.push({
        level: 'WARNING',
        type: 'FETCH_ERROR',
        strategyKey,
        action: 'alert_only',
        message: `Clearinghouse fetch failed for ${strategyKey}`,
      });
    }

    // Extract all on-chain positions
    const onChainPositions = new Map<string, {
      direction: string;
      size: number;
      entryPx: number;
      leverage: number;
    }>();

    if (chData) {
      for (const sectionKey of ['main', 'xyz'] as const) {
        const section = chData[sectionKey];
        if (!section?.assetPositions) continue;
        for (const ap of section.assetPositions) {
          const pos = ap.position;
          if (!pos) continue;
          const szi = parseFloat(pos.szi || '0');
          if (szi === 0) continue;
          const marginUsed = parseFloat(pos.marginUsed || '0');
          const posValue = parseFloat(pos.positionValue || '0');

          onChainPositions.set(pos.coin, {
            direction: szi < 0 ? 'SHORT' : 'LONG',
            size: Math.abs(szi),
            entryPx: parseFloat(pos.entryPx || '0'),
            leverage: marginUsed > 0 ? roundTo(posValue / marginUsed, 1) : 0,
          });
        }
      }
    }

    // Get all DSL states for this strategy
    const dslStates = this.stateManager.listActiveDslStates(this.skillName, strategyKey);

    // Check each on-chain position has a valid DSL
    for (const [coin, pos] of onChainPositions) {
      const cleanCoin = coin.replace('xyz:', '');
      const dsl = this.stateManager.getDslState(this.skillName, strategyKey, cleanCoin);

      if (!dsl) {
        // NO_DSL: auto-create
        if (pos.entryPx && pos.size && pos.leverage) {
          this.createDslForPosition(strategyKey, cleanCoin, coin, pos, stratConfig);
          issues.push({
            level: 'CRITICAL',
            type: 'NO_DSL',
            strategyKey,
            asset: coin,
            action: 'auto_created',
            message: `${coin} ${pos.direction} had no DSL — auto-created`,
          });
        }
        continue;
      }

      // Validate schema
      const { valid, error } = validateDslState(dsl);
      if (!valid) {
        if (pos.entryPx && pos.size && pos.leverage) {
          this.createDslForPosition(strategyKey, cleanCoin, coin, pos, stratConfig);
          issues.push({
            level: 'CRITICAL',
            type: 'SCHEMA_INVALID',
            strategyKey,
            asset: coin,
            action: 'auto_replaced',
            message: `${coin} DSL had invalid schema (${error}) — auto-replaced`,
          });
        }
        continue;
      }

      if (!dsl.active && !dsl.pendingClose) {
        // DSL_INACTIVE: auto-replace
        if (pos.entryPx && pos.size && pos.leverage) {
          this.createDslForPosition(strategyKey, cleanCoin, coin, pos, stratConfig);
          issues.push({
            level: 'CRITICAL',
            type: 'DSL_INACTIVE',
            strategyKey,
            asset: coin,
            action: 'auto_replaced',
            message: `${coin} DSL was inactive — auto-replaced`,
          });
        }
        continue;
      }

      // Direction mismatch
      if (dsl.direction !== pos.direction) {
        this.createDslForPosition(strategyKey, cleanCoin, coin, pos, stratConfig);
        issues.push({
          level: 'CRITICAL',
          type: 'DIRECTION_MISMATCH',
          strategyKey,
          asset: coin,
          action: 'auto_replaced',
          message: `${coin} was ${dsl.direction} but position is ${pos.direction} — auto-replaced`,
        });
        continue;
      }

      // Approximate DSL reconciliation
      if (dsl.approximate && pos.entryPx > 0) {
        dsl.entryPrice = pos.entryPx;
        dsl.size = pos.size;
        dsl.leverage = pos.leverage;
        dsl.highWaterPrice = pos.entryPx;

        // Recalculate absoluteFloor
        const retracePrice = (Math.abs(dsl.phase1.retraceThreshold) / 100) / dsl.leverage;
        const absFloor = dsl.direction === 'LONG'
          ? roundTo(pos.entryPx * (1 - retracePrice), 6)
          : roundTo(pos.entryPx * (1 + retracePrice), 6);
        dsl.phase1.absoluteFloor = absFloor;
        dsl.floorPrice = absFloor;
        delete dsl.approximate;

        this.stateManager.setDslState(this.skillName, strategyKey, cleanCoin, dsl);
        issues.push({
          level: 'INFO',
          type: 'APPROXIMATE_DSL_RECONCILED',
          strategyKey,
          asset: coin,
          action: 'updated_state',
          message: `${coin} approximate DSL reconciled (entry=${pos.entryPx})`,
        });
        continue;
      }

      // Size/entry/leverage drift reconciliation
      const updates: Partial<DslState> = {};
      if (pos.size && dsl.size && pctDiff(pos.size, dsl.size) > 1) {
        updates.size = pos.size;
      }
      if (pos.entryPx && dsl.entryPrice && pctDiff(pos.entryPx, dsl.entryPrice) > 0.1) {
        updates.entryPrice = pos.entryPx;
      }
      if (pos.leverage && dsl.leverage && Math.abs(pos.leverage - dsl.leverage) > 0.5) {
        updates.leverage = pos.leverage;
      }

      if (Object.keys(updates).length > 0) {
        Object.assign(dsl, updates);
        // Reset HW if entry moved past it
        if (updates.entryPrice) {
          const hw = dsl.highWaterPrice;
          if ((dsl.direction === 'LONG' && updates.entryPrice > hw) ||
              (dsl.direction === 'SHORT' && updates.entryPrice < hw)) {
            dsl.highWaterPrice = updates.entryPrice;
          }
        }
        this.stateManager.setDslState(this.skillName, strategyKey, cleanCoin, dsl);
        issues.push({
          level: 'INFO',
          type: 'STATE_RECONCILED',
          strategyKey,
          asset: coin,
          action: 'updated_state',
          message: `${coin} DSL reconciled: ${Object.keys(updates).join(', ')}`,
        });
      }

      // DSL freshness check
      if (dsl.lastCheck) {
        const lastCheck = new Date(dsl.lastCheck).getTime();
        const ageMin = (now.getTime() - lastCheck) / 60_000;
        if (ageMin > 10) {
          issues.push({
            level: 'WARNING',
            type: 'DSL_STALE',
            strategyKey,
            asset: coin,
            action: 'alert_only',
            message: `${coin} DSL last checked ${Math.round(ageMin)}min ago`,
          });
        }
      }
    }

    // Check for orphan DSLs
    if (!hadFetchError) {
      for (const { asset, state } of dslStates) {
        const cleanAsset = asset.replace('xyz:', '');
        const hasOnChain =
          onChainPositions.has(asset) ||
          onChainPositions.has(cleanAsset) ||
          onChainPositions.has(`xyz:${asset}`);

        if (!hasOnChain) {
          // Skip recent approximate DSLs
          if (state.approximate && state.createdAt) {
            const ageMin = (now.getTime() - new Date(state.createdAt).getTime()) / 60_000;
            if (ageMin < 10) continue;
          }

          state.active = false;
          state.closeReason = 'externally_closed_detected_by_healthcheck';
          this.stateManager.setDslState(this.skillName, strategyKey, asset, state);
          issues.push({
            level: 'WARNING',
            type: 'ORPHAN_DSL',
            strategyKey,
            asset,
            action: 'auto_deactivated',
            message: `${asset} DSL was active but no position found — auto-deactivated`,
          });
        }
      }
    }

    return issues;
  }

  private createDslForPosition(
    strategyKey: string,
    cleanCoin: string,
    coin: string,
    pos: { direction: string; size: number; entryPx: number; leverage: number },
    stratConfig: { wallet: string; tiers?: DslTier[] },
  ): void {
    const now = new Date().toISOString();
    const isLong = pos.direction === 'LONG';
    const tiers = stratConfig.tiers ?? [
      { triggerPct: 5, lockPct: 50, breaches: 3 },
      { triggerPct: 10, lockPct: 65, breaches: 2 },
      { triggerPct: 15, lockPct: 75, breaches: 2 },
      { triggerPct: 20, lockPct: 85, breaches: 1 },
    ];

    const retracePrice = (10 / 100) / pos.leverage;
    const absFloor = isLong
      ? roundTo(pos.entryPx * (1 - retracePrice), 6)
      : roundTo(pos.entryPx * (1 + retracePrice), 6);

    const dsl: DslState = {
      version: 2,
      asset: cleanCoin,
      direction: pos.direction as 'LONG' | 'SHORT',
      entryPrice: pos.entryPx,
      size: pos.size,
      leverage: pos.leverage,
      active: true,
      highWaterPrice: pos.entryPx,
      phase: 1,
      currentBreachCount: 0,
      currentTierIndex: null,
      tierFloorPrice: 0,
      floorPrice: absFloor,
      tiers,
      phase1: {
        retraceThreshold: 10,
        absoluteFloor: absFloor,
        consecutiveBreachesRequired: 3,
      },
      phase2TriggerTier: 0,
      createdAt: now,
      lastCheck: now,
      strategyKey,
      wallet: stratConfig.wallet,
      dex: coin.startsWith('xyz:') ? 'xyz' : 'hl',
      createdBy: 'healthcheck_auto_create',
    };

    this.stateManager.setDslState(this.skillName, strategyKey, cleanCoin, dsl);
  }
}
