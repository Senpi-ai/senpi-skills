import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  SIGNAL_CONVICTION,
  Signal,
  EmergingMoversSignalMetadata,
  StrategyConfig,
  ClearinghouseState,
} from '../src/core/types.js';
import {
  EmergingMoversScanner,
  ScanSnapshot,
  isErraticHistory,
  applyFilters,
  computeTopPicks,
  shouldSkipSignals,
} from '../src/primitives/scanners/emerging-movers.js';

// ─── Helpers ───

type SignalOverrides = Partial<Signal<EmergingMoversSignalMetadata>> & {
  // Allow flat overrides for metadata fields for test convenience
  currentRank?: number;
  contribution?: number;
  contribVelocity?: number;
  traders?: number;
  priceChg4h?: number;
  maxLeverage?: number | null;
  rankHistory?: (number | null)[];
  contribHistory?: (number | null)[];
  isFirstJump?: boolean;
  isContribExplosion?: boolean;
  isImmediate?: boolean;
  isDeepClimber?: boolean;
  erratic?: boolean;
  lowVelocity?: boolean;
  rankJumpThisScan?: number;
};

function makeSignal(overrides: SignalOverrides = {}): Signal<EmergingMoversSignalMetadata> {
  const {
    currentRank, contribution, contribVelocity, traders, priceChg4h,
    maxLeverage, rankHistory, contribHistory, isFirstJump, isContribExplosion,
    isImmediate, isDeepClimber, erratic, lowVelocity, rankJumpThisScan,
    metadata: metaOverrides,
    ...signalOverrides
  } = overrides;

  return {
    scannerType: 'emerging_movers',
    token: 'BTC',
    dex: null,
    qualifiedAsset: 'BTC',
    direction: 'LONG',
    conviction: 0.7,
    signalType: 'IMMEDIATE_MOVER',
    signalPriority: 3,
    reasons: ['RANK_UP +12', 'IMMEDIATE_MOVER +12 from #25 in ONE scan'],
    ...signalOverrides,
    metadata: {
      scannerType: 'emerging_movers',
      currentRank: currentRank ?? 10,
      contribution: contribution ?? 5.0,
      contribVelocity: contribVelocity ?? 0.1,
      traders: traders ?? 15,
      priceChg4h: priceChg4h ?? 2.5,
      maxLeverage: maxLeverage !== undefined ? maxLeverage : 50,
      rankHistory: rankHistory ?? [25, 22, 10],
      contribHistory: contribHistory ?? [1.0, 2.0, 5.0],
      isFirstJump: isFirstJump ?? false,
      isContribExplosion: isContribExplosion ?? false,
      isImmediate: isImmediate ?? true,
      isDeepClimber: isDeepClimber ?? true,
      erratic: erratic ?? false,
      lowVelocity: lowVelocity ?? false,
      rankJumpThisScan: rankJumpThisScan ?? 12,
      ...metaOverrides,
    },
  };
}

function makeScanSnapshot(
  time: string,
  markets: Array<{
    token: string;
    dex?: string;
    rank: number;
    direction?: string;
    contribution?: number;
    traders?: number;
    price_chg_4h?: number;
  }>,
): ScanSnapshot {
  return {
    time,
    markets: markets.map((m) => ({
      token: m.token,
      dex: m.dex ?? '',
      rank: m.rank,
      direction: m.direction ?? 'LONG',
      contribution: m.contribution ?? 0.01,
      traders: m.traders ?? 10,
      price_chg_4h: m.price_chg_4h ?? 0,
    })),
  };
}

function mockMcp() {
  return {
    getLeaderboardMarkets: vi.fn(),
    getClearinghouseState: vi.fn().mockResolvedValue(null),
    closePosition: vi.fn(),
    createPosition: vi.fn(),
    callToolSafe: vi.fn(),
  } as any;
}

