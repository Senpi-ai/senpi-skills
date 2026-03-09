import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EntryHandler } from '../src/primitives/entry/entry-handler.js';
import {
  Signal,
  EmergingMoversSignalMetadata,
  ScanResult,
  EmergingMoversScanMetadata,
  StrategyConfig,
  EntryDecision,
  HookEvent,
} from '../src/core/types.js';

// ─── Helpers ───

function makeSignal(overrides: Partial<Signal<EmergingMoversSignalMetadata>> & {
  token?: string;
} = {}): Signal<EmergingMoversSignalMetadata> {
  return {
    scannerType: 'emerging_movers',
    token: overrides.token ?? 'BTC',
    dex: null,
    qualifiedAsset: overrides.token ?? 'BTC',
    direction: 'LONG',
    conviction: 0.9,
    signalType: 'FIRST_JUMP',
    signalPriority: 1,
    reasons: ['FIRST_JUMP from #30→#5'],
    metadata: {
      scannerType: 'emerging_movers',
      currentRank: 5,
      contribution: 8.0,
      contribVelocity: 0.5,
      traders: 20,
      priceChg4h: 3.0,
      maxLeverage: 50,
      rankHistory: [30, 20, 5],
      contribHistory: [1.0, 4.0, 8.0],
      isFirstJump: true,
      isContribExplosion: false,
      isImmediate: true,
      isDeepClimber: true,
      erratic: false,
      lowVelocity: false,
      rankJumpThisScan: 25,
    },
    ...overrides,
  };
}

function makeScanResult(
  signals: Signal[] = [makeSignal()],
  slots = 2,
): ScanResult<EmergingMoversScanMetadata> {
  return {
    scannerType: 'emerging_movers',
    signals,
    topPicks: signals,
    strategySlots: {
      alpha: {
        name: 'alpha',
        slots: 3,
        used: 1,
        available: slots,
        dslActive: 1,
        onChain: 1,
        onChainCoins: ['ETH'],
        slotAges: [],
        rotationEligibleCoins: [],
        hasRotationCandidate: false,
        gate: 'OPEN',
        gateReason: null,
      },
    },
    anySlotsAvailable: slots > 0,
    totalAvailableSlots: slots,
    scansInHistory: 5,
    metadata: {
      scannerType: 'emerging_movers',
      hasFirstJump: true,
      hasImmediate: true,
      hasContribExplosion: false,
    },
  };
}

function makeHookEvent(scanResult: ScanResult = makeScanResult()): HookEvent {
  return {
    type: 'on_signal_detected',
    skillName: 'wolf',
    data: scanResult as unknown as Record<string, unknown>,
    timestamp: new Date().toISOString(),
  };
}

const defaultStrategy: StrategyConfig = {
  wallet: '0xabc',
  strategyId: 'strat-1',
  budget: 1000,
  slots: 3,
  tradingRisk: 'aggressive',
  dslPreset: 'aggressive',
  marginPerSlot: 100,
  guardRails: { maxEntriesPerDay: 5 },
  _key: 'alpha',
};

function mockLlmDecision(decision: Partial<EntryDecision> = {}) {
  return {
    decide: vi.fn().mockResolvedValue({
      decision: {
        enter: true,
        target_strategy: 'alpha',
        direction: 'LONG' as const,
        confidence: 8,
        reasoning: 'Strong first jump signal',
        rotate_out: null,
        ...decision,
      },
      rawResponse: '{}',
      tokensUsed: 400,
      latencyMs: 500,
    }),
    decideWithRetry: vi.fn(),
  } as any;
}

function mockContextBuilder() {
  return {
    registerProvider: vi.fn(),
    build: vi.fn().mockReturnValue({ signal: {}, portfolio: {} }),
  } as any;
}

function mockHookSystem() {
  const handlers = new Map<string, Function[]>();
  return {
    on: vi.fn((eventType: string, handler: Function) => {
      const existing = handlers.get(eventType) ?? [];
      existing.push(handler);
      handlers.set(eventType, existing);
    }),
    fire: vi.fn().mockResolvedValue(undefined),
    registerSkillHooks: vi.fn(),
    _handlers: handlers,
  } as any;
}

