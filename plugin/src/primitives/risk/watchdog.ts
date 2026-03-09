/**
 * Watchdog — Margin buffer, liquidation distance monitoring.
 *
 * Ports: wolf-monitor.py (252 lines)
 *
 * Monitors margin buffer and liquidation distances per strategy.
 * Fires on_position_at_risk hook for CRITICAL alerts.
 */

import { BackgroundService, ClearinghouseState, DslState } from '../../core/types.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { StateManager } from '../../core/state-manager.js';
import { HookSystem } from '../../core/hook-system.js';
import { NotificationService } from '../../core/notifications.js';
import { logger } from '../../core/logger.js';
import { roundTo } from '../../core/types.js';

interface WatchdogAlert {
  level: 'CRITICAL' | 'WARNING' | 'INFO';
  strategyKey: string;
  msg: string;
}

export class WatchdogService implements BackgroundService {
  name = 'watchdog';
  intervalMs = 300_000; // 5 minutes

  private mcp: SenpiMcpClient;
  private stateManager: StateManager;
  private hookSystem: HookSystem;
  private notifications: NotificationService;
  private skillName: string;
  private strategies: Map<string, { wallet: string; slots: number }>;

  constructor(config: {
    mcp: SenpiMcpClient;
    stateManager: StateManager;
    hookSystem: HookSystem;
    notifications: NotificationService;
    skillName: string;
    strategies: Map<string, { wallet: string; slots: number }>;
  }) {
    this.mcp = config.mcp;
    this.stateManager = config.stateManager;
    this.hookSystem = config.hookSystem;
    this.notifications = config.notifications;
    this.skillName = config.skillName;
    this.strategies = config.strategies;
  }

  async tick(): Promise<void> {
    const allAlerts: WatchdogAlert[] = [];

    for (const [strategyKey, stratConfig] of this.strategies) {
      if (!stratConfig.wallet) continue;

      const chData = await this.mcp.getClearinghouseState(stratConfig.wallet);
      if (!chData) {
        allAlerts.push({
          level: 'WARNING',
          strategyKey,
          msg: `Clearinghouse fetch failed for ${strategyKey}`,
        });
        continue;
      }

      // Check crypto (main) section
      const main = chData.main;
      if (main?.marginSummary) {
        const acctValue = parseFloat(main.marginSummary.accountValue || '0');
        const totalMargin = parseFloat(main.marginSummary.totalMarginUsed || '0');
        const maintMargin = main.crossMaintenanceMarginUsed || 0;

        if (acctValue > 0) {
          const bufferPct = roundTo(((acctValue - maintMargin) / acctValue) * 100, 1);

          if (bufferPct < 50) {
            const alert: WatchdogAlert = {
              level: bufferPct < 30 ? 'CRITICAL' : 'WARNING',
              strategyKey,
              msg: `[${strategyKey}] Cross-margin buffer: ${bufferPct}% (account $${roundTo(acctValue, 2)}, maint margin $${roundTo(maintMargin, 2)})`,
            };
            allAlerts.push(alert);
          }
        }
      }

      // Check individual positions
      for (const sectionKey of ['main', 'xyz'] as const) {
        const section = chData[sectionKey];
        if (!section?.assetPositions) continue;

        for (const ap of section.assetPositions) {
          const pos = ap.position;
          if (!pos) continue;

          const szi = parseFloat(pos.szi || '0');
          if (szi === 0) continue;

          const coin = pos.coin;
          const direction = szi > 0 ? 'LONG' : 'SHORT';
          const roe = parseFloat(pos.returnOnEquity || '0') * 100;
          const posValue = parseFloat(pos.positionValue || '0');
          const price = Math.abs(szi) > 0 ? posValue / Math.abs(szi) : 0;
          const liq = pos.liquidationPx ? parseFloat(pos.liquidationPx) : null;

          // Get DSL floor
          const cleanCoin = coin.replace('xyz:', '');
          const dsl = this.stateManager.getDslState(this.skillName, strategyKey, cleanCoin);
          const dslFloor = dsl?.active ? dsl.floorPrice : null;

          // Calculate distances
          let liqDistPct: number | null = null;
          let dslDistPct: number | null = null;

          if (liq && direction === 'LONG' && price > 0) {
            liqDistPct = roundTo(((price - liq) / price) * 100, 1);
          } else if (liq && direction === 'SHORT' && price > 0) {
            liqDistPct = roundTo(((liq - price) / price) * 100, 1);
          }

          if (dslFloor && direction === 'LONG' && price > 0) {
            dslDistPct = roundTo(((price - dslFloor) / price) * 100, 1);
          } else if (dslFloor && direction === 'SHORT' && price > 0) {
            dslDistPct = roundTo(((dslFloor - price) / price) * 100, 1);
          }

          // Alert: liq closer than DSL
          if (liqDistPct !== null && dslDistPct !== null && liqDistPct < dslDistPct) {
            allAlerts.push({
              level: 'CRITICAL',
              strategyKey,
              msg: `[${strategyKey}] ${coin} ${direction}: Liquidation (${liqDistPct}% away) CLOSER than DSL floor (${dslDistPct}% away)!`,
            });
          }

          // Alert: bad ROE
          if (roe < -15) {
            allAlerts.push({
              level: 'WARNING',
              strategyKey,
              msg: `[${strategyKey}] ${coin} ${direction}: ROE at ${roundTo(roe, 1)}% -- approaching danger zone`,
            });
          }

          // Alert: liquidation close
          if (liqDistPct !== null && liqDistPct < 30) {
            allAlerts.push({
              level: 'WARNING',
              strategyKey,
              msg: `[${strategyKey}] ${coin} ${direction}: Liquidation only ${liqDistPct}% away${sectionKey === 'xyz' ? ' (isolated)' : ''}`,
            });
          }
        }
      }
    }

    // Fire hooks for critical alerts
    for (const alert of allAlerts.filter((a) => a.level === 'CRITICAL')) {
      await this.hookSystem.fire(this.skillName, {
        type: 'on_position_at_risk',
        skillName: this.skillName,
        strategyKey: alert.strategyKey,
        data: { msg: alert.msg, level: alert.level },
        timestamp: new Date().toISOString(),
      });
    }

    if (allAlerts.length > 0) {
      logger.info('Watchdog check complete', {
        alerts: allAlerts.length,
        critical: allAlerts.filter((a) => a.level === 'CRITICAL').length,
      });
    }
  }
}