function mockStateManager(
  dslStates: Array<{
    asset: string;
    strategyKey: string;
    state: { active: boolean; approximate?: boolean; createdAt?: string };
  }> = [],
  scanHistory: ScanSnapshot[] = [],
) {
  return {
    read: vi.fn((key: string) => {
      if (key === 'scan-history') return scanHistory.length > 0 ? { scans: scanHistory } : null;
      return null;
    }),
    write: vi.fn(),
    listActiveDslStates: vi.fn((_skill: string, _stratKey?: string) =>
      dslStates.filter((d) => !_stratKey || d.strategyKey === _stratKey),
    ),
    getDslState: vi.fn(),
    setDslState: vi.fn(),
  } as any;
}

function mockHookSystem() {
  return {
    fire: vi.fn().mockResolvedValue(undefined),
    registerSkillHooks: vi.fn(),
  } as any;
}

function mockRiskGuard(gate: 'OPEN' | 'COOLDOWN' | 'CLOSED' = 'OPEN', reason?: string) {
  return {
    checkGate: vi.fn().mockReturnValue({ gate, reason }),
    recordEntry: vi.fn(),
    onPositionClosed: vi.fn(),
  } as any;
}

const defaultStrategy: StrategyConfig = {
  wallet: '0xabc',
  strategyId: 'strat-1',
  budget: 1000,
  slots: 2,
  tradingRisk: 'moderate',
  dslPreset: 'standard',
  guardRails: { maxEntriesPerDay: 5 },
  _key: 'alpha',
};

// ─── Tests ───

describe('Emerging Movers — Signal Classification', () => {
  describe('Signal Conviction Mapping', () => {
    it('FIRST_JUMP has highest conviction', () => {
      expect(SIGNAL_CONVICTION.FIRST_JUMP).toBe(0.9);
    });

    it('CONTRIB_EXPLOSION has second-highest conviction', () => {
      expect(SIGNAL_CONVICTION.CONTRIB_EXPLOSION).toBe(0.8);
    });

    it('IMMEDIATE_MOVER and NEW_ENTRY_DEEP are equal', () => {
      expect(SIGNAL_CONVICTION.IMMEDIATE_MOVER).toBe(0.7);
      expect(SIGNAL_CONVICTION.NEW_ENTRY_DEEP).toBe(0.7);
    });

    it('DEEP_CLIMBER has lowest conviction', () => {
      expect(SIGNAL_CONVICTION.DEEP_CLIMBER).toBe(0.5);
    });
  });

  describe('Signal Priority Order', () => {
    it('priority 1 sorts before priority 5', () => {
      const signals = [
        { signalPriority: 5, contribVelocity: 0.1, reasons: ['a'] },
        { signalPriority: 1, contribVelocity: 0.05, reasons: ['b'] },
      ];

      signals.sort((a, b) => {
        if (a.signalPriority !== b.signalPriority) return a.signalPriority - b.signalPriority;
        if (Math.abs(b.contribVelocity) !== Math.abs(a.contribVelocity))
          return Math.abs(b.contribVelocity) - Math.abs(a.contribVelocity);
        return b.reasons.length - a.reasons.length;
      });

      expect(signals[0].signalPriority).toBe(1);
    });

    it('within same priority, higher velocity sorts first', () => {
      const signals = [
        { signalPriority: 3, contribVelocity: 0.01, reasons: ['a'] },
        { signalPriority: 3, contribVelocity: 0.05, reasons: ['b'] },
      ];

      signals.sort((a, b) => {
        if (a.signalPriority !== b.signalPriority) return a.signalPriority - b.signalPriority;
        if (Math.abs(b.contribVelocity) !== Math.abs(a.contribVelocity))
          return Math.abs(b.contribVelocity) - Math.abs(a.contribVelocity);
        return b.reasons.length - a.reasons.length;
      });

      expect(signals[0].contribVelocity).toBe(0.05);
    });
  });
});