function mockDeps(overrides: {
  decision?: Partial<EntryDecision>;
  llmError?: Error;
} = {}) {
  const hookSystem = mockHookSystem();
  const llm = overrides.llmError
    ? { decide: vi.fn().mockRejectedValue(overrides.llmError), decideWithRetry: vi.fn() } as any
    : mockLlmDecision(overrides.decision);
  const contextBuilder = mockContextBuilder();
  const notifications = { send: vi.fn().mockResolvedValue(undefined), sendAll: vi.fn() } as any;
  const stateManager = {
    read: vi.fn(),
    write: vi.fn(),
    listActiveDslStates: vi.fn().mockReturnValue([]),
    getDslState: vi.fn().mockReturnValue(null),
    setDslState: vi.fn(),
  } as any;
  const mcp = {
    createPosition: vi.fn().mockResolvedValue({ results: [{ status: 'ok' }] }),
    getClearinghouseState: vi.fn().mockResolvedValue({
      main: {
        marginSummary: { accountValue: '1000', totalMarginUsed: '100' },
        crossMaintenanceMarginUsed: 0,
        withdrawable: '900',
        assetPositions: [],
      },
      xyz: {
        marginSummary: { accountValue: '0', totalMarginUsed: '0' },
        crossMaintenanceMarginUsed: 0,
        withdrawable: '0',
        assetPositions: [],
      },
    }),
    closePosition: vi.fn(),
  } as any;
  const riskGuard = {
    checkGate: vi.fn().mockReturnValue({ gate: 'OPEN' }),
    recordEntry: vi.fn(),
  } as any;

  const strategies = new Map<string, StrategyConfig>();
  strategies.set('alpha', defaultStrategy);

  return {
    hookSystem,
    llm,
    contextBuilder,
    notifications,
    stateManager,
    mcp,
    riskGuard,
    strategies,
  };
}

// ─── Tests ───

describe('EntryHandler', () => {
  it('registers on on_signal_detected hook', () => {
    const deps = mockDeps();
    new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test prompt',
        context: ['signal', 'portfolio'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    expect(deps.hookSystem.on).toHaveBeenCalledWith(
      'on_signal_detected',
      expect.any(Function),
    );
  });

  it('calls LLM with correct prompt and context on signal', async () => {
    const deps = mockDeps();
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'Evaluate this signal',
        context: ['signal', 'portfolio'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    await handler.handleScanResult(makeHookEvent());

    expect(deps.contextBuilder.registerProvider).toHaveBeenCalledWith(
      'signal',
      expect.any(Function),
    );
    expect(deps.contextBuilder.build).toHaveBeenCalledWith(['signal', 'portfolio']);
    expect(deps.llm.decide).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: 'Evaluate this signal',
        context: { signal: {}, portfolio: {} },
      }),
    );
  });

  it('does not call LLM when no signals', async () => {
    const deps = mockDeps();
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    const emptyResult = makeScanResult([], 2);
    await handler.handleScanResult(makeHookEvent(emptyResult));

    expect(deps.llm.decide).not.toHaveBeenCalled();
  });

  it('does not call LLM when no slots available', async () => {
    const deps = mockDeps();
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    const noSlotsResult = makeScanResult([makeSignal()], 0);
    await handler.handleScanResult(makeHookEvent(noSlotsResult));

    expect(deps.llm.decide).not.toHaveBeenCalled();
  });

  it('does not open position when confidence below threshold', async () => {
    const deps = mockDeps({ decision: { confidence: 3 } });
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    await handler.handleScanResult(makeHookEvent());

    expect(deps.llm.decide).toHaveBeenCalled();
    expect(deps.mcp.createPosition).not.toHaveBeenCalled();
  });

  it('does not open position when LLM says do not enter', async () => {
    const deps = mockDeps({ decision: { enter: false, confidence: 8 } });
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    await handler.handleScanResult(makeHookEvent());

    expect(deps.mcp.createPosition).not.toHaveBeenCalled();
  });

  it('opens position and fires hook when LLM approves', async () => {
    const deps = mockDeps({ decision: { enter: true, confidence: 8 } });
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    await handler.handleScanResult(makeHookEvent());

    expect(deps.mcp.createPosition).toHaveBeenCalled();
    expect(deps.hookSystem.fire).toHaveBeenCalledWith(
      'wolf',
      expect.objectContaining({ type: 'on_position_opened' }),
    );
    expect(deps.notifications.send).toHaveBeenCalled();
  });

  it('handles LLM error gracefully', async () => {
    const deps = mockDeps({ llmError: new Error('LLM unavailable') });
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    // Should not throw
    await handler.handleScanResult(makeHookEvent());

    expect(deps.mcp.createPosition).not.toHaveBeenCalled();
  });

  it('skips when decision_mode is not llm', async () => {
    const deps = mockDeps();
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'none',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    await handler.handleScanResult(makeHookEvent());

    expect(deps.llm.decide).not.toHaveBeenCalled();
  });

  it('rejects when target strategy does not exist', async () => {
    const deps = mockDeps({ decision: { target_strategy: 'nonexistent' } });
    const handler = new EntryHandler({
      skillName: 'wolf',
      entryConfig: {
        decision_mode: 'llm',
        decision_prompt: 'test',
        context: ['signal'],
        min_confidence: 6,
      },
      strategies: deps.strategies,
      contextBuilder: deps.contextBuilder,
      llmDecision: deps.llm,
      stateManager: deps.stateManager,
      mcp: deps.mcp,
      riskGuard: deps.riskGuard,
      hookSystem: deps.hookSystem,
      notifications: deps.notifications,
    });

    await handler.handleScanResult(makeHookEvent());

    expect(deps.mcp.createPosition).not.toHaveBeenCalled();
  });
});
