/**
 * Risk Guard — Guard rails, gate states, daily limits.
 *
 * Ports: risk-guardian.py + guard rail logic from wolf_config.py
 *
 * Key improvement over Python: Risk guard is hook-triggered (on_position_closed),
 * not polled every 5 minutes. Catches losses instantly.
 */

import { GateStatus, TradeCounter, StrategyConfig } from '../../core/types.js';
import { StateManager } from '../../core/state-manager.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { logger } from '../../core/logger.js';

const GUARD_RAIL_DEFAULTS = {
  maxEntriesPerDay: 8,
  bypassOnProfit: true,
  maxConsecutiveLosses: 3,
  cooldownMinutes: 60,
};

export interface GateCheckResult {
  gate: GateStatus;
  reason?: string;
}

export class RiskGuard {
  private stateManager: StateManager;
  private mcp: SenpiMcpClient;

  constructor(stateManager: StateManager, mcp: SenpiMcpClient) {
    this.stateManager = stateManager;
    this.mcp = mcp;
  }

  /** Load or create the daily trade counter for a strategy */
  private loadTradeCounter(strategyKey: string, guardRails?: StrategyConfig['guardRails']): TradeCounter {
    const existing = this.stateManager.read<TradeCounter>(`trade-counter-${strategyKey}`);
    const today = new Date().toISOString().split('T')[0];

    const grCfg = guardRails ?? {};
    const mergedConfig = {
      maxEntriesPerDay: grCfg.maxEntriesPerDay ?? GUARD_RAIL_DEFAULTS.maxEntriesPerDay,
      bypassOnProfit: grCfg.bypassOnProfit ?? GUARD_RAIL_DEFAULTS.bypassOnProfit,
      maxConsecutiveLosses: grCfg.maxConsecutiveLosses ?? GUARD_RAIL_DEFAULTS.maxConsecutiveLosses,
      cooldownMinutes: grCfg.cooldownMinutes ?? GUARD_RAIL_DEFAULTS.cooldownMinutes,
    };

    if (existing && existing.date === today) {
      // Same day — update config overlay but keep counters
      return { ...existing, ...mergedConfig };
    }

    // Day rollover — reset daily counters, preserve streaks + active cooldown
    const counter: TradeCounter = {
      date: today,
      accountValueStart: null,
      entries: 0,
      closedTrades: 0,
      realizedPnl: 0,
      gate: 'OPEN',
      gateReason: null,
      cooldownUntil: null,
      lastResults: existing?.lastResults ?? [],
      processedOrderIds: [],
      updatedAt: null,
      ...mergedConfig,
    };

    // Preserve active cooldown across day boundary
    if (existing?.cooldownUntil) {
      const cd = new Date(existing.cooldownUntil);
      if (cd > new Date()) {
        counter.gate = 'COOLDOWN';
        counter.gateReason = 'consecutive_losses_cooldown (carried from previous day)';
        counter.cooldownUntil = existing.cooldownUntil;
      }
    }

    return counter;
  }

  /** Save the trade counter */
  private saveTradeCounter(strategyKey: string, counter: TradeCounter): void {
    counter.updatedAt = new Date().toISOString();
    this.stateManager.write(`trade-counter-${strategyKey}`, counter);
  }

  /** Check if the gate is open for entries */
  checkGate(strategyKey: string, guardRails?: StrategyConfig['guardRails']): GateCheckResult {
    const counter = this.loadTradeCounter(strategyKey, guardRails);

    if (counter.gate === 'CLOSED') {
      return { gate: 'CLOSED', reason: counter.gateReason ?? undefined };
    }

    if (counter.gate === 'COOLDOWN' && counter.cooldownUntil) {
      const cd = new Date(counter.cooldownUntil);
      if (cd > new Date()) {
        return { gate: 'COOLDOWN', reason: counter.gateReason ?? undefined };
      }

      // Cooldown expired — clear
      counter.gate = 'OPEN';
      counter.gateReason = null;
      counter.cooldownUntil = null;
      counter.lastResults.push('R');
      counter.lastResults = counter.lastResults.slice(-20);
      this.saveTradeCounter(strategyKey, counter);
    }

    return { gate: 'OPEN' };
  }

  /** Record a new entry */
  recordEntry(strategyKey: string, guardRails?: StrategyConfig['guardRails']): void {
    const counter = this.loadTradeCounter(strategyKey, guardRails);
    counter.entries += 1;
    this.saveTradeCounter(strategyKey, counter);
  }

