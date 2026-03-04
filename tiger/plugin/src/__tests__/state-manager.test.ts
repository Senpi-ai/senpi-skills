import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import { StateManager } from '../state-manager.js';
import type { TigerPaths } from '../types.js';

describe('StateManager', () => {
  let tmpDir: string;
  let paths: TigerPaths;
  let manager: StateManager;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'tiger-test-'));
    paths = {
      workspace: tmpDir,
      scriptsDir: path.join(tmpDir, 'scripts'),
      stateDir: path.join(tmpDir, 'state'),
      configFile: path.join(tmpDir, 'tiger-config.json'),
    };
    manager = new StateManager(paths);
    await fs.mkdir(paths.stateDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  describe('readConfig', () => {
    it('reads and parses tiger-config.json', async () => {
      await fs.writeFile(
        paths.configFile,
        JSON.stringify({ version: 1, budget: 1000, strategyId: 's1' }),
      );

      const config = await manager.readConfig();
      expect(config.version).toBe(1);
      expect(config.budget).toBe(1000);
      expect(config.strategyId).toBe('s1');
    });

    it('throws when config file missing', async () => {
      await expect(manager.readConfig()).rejects.toThrow();
    });
  });

  describe('getDefaultStrategyId', () => {
    it('returns strategyId from config', async () => {
      await fs.writeFile(
        paths.configFile,
        JSON.stringify({ strategyId: 'my-strat' }),
      );

      const id = await manager.getDefaultStrategyId();
      expect(id).toBe('my-strat');
    });

    it('returns "default" when config missing', async () => {
      const id = await manager.getDefaultStrategyId();
      expect(id).toBe('default');
    });

    it('returns "default" when strategyId is null', async () => {
      await fs.writeFile(
        paths.configFile,
        JSON.stringify({ strategyId: null }),
      );

      const id = await manager.getDefaultStrategyId();
      expect(id).toBe('default');
    });
  });

  describe('readState', () => {
    it('reads existing state with defaults merged', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'tiger-state.json'),
        JSON.stringify({
          active: true,
          currentBalance: 1500,
          aggression: 'ELEVATED',
        }),
      );

      const state = await manager.readState('s1');
      expect(state.active).toBe(true);
      expect(state.currentBalance).toBe(1500);
      expect(state.aggression).toBe('ELEVATED');
      // Defaults filled in
      expect(state.version).toBe(1);
      expect(state.totalTrades).toBe(0);
      expect(state.safety.halted).toBe(false);
    });

    it('returns defaults when state file missing', async () => {
      const state = await manager.readState('nonexistent');
      expect(state.active).toBe(true);
      expect(state.currentBalance).toBe(0);
      expect(state.aggression).toBe('NORMAL');
      expect(state.instanceKey).toBe('nonexistent');
    });

    it('merges safety defaults', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'tiger-state.json'),
        JSON.stringify({
          safety: { halted: true, haltReason: 'drawdown' },
        }),
      );

      const state = await manager.readState('s1');
      expect(state.safety.halted).toBe(true);
      expect(state.safety.haltReason).toBe('drawdown');
      expect(state.safety.dailyLossPct).toBe(0);
      expect(state.safety.tradesToday).toBe(0);
    });

    it('handles corrupted JSON gracefully', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'tiger-state.json'),
        'not valid json{{{',
      );

      const state = await manager.readState('s1');
      expect(state.active).toBe(true); // falls back to defaults
      expect(state.instanceKey).toBe('s1');
    });
  });

  describe('readDslState', () => {
    it('reads existing DSL state', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'dsl-ETH.json'),
        JSON.stringify({
          active: true,
          asset: 'ETH',
          direction: 'LONG',
          entryPrice: 3500,
          phase: 1,
          currentTierIndex: -1,
        }),
      );

      const dsl = await manager.readDslState('s1', 'ETH');
      expect(dsl).not.toBeNull();
      expect(dsl!.asset).toBe('ETH');
      expect(dsl!.direction).toBe('LONG');
      expect(dsl!.entryPrice).toBe(3500);
    });

    it('returns null when DSL state missing', async () => {
      const dsl = await manager.readDslState('s1', 'NONEXIST');
      expect(dsl).toBeNull();
    });
  });

  describe('listDslStates', () => {
    it('lists all DSL assets', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(path.join(instanceDir, 'dsl-ETH.json'), '{}');
      await fs.writeFile(path.join(instanceDir, 'dsl-BTC.json'), '{}');
      await fs.writeFile(path.join(instanceDir, 'tiger-state.json'), '{}');

      const assets = await manager.listDslStates('s1');
      expect(assets.sort()).toEqual(['BTC', 'ETH']);
    });

    it('returns empty array when instance dir missing', async () => {
      const assets = await manager.listDslStates('missing');
      expect(assets).toEqual([]);
    });

    it('returns empty array when no DSL files exist', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(path.join(instanceDir, 'tiger-state.json'), '{}');

      const assets = await manager.listDslStates('s1');
      expect(assets).toEqual([]);
    });
  });

  describe('dslStateExists', () => {
    it('returns true when DSL state file exists', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(path.join(instanceDir, 'dsl-ETH.json'), '{}');

      const exists = await manager.dslStateExists('s1', 'ETH');
      expect(exists).toBe(true);
    });

    it('returns false when DSL state file missing', async () => {
      const exists = await manager.dslStateExists('s1', 'NONEXIST');
      expect(exists).toBe(false);
    });

    it('returns false when instance dir missing', async () => {
      const exists = await manager.dslStateExists('missing', 'ETH');
      expect(exists).toBe(false);
    });
  });

  describe('writeDslState', () => {
    it('writes DSL state file atomically', async () => {
      const state = {
        active: true,
        asset: 'ETH',
        direction: 'LONG' as const,
        entryPrice: 3500,
        size: 1.5,
        leverage: 10,
        wallet: '0xabc',
        highWaterPrice: 3500,
        phase: 1,
        currentBreachCount: 0,
        currentTierIndex: -1,
        tierFloorPrice: null,
        pendingClose: false,
        phase1: { retraceThreshold: 0.015, consecutiveBreachesRequired: 3, absoluteFloor: 3430 },
        phase2: { retraceThreshold: 0.012, consecutiveBreachesRequired: 2 },
        phase2TriggerTier: 0,
        tiers: [{ triggerPct: 0.05, lockPct: 0.20, retrace: 0.015 }],
        breachDecay: 'soft' as const,
        createdAt: '2025-01-01T00:00:00Z',
        updatedAt: '2025-01-01T00:00:00Z',
        lastCheck: '2025-01-01T00:00:00Z',
        lastPrice: 3500,
        consecutiveFetchFailures: 0,
      };

      await manager.writeDslState('s1', 'ETH', state);

      const written = await fs.readFile(
        path.join(paths.stateDir, 's1', 'dsl-ETH.json'),
        'utf-8',
      );
      const parsed = JSON.parse(written);
      expect(parsed.active).toBe(true);
      expect(parsed.asset).toBe('ETH');
      expect(parsed.entryPrice).toBe(3500);
    });

    it('creates instance directory if it does not exist', async () => {
      const state = {
        active: true,
        asset: 'BTC',
        direction: 'SHORT' as const,
        entryPrice: 65000,
        size: 0.1,
        leverage: 5,
        wallet: '0xdef',
        highWaterPrice: 65000,
        phase: 1,
        currentBreachCount: 0,
        currentTierIndex: -1,
        tierFloorPrice: null,
        pendingClose: false,
        phase1: { retraceThreshold: 0.015, consecutiveBreachesRequired: 3, absoluteFloor: 66300 },
        phase2: { retraceThreshold: 0.012, consecutiveBreachesRequired: 2 },
        phase2TriggerTier: 0,
        tiers: [],
        breachDecay: 'soft' as const,
        createdAt: '2025-01-01T00:00:00Z',
        updatedAt: '2025-01-01T00:00:00Z',
        lastCheck: '2025-01-01T00:00:00Z',
        lastPrice: 65000,
        consecutiveFetchFailures: 0,
      };

      await manager.writeDslState('new-instance', 'BTC', state);

      const exists = await manager.dslStateExists('new-instance', 'BTC');
      expect(exists).toBe(true);
    });

    it('does not leave .tmp file after successful write', async () => {
      const state = {
        active: true,
        asset: 'SOL',
        direction: 'LONG' as const,
        entryPrice: 100,
        size: 10,
        leverage: 5,
        wallet: '0x123',
        highWaterPrice: 100,
        phase: 1,
        currentBreachCount: 0,
        currentTierIndex: -1,
        tierFloorPrice: null,
        pendingClose: false,
        phase1: { retraceThreshold: 0.015, consecutiveBreachesRequired: 3, absoluteFloor: 98 },
        phase2: { retraceThreshold: 0.012, consecutiveBreachesRequired: 2 },
        phase2TriggerTier: 0,
        tiers: [],
        breachDecay: 'soft' as const,
        createdAt: '2025-01-01T00:00:00Z',
        updatedAt: '2025-01-01T00:00:00Z',
        lastCheck: '2025-01-01T00:00:00Z',
        lastPrice: 100,
        consecutiveFetchFailures: 0,
      };

      await manager.writeDslState('s1', 'SOL', state);

      const dir = path.join(paths.stateDir, 's1');
      const files = await fs.readdir(dir);
      expect(files.filter((f) => f.endsWith('.tmp'))).toHaveLength(0);
    });

    it('overwrites existing DSL state', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'dsl-ETH.json'),
        JSON.stringify({ active: true, entryPrice: 3000 }),
      );

      const newState = {
        active: false,
        asset: 'ETH',
        direction: 'LONG' as const,
        entryPrice: 3500,
        size: 1.5,
        leverage: 10,
        wallet: '0xabc',
        highWaterPrice: 3600,
        phase: 2,
        currentBreachCount: 0,
        currentTierIndex: 1,
        tierFloorPrice: 3520,
        pendingClose: false,
        phase1: { retraceThreshold: 0.015, consecutiveBreachesRequired: 3, absoluteFloor: 3430 },
        phase2: { retraceThreshold: 0.012, consecutiveBreachesRequired: 2 },
        phase2TriggerTier: 0,
        tiers: [],
        breachDecay: 'soft' as const,
        createdAt: '2025-01-01T00:00:00Z',
        updatedAt: '2025-01-01T02:00:00Z',
        lastCheck: '2025-01-01T02:00:00Z',
        lastPrice: 3580,
        consecutiveFetchFailures: 0,
        closedAt: '2025-01-01T02:00:00Z',
        closeReason: 'OI_COLLAPSE',
      };

      await manager.writeDslState('s1', 'ETH', newState);

      const read = await manager.readDslState('s1', 'ETH');
      expect(read!.active).toBe(false);
      expect(read!.entryPrice).toBe(3500);
      expect(read!.closeReason).toBe('OI_COLLAPSE');
    });
  });

  describe('readTradeLog', () => {
    it('reads trade log entries', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      const entries = [
        {
          version: 1,
          timestamp: '2025-01-01T00:00:00Z',
          asset: 'ETH',
          pattern: 'COMPRESSION_BREAKOUT',
          direction: 'LONG',
          entryPrice: 3500,
          exitPrice: 3675,
          leverage: 10,
          sizeUsd: 300,
          pnlUsd: 150,
          feesUsd: 5.4,
          holdMinutes: 180,
          exitReason: 'DSL_TIER_2',
          confluenceScore: 0.65,
          aggression: 'NORMAL',
        },
      ];
      await fs.writeFile(
        path.join(instanceDir, 'trade-log.json'),
        JSON.stringify(entries),
      );

      const log = await manager.readTradeLog('s1');
      expect(log).toHaveLength(1);
      expect(log[0].asset).toBe('ETH');
      expect(log[0].pnlUsd).toBe(150);
    });

    it('returns empty array when trade log missing', async () => {
      const log = await manager.readTradeLog('missing');
      expect(log).toEqual([]);
    });

    it('returns empty array for non-array JSON', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'trade-log.json'),
        JSON.stringify({ not: 'an array' }),
      );

      const log = await manager.readTradeLog('s1');
      expect(log).toEqual([]);
    });

    it('returns empty array for corrupted JSON', async () => {
      const instanceDir = path.join(paths.stateDir, 's1');
      await fs.mkdir(instanceDir, { recursive: true });
      await fs.writeFile(
        path.join(instanceDir, 'trade-log.json'),
        'bad json',
      );

      const log = await manager.readTradeLog('s1');
      expect(log).toEqual([]);
    });
  });
});
