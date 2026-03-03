import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import type {
  TigerPaths,
  TigerConfig,
  TigerState,
  DslState,
  TradeLogEntry,
  SafetyState,
} from './types.js';

const DEFAULT_SAFETY: SafetyState = {
  halted: false,
  haltReason: null,
  dailyLossPct: 0,
  tradesToday: 0,
};

const DEFAULT_STATE: TigerState = {
  version: 1,
  active: true,
  instanceKey: null,
  createdAt: null,
  updatedAt: null,
  currentBalance: 0,
  peakBalance: 0,
  dayStartBalance: 0,
  dailyPnl: 0,
  totalPnl: 0,
  tradesToday: 0,
  winsToday: 0,
  totalTrades: 0,
  totalWins: 0,
  aggression: 'NORMAL',
  dailyRateNeeded: 0,
  daysRemaining: 7,
  dayNumber: 1,
  activePositions: {},
  safety: { ...DEFAULT_SAFETY },
  lastGoalRecalc: null,
  lastBtcPrice: null,
  lastBtcCheck: null,
};

export class StateManager {
  constructor(private readonly paths: TigerPaths) {}

  private instanceDir(strategyId: string): string {
    return path.join(this.paths.stateDir, strategyId);
  }

  async readConfig(): Promise<TigerConfig> {
    const content = await fs.readFile(this.paths.configFile, 'utf-8');
    return JSON.parse(content) as TigerConfig;
  }

  async getDefaultStrategyId(): Promise<string> {
    try {
      const config = await this.readConfig();
      return config.strategyId ?? 'default';
    } catch {
      return 'default';
    }
  }

  async readState(strategyId: string): Promise<TigerState> {
    const dir = this.instanceDir(strategyId);
    const statePath = path.join(dir, 'tiger-state.json');

    try {
      const content = await fs.readFile(statePath, 'utf-8');
      const saved = JSON.parse(content) as Partial<TigerState>;
      return {
        ...DEFAULT_STATE,
        ...saved,
        safety: { ...DEFAULT_SAFETY, ...saved.safety },
        instanceKey: saved.instanceKey ?? strategyId,
      };
    } catch {
      return { ...DEFAULT_STATE, instanceKey: strategyId };
    }
  }

  async readDslState(
    strategyId: string,
    asset: string,
  ): Promise<DslState | null> {
    const dir = this.instanceDir(strategyId);
    const dslPath = path.join(dir, `dsl-${asset}.json`);

    try {
      const content = await fs.readFile(dslPath, 'utf-8');
      return JSON.parse(content) as DslState;
    } catch {
      return null;
    }
  }

  async listDslStates(strategyId: string): Promise<string[]> {
    const dir = this.instanceDir(strategyId);

    try {
      const files = await fs.readdir(dir);
      return files
        .filter((f) => f.startsWith('dsl-') && f.endsWith('.json'))
        .map((f) => f.slice(4, -5)); // strip "dsl-" prefix and ".json" suffix
    } catch {
      return [];
    }
  }

  async readTradeLog(strategyId: string): Promise<TradeLogEntry[]> {
    const dir = this.instanceDir(strategyId);
    const logPath = path.join(dir, 'trade-log.json');

    try {
      const content = await fs.readFile(logPath, 'utf-8');
      const data = JSON.parse(content);
      return Array.isArray(data) ? (data as TradeLogEntry[]) : [];
    } catch {
      return [];
    }
  }
}