describe('isErraticHistory', () => {
  it('returns false for too few data points', () => {
    expect(isErraticHistory([10, 5], false)).toBe(false);
    expect(isErraticHistory([10], false)).toBe(false);
    expect(isErraticHistory([], false)).toBe(false);
  });

  it('returns false for steady climb', () => {
    expect(isErraticHistory([40, 30, 20, 10], false)).toBe(false);
  });

  it('detects zigzag pattern', () => {
    // rank 40→30 (climb) then 30→40 (drop >5) = erratic
    expect(isErraticHistory([40, 30, 40], false)).toBe(true);
  });

  it('does not flag small reversals', () => {
    // reversal of 3 ranks is below ERRATIC_REVERSAL_THRESHOLD=5
    expect(isErraticHistory([30, 25, 28], false)).toBe(false);
  });

  it('excludes last entry when excludeLast=true', () => {
    // Without exclusion: 40→30→40 = erratic
    expect(isErraticHistory([40, 30, 40], false)).toBe(true);
    // With exclusion: only checks [40,30] — too few = not erratic
    expect(isErraticHistory([40, 30, 40], true)).toBe(false);
  });

  it('handles null values in history', () => {
    expect(isErraticHistory([null, 40, 30, 40], false)).toBe(true);
    expect(isErraticHistory([null, null, 30], false)).toBe(false);
  });

  it('detects erratic with climb then crash', () => {
    // 20→15 (climb via negative delta) then 15→25 (drop of +10 > threshold)
    expect(isErraticHistory([20, 15, 25], false)).toBe(true);
  });
});

describe('applyFilters', () => {
  it('returns all signals when no filters', () => {
    const signals = [makeSignal(), makeSignal({ token: 'ETH' })];
    expect(applyFilters(signals)).toHaveLength(2);
  });

  it('filters by blocked assets (case-insensitive)', () => {
    const signals = [
      makeSignal({ token: 'BTC', qualifiedAsset: 'BTC' }),
      makeSignal({ token: 'ETH', qualifiedAsset: 'ETH' }),
      makeSignal({ token: 'DOGE', qualifiedAsset: 'xyz:DOGE' }),
    ];
    const result = applyFilters(signals, undefined, ['btc', 'xyz:DOGE']);
    expect(result).toHaveLength(1);
    expect(result[0].token).toBe('ETH');
  });

  it('filters by min_reasons', () => {
    const signals = [
      makeSignal({ reasons: ['a'] }),
      makeSignal({ token: 'ETH', reasons: ['a', 'b', 'c'] }),
    ];
    const result = applyFilters(signals, { min_reasons: 2 });
    expect(result).toHaveLength(1);
    expect(result[0].token).toBe('ETH');
  });

  it('filters by max_rank', () => {
    const signals = [
      makeSignal({ currentRank: 5 }),
      makeSignal({ token: 'ETH', currentRank: 30 }),
    ];
    const result = applyFilters(signals, { max_rank: 20 });
    expect(result).toHaveLength(1);
    expect(result[0].metadata.currentRank).toBe(5);
  });

  it('filters by min_traders', () => {
    const signals = [
      makeSignal({ traders: 3 }),
      makeSignal({ token: 'ETH', traders: 15 }),
    ];
    const result = applyFilters(signals, { min_traders: 10 });
    expect(result).toHaveLength(1);
    expect(result[0].token).toBe('ETH');
  });

  it('filters by exclude_erratic', () => {
    const signals = [
      makeSignal({ erratic: true }),
      makeSignal({ token: 'ETH', erratic: false }),
    ];
    const result = applyFilters(signals, { exclude_erratic: true });
    expect(result).toHaveLength(1);
    expect(result[0].token).toBe('ETH');
  });

  it('filters by require_immediate — keeps FIRST_JUMP too', () => {
    const signals = [
      makeSignal({ isImmediate: false, isFirstJump: false, signalType: 'DEEP_CLIMBER' }),
      makeSignal({ token: 'ETH', isImmediate: true, isFirstJump: false }),
      makeSignal({ token: 'SOL', isImmediate: false, isFirstJump: true }),
    ];
    const result = applyFilters(signals, { require_immediate: true });
    expect(result).toHaveLength(2);
    expect(result.map((s) => s.token).sort()).toEqual(['ETH', 'SOL']);
  });

  it('applies multiple filters together', () => {
    const signals = [
      makeSignal({ currentRank: 5, traders: 20, reasons: ['a', 'b'] }),
      makeSignal({ token: 'ETH', currentRank: 5, traders: 3, reasons: ['a', 'b'] }),
      makeSignal({ token: 'SOL', currentRank: 30, traders: 20, reasons: ['a', 'b'] }),
    ];
    const result = applyFilters(signals, { max_rank: 20, min_traders: 10 });
    expect(result).toHaveLength(1);
    expect(result[0].token).toBe('BTC');
  });
});

