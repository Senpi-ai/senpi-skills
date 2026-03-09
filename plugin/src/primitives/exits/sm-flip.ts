/**
 * SM Flip Checker — Smart Money conviction flip detector.
 *
 * Ports: sm-flip-check.py (147 lines)
 *
 * Binary threshold logic. Fires on_position_closed hook when FLIP_NOW detected.
 * Checks if SM direction has flipped against any active positions.
 */

import { BackgroundService, LeaderboardMarket } from '../../core/types.js';
import { StateManager } from '../../core/state-manager.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { HookSystem } from '../../core/hook-system.js';
import { NotificationService } from '../../core/notifications.js';
import { logger } from '../../core/logger.js';

interface SmMapEntry {
  direction: string;
  pnlPct: number;
  traders: number;
  avgAtPeak: number;
  nearPeakPct: number;
}

interface FlipAlert {
  asset: string;
  myDirection: string;
  smDirection: string;
  alertLevel: 'FLIP_NOW' | 'FLIP_WARNING' | 'WATCH' | 'none';
  conviction: number;
  smPnlPct: number;
  smTraders: number;
  strategyKey: string;
}

export class SmFlipService implements BackgroundService {
  name = 'sm-flip-checker';
  intervalMs: number;

  private stateManager: StateManager;
  private mcp: SenpiMcpClient;
  private hookSystem: HookSystem;
  private notifications: NotificationService;
  private skillName: string;

  constructor(config: {
    stateManager: StateManager;
    mcp: SenpiMcpClient;
    hookSystem: HookSystem;
    notifications: NotificationService;
    skillName: string;
    intervalMs: number;
  }) {
    this.stateManager = config.stateManager;
    this.mcp = config.mcp;
    this.hookSystem = config.hookSystem;
    this.notifications = config.notifications;
    this.skillName = config.skillName;
    this.intervalMs = config.intervalMs;
  }

  async tick(): Promise<void> {
    // Get all active positions
    const activeStates = this.stateManager.listActiveDslStates(this.skillName);
    if (activeStates.length === 0) return;

    // Fetch SM data
    let rawMarkets: LeaderboardMarket[];
    try {
      rawMarkets = await this.mcp.getLeaderboardMarkets();
    } catch (err) {
      logger.error('SM flip: leaderboard fetch failed', { error: String(err) });
      return;
    }

    // Build asset -> SM map
    const smMap = new Map<string, SmMapEntry>();
    for (const m of rawMarkets) {
      const asset = (m.token || '').toUpperCase();
      if (!asset) continue;

      const pnlPct = m.pct_of_top_traders_gain * 100;
      const traders = m.trader_count;
      const direction = (m.direction || '').toUpperCase();
      const avgAtPeak = m.avgAtPeak ?? 50;
      const nearPeakPct = m.nearPeakPct ?? 0;

      const existing = smMap.get(asset);
      if (!existing || Math.abs(pnlPct) > Math.abs(existing.pnlPct)) {
        smMap.set(asset, { direction, pnlPct, traders, avgAtPeak, nearPeakPct });
      }
    }

    // Check each position for SM flip
    const alerts: FlipAlert[] = [];

    for (const { asset, strategyKey, state } of activeStates) {
      const assetUpper = asset.toUpperCase();
      const sm = smMap.get(assetUpper);
      if (!sm) continue;

      const myDir = state.direction.toUpperCase();
      const smDir = sm.direction;

      const flipped =
        (myDir === 'LONG' && smDir === 'SHORT') ||
        (myDir === 'SHORT' && smDir === 'LONG');

      if (!flipped) continue;

      // Conviction scoring
      let conviction = 0;
      if (sm.pnlPct > 5) conviction += 2;
      else if (sm.pnlPct > 1) conviction += 1;
      if (sm.traders > 100) conviction += 2;
      else if (sm.traders > 30) conviction += 1;
      if (sm.nearPeakPct > 50) conviction += 2;
      else if (sm.nearPeakPct > 20) conviction += 1;
      if (sm.avgAtPeak > 80) conviction += 1;

      let alertLevel: FlipAlert['alertLevel'];
      if (conviction >= 4) alertLevel = 'FLIP_NOW';
      else if (conviction >= 2) alertLevel = 'FLIP_WARNING';
      else alertLevel = 'WATCH';

      alerts.push({
        asset: assetUpper,
        myDirection: myDir,
        smDirection: smDir,
        alertLevel,
        conviction,
        smPnlPct: sm.pnlPct,
        smTraders: sm.traders,
        strategyKey,
      });

      // Auto-close on FLIP_NOW
      if (alertLevel === 'FLIP_NOW') {
        const wallet = state.wallet ?? '';
        if (wallet) {
          const coin = state.dex === 'xyz'
            ? (asset.startsWith('xyz:') ? asset : `xyz:${asset}`)
            : asset;

          const closeResult = await this.mcp.closePosition(
            wallet,
            coin,
            { reason: `SM flip: conviction ${conviction}, ${sm.traders} traders flipped ${smDir}` },
          );

          if (closeResult.success) {
            state.active = false;
            state.closeReason = `SM FLIP_NOW: conviction ${conviction}`;
            state.closedAt = new Date().toISOString();
            this.stateManager.setDslState(this.skillName, strategyKey, asset, state);

            const notif = `\u{1F534} SM FLIP CLOSE: ${asset} ${myDir} [${strategyKey}] — conviction ${conviction}, ${sm.traders} traders now ${smDir}`;
            await this.notifications.send(notif);

            await this.hookSystem.fire(this.skillName, {
              type: 'on_position_closed',
              skillName: this.skillName,
              strategyKey,
              data: {
                asset,
                direction: myDir,
                closeReason: `SM FLIP_NOW: conviction ${conviction}`,
                smConviction: conviction,
                smTraders: sm.traders,
              },
              timestamp: new Date().toISOString(),
            });
          }
        }
      }
    }

    if (alerts.length > 0) {
      logger.info('SM flip check complete', {
        alerts: alerts.length,
        flipNow: alerts.filter((a) => a.alertLevel === 'FLIP_NOW').length,
      });
    }
  }
}
