/**
 * DSL Runner — Background service that ticks all positions.
 *
 * Ports: dsl-combined.py → main loop
 *
 * Iterates all active DSL states across all skills.
 * Gets prices from shared cache. Runs dslTick(). Closes positions on breach.
 * Fires hooks for position closed / tier changed events.
 */

import { BackgroundService, DslConfig, DslState } from '../../core/types.js';
import { StateManager } from '../../core/state-manager.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { PriceCache } from '../../core/price-cache.js';
import { HookSystem } from '../../core/hook-system.js';
import { NotificationService } from '../../core/notifications.js';
import { logger } from '../../core/logger.js';
import { dslTick } from './dsl-engine.js';

export class DslRunnerService implements BackgroundService {
  name = 'dsl-runner';
  intervalMs = 180_000; // 3 minutes

  private stateManager: StateManager;
  private mcp: SenpiMcpClient;
  private priceCache: PriceCache;
  private hookSystem: HookSystem;
  private notifications: NotificationService;
  private skillName: string;
  private dslConfigs: Map<string, DslConfig>;

  constructor(config: {
    stateManager: StateManager;
    mcp: SenpiMcpClient;
    priceCache: PriceCache;
    hookSystem: HookSystem;
    notifications: NotificationService;
    skillName: string;
    dslConfigs?: Map<string, DslConfig>;
  }) {
    this.stateManager = config.stateManager;
    this.mcp = config.mcp;
    this.priceCache = config.priceCache;
    this.hookSystem = config.hookSystem;
    this.notifications = config.notifications;
    this.skillName = config.skillName;
    this.dslConfigs = config.dslConfigs ?? new Map();
  }

  async tick(): Promise<void> {
    // Check price staleness
    if (this.priceCache.isCriticallyStale()) {
      logger.error('DSL runner: prices critically stale, skipping tick');
      return;
    }
    if (this.priceCache.isStale()) {
      logger.warn('DSL runner: prices stale, proceeding with caution');
    }

    // Get all active DSL states
    const activeStates = this.stateManager.listActiveDslStates(this.skillName);
    if (activeStates.length === 0) return;

    // Fetch XYZ prices if any XYZ positions exist
    const hasXyz = activeStates.some(
      (s) => s.state.dex === 'xyz' || s.state.asset.startsWith('xyz:'),
    );
    if (hasXyz) {
      await this.priceCache.fetchXyzPrices();
    }

    const now = new Date();
    const closedPositions: string[] = [];
    const tierChanges: string[] = [];

    for (const { asset, strategyKey, state } of activeStates) {
      try {
        // Skip approximate DSLs (health check will reconcile)
        if (state.approximate) {
          logger.debug(`Skipping approximate DSL: ${asset} [${strategyKey}]`);
          continue;
        }

        // Resolve price
        const isXyz = state.dex === 'xyz' || asset.startsWith('xyz:');
        const priceLookup = isXyz ? `xyz:${asset.replace('xyz:', '')}` : asset;
        const price = this.priceCache.getPrice(priceLookup);

        if (price === null) {
          // Track consecutive fetch failures
          const fails = (state.consecutiveFetchFailures ?? 0) + 1;
          state.consecutiveFetchFailures = fails;
          state.lastCheck = now.toISOString();

          const maxFails = state.maxFetchFailures ?? 10;
          if (fails >= maxFails) {
            state.active = false;
            state.closeReason = `Auto-deactivated: ${fails} consecutive fetch failures`;
            logger.error(`DSL deactivated: ${asset} — ${fails} fetch failures`);
          }

          this.stateManager.setDslState(this.skillName, strategyKey, asset, state);
          continue;
        }

        state.consecutiveFetchFailures = 0;

        // Get strategy-specific DSL config
        const dslConfig = this.dslConfigs.get(strategyKey) ?? {};

        // Run DSL tick
        const result = dslTick(state, price, now, dslConfig);

        // Handle close
        if (result.action === 'close') {
          const wallet = state.wallet ?? '';
          const closeCoin = isXyz
            ? (asset.startsWith('xyz:') ? asset : `xyz:${asset}`)
            : asset;

          if (wallet) {
            const closeResult = await this.mcp.closePosition(
              wallet,
              closeCoin,
              { reason: result.closeReason ?? 'DSL breach' },
            );

            if (closeResult.success) {
              result.updatedState.active = false;
              result.updatedState.pendingClose = false;
              result.updatedState.closedAt = now.toISOString();
              result.updatedState.closeReason = result.closeReason ?? 'DSL breach';

              const notif = `\u{1F534} CLOSED ${asset} ${state.direction} [${strategyKey}]: ${result.closeReason} | uPnL: $${result.metrics.upnl.toFixed(2)}`;
              await this.notifications.send(notif);
              closedPositions.push(asset);

              // Fire hook
              await this.hookSystem.fire(this.skillName, {
                type: 'on_position_closed',
                skillName: this.skillName,
                strategyKey,
                data: {
                  asset,
                  direction: state.direction,
                  closeReason: result.closeReason,
                  upnl: result.metrics.upnl,
                  upnlPct: result.metrics.upnlPct,
                },
                timestamp: now.toISOString(),
              });
            } else {
              result.updatedState.pendingClose = true;
              logger.error(`Close failed for ${asset}: ${closeResult.result}`);
            }
          }
        }

        // Handle tier change
        if (result.tierChanged) {
          tierChanges.push(asset);
          await this.hookSystem.fire(this.skillName, {
            type: 'on_tier_changed',
            skillName: this.skillName,
            strategyKey,
            data: {
              asset,
              previousTier: result.previousTierIndex,
              newTier: result.newTierIndex,
              phase: result.newPhase,
            },
            timestamp: now.toISOString(),
          });
        }

        // Save updated state
        this.stateManager.setDslState(this.skillName, strategyKey, asset, result.updatedState);
      } catch (err) {
        logger.error(`DSL tick error for ${asset} [${strategyKey}]`, {
          error: String(err),
        });
      }
    }

    // Flush state changes
    await this.stateManager.flushAll();

    if (closedPositions.length > 0 || tierChanges.length > 0) {
      logger.info('DSL runner tick complete', {
        positions: activeStates.length,
        closed: closedPositions,
        tierChanges,
      });
    }
  }
}