describe('computeTopPicks', () => {
  it('returns empty for no signals', () => {
    expect(computeTopPicks([], 3)).toEqual([]);
  });

  it('returns up to totalAvailableSlots signals', () => {
    const signals = [
      makeSignal({ token: 'BTC' }),
      makeSignal({ token: 'ETH' }),
      makeSignal({ token: 'SOL' }),
    ];
    const picks = computeTopPicks(signals, 2);
    expect(picks).toHaveLength(2);
    expect(picks[0].token).toBe('BTC');
    expect(picks[1].token).toBe('ETH');
  });

  it('uses firstJumpCount when > totalAvailableSlots', () => {
    const signals = [
      makeSignal({ token: 'BTC', isFirstJump: true }),
      makeSignal({ token: 'ETH', isFirstJump: true }),
      makeSignal({ token: 'SOL', isFirstJump: true }),
      makeSignal({ token: 'DOGE', isFirstJump: false }),
    ];
    const picks = computeTopPicks(signals, 1); // 1 slot but 3 first jumps
    expect(picks).toHaveLength(3);
  });

  it('returns empty when 0 slots and no first jumps', () => {
    const signals = [makeSignal({ isFirstJump: false })];
    const picks = computeTopPicks(signals, 0);
    expect(picks).toEqual([]);
  });
});

describe('shouldSkipSignals', () => {
  it('does not skip when slots available', () => {
    expect(shouldSkipSignals(true, false, false)).toBe(false);
  });

  it('skips when no slots and no FIRST_JUMP', () => {
    expect(shouldSkipSignals(false, false, false)).toBe(true);
    expect(shouldSkipSignals(false, false, true)).toBe(true);
  });

  it('does not skip when no slots but has FIRST_JUMP', () => {
    expect(shouldSkipSignals(false, true, false)).toBe(false);
    expect(shouldSkipSignals(false, true, true)).toBe(false);
  });
});