  /** Called on every position close — evaluates guard rails instantly */
  async onPositionClosed(
    strategyKey: string,
    pnl: number,
    closedOrderId: string,
    config: StrategyConfig,
  ): Promise<{ gateAction: GateCheckResult; notifications: string[] }> {
    const counter = this.loadTradeCounter(strategyKey, config.guardRails);
    const notifications: string[] = [];

    // Skip if already processed
    if (counter.processedOrderIds.includes(closedOrderId)) {
      return { gateAction: { gate: counter.gate }, notifications };
    }

    // Record the close
    const result: 'W' | 'L' = pnl >= 0 ? 'W' : 'L';
    counter.lastResults.push(result);
    counter.lastResults = counter.lastResults.slice(-20);
    counter.realizedPnl += pnl;
    counter.closedTrades += 1;
    counter.processedOrderIds.push(closedOrderId);

    // If already CLOSED for today, stay closed
    if (counter.gate === 'CLOSED') {
      this.saveTradeCounter(strategyKey, counter);
      return { gateAction: { gate: 'CLOSED', reason: counter.gateReason ?? undefined }, notifications };
    }

    // ── G1: Daily Loss Halt ──
    const dailyLossLimit = config.dailyLossLimit ?? 0;
    if (dailyLossLimit > 0 && counter.accountValueStart !== null) {
      const currentValue = await this.getAccountValue(config.wallet);
      if (currentValue !== null) {
        const dailyPnl = currentValue - counter.accountValueStart;
        if (dailyPnl <= -dailyLossLimit) {
          counter.gate = 'CLOSED';
          counter.gateReason = `G1 daily loss halt: PnL $${dailyPnl.toFixed(2)} exceeded -$${dailyLossLimit.toFixed(2)} limit`;
          notifications.push(`\u{1F6D1} GATE CLOSED [${strategyKey}]: ${counter.gateReason}`);
          this.saveTradeCounter(strategyKey, counter);
          return { gateAction: { gate: 'CLOSED', reason: counter.gateReason }, notifications };
        }
      }
    }

    // ── G3: Max Entries Per Day ──
    const maxEntries = counter.maxEntriesPerDay;
    if (counter.entries >= maxEntries) {
      const bypass = counter.bypassOnProfit;
      let shouldClose = true;

      if (bypass && counter.accountValueStart !== null) {
        const currentValue = await this.getAccountValue(config.wallet);
        if (currentValue !== null && currentValue > counter.accountValueStart) {
          shouldClose = false; // bypass — profitable day
        }
      }

      if (shouldClose) {
        counter.gate = 'CLOSED';
        counter.gateReason = `G3 max entries: ${counter.entries}/${maxEntries} entries reached`;
        notifications.push(`\u{1F6D1} GATE CLOSED [${strategyKey}]: ${counter.gateReason}`);
        this.saveTradeCounter(strategyKey, counter);
        return { gateAction: { gate: 'CLOSED', reason: counter.gateReason }, notifications };
      }
    }

    // ── G4: Consecutive Losses Cooldown ──
    const maxConsec = counter.maxConsecutiveLosses;
    const cooldownMin = counter.cooldownMinutes;

    if (counter.lastResults.length >= maxConsec) {
      const tail = counter.lastResults.slice(-maxConsec);
      if (tail.every((r) => r === 'L')) {
        // Check if already in active cooldown
        if (counter.gate === 'COOLDOWN' && counter.cooldownUntil) {
          const cd = new Date(counter.cooldownUntil);
          if (cd > new Date()) {
            this.saveTradeCounter(strategyKey, counter);
            return { gateAction: { gate: 'COOLDOWN', reason: counter.gateReason ?? undefined }, notifications };
          }
        }

        // Set new cooldown
        const expiry = new Date(Date.now() + cooldownMin * 60_000);
        counter.gate = 'COOLDOWN';
        counter.cooldownUntil = expiry.toISOString();
        counter.gateReason = `G4 consecutive losses: ${maxConsec} losses in a row, cooldown until ${counter.cooldownUntil}`;
        notifications.push(`\u23F3 COOLDOWN [${strategyKey}]: ${counter.gateReason}`);
        this.saveTradeCounter(strategyKey, counter);
        return { gateAction: { gate: 'COOLDOWN', reason: counter.gateReason }, notifications };
      }
    }

    this.saveTradeCounter(strategyKey, counter);
    return { gateAction: { gate: 'OPEN' }, notifications };
  }

  /** Set account value start for a strategy */
  async setAccountValueStart(strategyKey: string, wallet: string, guardRails?: StrategyConfig['guardRails']): Promise<void> {
    const counter = this.loadTradeCounter(strategyKey, guardRails);
    if (counter.accountValueStart === null) {
      const av = await this.getAccountValue(wallet);
      if (av !== null) {
        counter.accountValueStart = Math.round(av * 100) / 100;
        this.saveTradeCounter(strategyKey, counter);
      }
    }
  }

  /** Daily reset — called at midnight UTC */
  dailyReset(): void {
    // Trade counters auto-reset on day rollover via loadTradeCounter
    logger.info('Risk guard daily reset triggered');
  }

  /** Get trade counter for a strategy */
  getTradeCounter(strategyKey: string): TradeCounter | null {
    return this.stateManager.read<TradeCounter>(`trade-counter-${strategyKey}`);
  }

  /** Get current account value */
  private async getAccountValue(wallet: string): Promise<number | null> {
    const data = await this.mcp.getClearinghouseState(wallet);
    if (!data) return null;

    let total = 0;
    for (const sectionKey of ['main', 'xyz'] as const) {
      const section = data[sectionKey];
      if (section?.marginSummary?.accountValue) {
        total += parseFloat(section.marginSummary.accountValue);
      }
    }
    return total;
  }
}