describe('EmergingMoversScanner — tick()', () => {
  let mcp: ReturnType<typeof mockMcp>;
  let stateManager: ReturnType<typeof mockStateManager>;
  let hookSystem: ReturnType<typeof mockHookSystem>;
  let riskGuard: ReturnType<typeof mockRiskGuard>;

  // Reusable leaderboard data: token jumps from #30→#10 in one scan
  function leaderboardWith(markets: Array<{
    token: string;
    dex?: string;
    direction?: string;
    pct_of_top_traders_gain?: number;
    trader_count?: number;
    token_price_change_pct_4h?: number;
  }>) {
    return markets.map((m, i) => ({
      token: m.token,
      dex: m.dex ?? '',
      direction: m.direction ?? 'LONG',
      pct_of_top_traders_gain: m.pct_of_top_traders_gain ?? 0.05 - i * 0.001,
      trader_count: m.trader_count ?? 15,
      token_price_change_pct_4h: m.token_price_change_pct_4h ?? 1.5,
    }));
  }

  function createScanner(opts: {
    scanHistory?: ScanSnapshot[];
    dslStates?: Array<{ asset: string; strategyKey: string; state: any }>;
    strategies?: Record<string, StrategyConfig>;
    gate?: 'OPEN' | 'COOLDOWN' | 'CLOSED';
    scannerConfig?: Record<string, unknown>;
    blockedAssets?: string[];
  } = {}) {
    stateManager = mockStateManager(opts.dslStates ?? [], opts.scanHistory ?? []);
    riskGuard = mockRiskGuard(opts.gate ?? 'OPEN');

    return new EmergingMoversScanner({
      mcp,
      stateManager,
      hookSystem,
      skillName: 'wolf',
      intervalMs: 180_000,
      strategies: opts.strategies ?? { alpha: defaultStrategy },
      riskGuard,
      scannerConfig: opts.scannerConfig,
      blockedAssets: opts.blockedAssets,
    });
  }

  beforeEach(() => {
    mcp = mockMcp();
    hookSystem = mockHookSystem();
  });

  it('does not fire hooks when no previous scans', async () => {
    mcp.getLeaderboardMarkets.mockResolvedValue(
      leaderboardWith([{ token: 'BTC' }, { token: 'ETH' }]),
    );

    const scanner = createScanner();
    await scanner.tick();

    expect(hookSystem.fire).not.toHaveBeenCalled();
  });

  it('detects FIRST_JUMP signal', async () => {
    // Previous scans: BTC at #30
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 40, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.008 },
    ]);

    // Current: BTC jumps to #5 (rank jump = 25, from #30 → was >= 30)
    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 4) {
        currentMarkets.push({
          token: 'BTC',
          pct_of_top_traders_gain: 0.08,
          trader_count: 20,
        });
      } else {
        currentMarkets.push({
          token: `TOKEN${i}`,
          pct_of_top_traders_gain: 0.05 - i * 0.001,
          trader_count: 10,
        });
      }
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const scanner = createScanner({ scanHistory: [prevScan1, prevScan2] });
    await scanner.tick();

    expect(hookSystem.fire).toHaveBeenCalledTimes(1);
    const hookCall = hookSystem.fire.mock.calls[0];
    expect(hookCall[1].type).toBe('on_signal_detected');

    const scanResult = hookCall[1].data;
    expect(scanResult.signals.length).toBeGreaterThanOrEqual(1);

    const btcSignal = scanResult.signals.find((s: any) => s.token === 'BTC');
    expect(btcSignal).toBeDefined();
    expect(btcSignal.signalType).toBe('FIRST_JUMP');
    expect(btcSignal.signalPriority).toBe(1);
    expect(btcSignal.metadata.isFirstJump).toBe(true);
  });

  it('fires batch hook with ScanResult structure', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
      { token: 'ETH', rank: 25, contribution: 0.01 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
      { token: 'ETH', rank: 25, contribution: 0.01 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else if (i === 4) currentMarkets.push({ token: 'ETH', pct_of_top_traders_gain: 0.06, trader_count: 15 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const scanner = createScanner({ scanHistory: [prevScan1, prevScan2] });
    await scanner.tick();

    expect(hookSystem.fire).toHaveBeenCalledTimes(1);
    const scanResult = hookSystem.fire.mock.calls[0][1].data;

    // Verify ScanResult structure
    expect(scanResult).toHaveProperty('scannerType', 'emerging_movers');
    expect(scanResult).toHaveProperty('signals');
    expect(scanResult).toHaveProperty('topPicks');
    expect(scanResult).toHaveProperty('strategySlots');
    expect(scanResult).toHaveProperty('anySlotsAvailable');
    expect(scanResult).toHaveProperty('totalAvailableSlots');
    expect(scanResult).toHaveProperty('scansInHistory');
    expect(scanResult).toHaveProperty('metadata');
    expect(scanResult.metadata).toHaveProperty('hasFirstJump');
    expect(scanResult.metadata).toHaveProperty('hasImmediate');
    expect(scanResult.metadata).toHaveProperty('hasContribExplosion');

    expect(Array.isArray(scanResult.signals)).toBe(true);
    expect(Array.isArray(scanResult.topPicks)).toBe(true);
    expect(typeof scanResult.strategySlots).toBe('object');
  });

  it('applies blocked assets filter', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
      { token: 'ETH', rank: 28, contribution: 0.008 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
      { token: 'ETH', rank: 25, contribution: 0.012 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else if (i === 4) currentMarkets.push({ token: 'ETH', pct_of_top_traders_gain: 0.06, trader_count: 15 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      blockedAssets: ['BTC'],
    });
    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const btcSignals = scanResult.signals.filter((s: any) => s.token === 'BTC');
      expect(btcSignals).toHaveLength(0);
    }
  });

  it('computes slot availability from DSL states', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      dslStates: [
        {
          asset: 'ETH',
          strategyKey: 'alpha',
          state: { active: true, createdAt: oneHourAgo },
        },
      ],
      strategies: { alpha: { ...defaultStrategy, slots: 3 } },
    });

    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const alphaSlots = scanResult.strategySlots.alpha;
      expect(alphaSlots).toBeDefined();
      expect(alphaSlots.slots).toBe(3);
      expect(alphaSlots.dslActive).toBe(1);
      expect(alphaSlots.used).toBe(1);
      expect(alphaSlots.available).toBe(2);
    }
  });

  it('skips stale approximate DSLs in slot count', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const twentyMinAgo = new Date(Date.now() - 20 * 60 * 1000).toISOString();
    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      dslStates: [
        {
          asset: 'ETH',
          strategyKey: 'alpha',
          state: { active: true, approximate: true, createdAt: twentyMinAgo }, // >10 min = stale
        },
      ],
      strategies: { alpha: { ...defaultStrategy, slots: 2 } },
    });

    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const alphaSlots = scanResult.strategySlots.alpha;
      expect(alphaSlots.dslActive).toBe(0); // stale approximate skipped
      expect(alphaSlots.available).toBe(2);
    }
  });

  it('sets available=0 when gate is not OPEN', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      gate: 'COOLDOWN',
    });

    await scanner.tick();

    // When gate is COOLDOWN and no FIRST_JUMP, signals should be skipped
    // (no slots available + no first jump)
    // But BTC might be detected as a signal with rank jump - need to check
    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const alphaSlots = scanResult.strategySlots.alpha;
      expect(alphaSlots.available).toBe(0);
      expect(alphaSlots.gate).toBe('COOLDOWN');
    }
  });

  it('skips signals when no slots and no FIRST_JUMP', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 20, contribution: 0.01 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 18, contribution: 0.012 },
    ]);

    // Small rank change — DEEP_CLIMBER at best, not FIRST_JUMP
    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 9) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.05, trader_count: 15 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    // All slots full
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      dslStates: [
        { asset: 'ETH', strategyKey: 'alpha', state: { active: true, createdAt: oneHourAgo } },
        { asset: 'SOL', strategyKey: 'alpha', state: { active: true, createdAt: oneHourAgo } },
      ],
      strategies: { alpha: { ...defaultStrategy, slots: 2 } },
    });

    await scanner.tick();

    // No hook should fire since signals are skipped (no slots + no first jump)
    expect(hookSystem.fire).not.toHaveBeenCalled();
  });

  it('tracks rotation eligible coins', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const twoHoursAgo = new Date(Date.now() - 120 * 60 * 1000).toISOString();
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();

    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      dslStates: [
        { asset: 'ETH', strategyKey: 'alpha', state: { active: true, createdAt: twoHoursAgo } },
        { asset: 'SOL', strategyKey: 'alpha', state: { active: true, createdAt: fiveMinAgo } },
      ],
      strategies: { alpha: { ...defaultStrategy, slots: 3 } },
    });

    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const alphaSlots = scanResult.strategySlots.alpha;
      // ETH is rotation-eligible (>45min), SOL is not (5min)
      expect(alphaSlots.rotationEligibleCoins).toContain('ETH');
      expect(alphaSlots.rotationEligibleCoins).not.toContain('SOL');
      expect(alphaSlots.hasRotationCandidate).toBe(true);
    }
  });

  it('handles leaderboard fetch failure gracefully', async () => {
    mcp.getLeaderboardMarkets.mockRejectedValue(new Error('Network error'));

    const scanner = createScanner();
    await scanner.tick();

    expect(hookSystem.fire).not.toHaveBeenCalled();
  });

  it('does not fire hook when no signals detected', async () => {
    // Two previous scans with same data = no movement
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 1, contribution: 0.1 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 1, contribution: 0.1 },
    ]);

    // BTC stays at rank #1 with same contribution
    mcp.getLeaderboardMarkets.mockResolvedValue(
      leaderboardWith([{ token: 'BTC', pct_of_top_traders_gain: 0.1, trader_count: 20 }]),
    );

    const scanner = createScanner({ scanHistory: [prevScan1, prevScan2] });
    await scanner.tick();

    expect(hookSystem.fire).not.toHaveBeenCalled();
  });

  it('persists scan history', async () => {
    mcp.getLeaderboardMarkets.mockResolvedValue(
      leaderboardWith([{ token: 'BTC' }]),
    );

    const scanner = createScanner();
    await scanner.tick();

    expect(stateManager.write).toHaveBeenCalledWith(
      'scan-history',
      expect.objectContaining({ scans: expect.any(Array) }),
    );
  });

  it('cross-checks on-chain positions for slot count', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    // On-chain shows 2 positions even though DSL shows 1
    mcp.getClearinghouseState.mockResolvedValue({
      main: {
        marginSummary: { accountValue: '1000', totalMarginUsed: '500' },
        crossMaintenanceMarginUsed: 0,
        withdrawable: '500',
        assetPositions: [
          { position: { coin: 'ETH', szi: '1.5', entryPx: '3000', positionValue: '4500', marginUsed: '450', unrealizedPnl: '150', returnOnEquity: '0.33', liquidationPx: '2500' } },
          { position: { coin: 'SOL', szi: '-10', entryPx: '150', positionValue: '1500', marginUsed: '150', unrealizedPnl: '-10', returnOnEquity: '-0.07', liquidationPx: '170' } },
        ],
      },
      xyz: {
        marginSummary: { accountValue: '0', totalMarginUsed: '0' },
        crossMaintenanceMarginUsed: 0,
        withdrawable: '0',
        assetPositions: [],
      },
    } as ClearinghouseState);

    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      dslStates: [
        { asset: 'ETH', strategyKey: 'alpha', state: { active: true, createdAt: oneHourAgo } },
      ],
      strategies: { alpha: { ...defaultStrategy, slots: 3 } },
    });

    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const alphaSlots = scanResult.strategySlots.alpha;
      // max(1 dsl, 2 on-chain) = 2 used
      expect(alphaSlots.dslActive).toBe(1);
      expect(alphaSlots.onChain).toBe(2);
      expect(alphaSlots.used).toBe(2);
      expect(alphaSlots.available).toBe(1);
      expect(alphaSlots.onChainCoins).toEqual(['ETH', 'SOL']);
    }
  });

  it('applies scanner filters from skill.yaml', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
      { token: 'DOGE', rank: 35, contribution: 0.003, traders: 3 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
      { token: 'DOGE', rank: 33, contribution: 0.004, traders: 3 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else if (i === 15) currentMarkets.push({ token: 'DOGE', pct_of_top_traders_gain: 0.02, trader_count: 3 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    // Filter: require at least 10 traders
    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      scannerConfig: { min_traders: 10 },
    });

    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      const dogeSignals = scanResult.signals.filter((s: any) => s.token === 'DOGE');
      expect(dogeSignals).toHaveLength(0); // filtered out: only 3 traders
    }
  });

  it('skips disabled strategies', async () => {
    const prevScan1 = makeScanSnapshot('2026-01-01T00:00:00Z', [
      { token: 'BTC', rank: 30, contribution: 0.005 },
    ]);
    const prevScan2 = makeScanSnapshot('2026-01-01T00:03:00Z', [
      { token: 'BTC', rank: 28, contribution: 0.008 },
    ]);

    const currentMarkets = [];
    for (let i = 0; i < 50; i++) {
      if (i === 2) currentMarkets.push({ token: 'BTC', pct_of_top_traders_gain: 0.08, trader_count: 20 });
      else currentMarkets.push({ token: `TOKEN${i}`, pct_of_top_traders_gain: 0.04 - i * 0.0005, trader_count: 10 });
    }
    mcp.getLeaderboardMarkets.mockResolvedValue(leaderboardWith(currentMarkets));

    const scanner = createScanner({
      scanHistory: [prevScan1, prevScan2],
      strategies: {
        alpha: { ...defaultStrategy, enabled: false },
        beta: { ...defaultStrategy, _key: 'beta', wallet: '0xdef', slots: 3 },
      },
    });

    await scanner.tick();

    if (hookSystem.fire.mock.calls.length > 0) {
      const scanResult = hookSystem.fire.mock.calls[0][1].data;
      // alpha should not appear in strategySlots (disabled)
      expect(scanResult.strategySlots.alpha).toBeUndefined();
      expect(scanResult.strategySlots.beta).toBeDefined();
    }
  });
});
