import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createDeactivateDslHandler } from '../../tools/deactivate-dsl.js';
import type { StateManager } from '../../state-manager.js';
import type { DslState } from '../../types.js';

function makeDslState(overrides?: Partial<DslState>): DslState {
  return {
    active: true,
    asset: 'ETH',
    direction: 'LONG',
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
    tiers: [
      { triggerPct: 0.05, lockPct: 0.20, retrace: 0.015 },
      { triggerPct: 0.10, lockPct: 0.50, retrace: 0.012 },
      { triggerPct: 0.20, lockPct: 0.70, retrace: 0.010 },
      { triggerPct: 0.35, lockPct: 0.80, retrace: 0.008 },
    ],
    breachDecay: 'soft',
    createdAt: '2025-01-01T00:00:00.000Z',
    updatedAt: '2025-01-01T01:00:00.000Z',
    lastCheck: '2025-01-01T01:00:00.000Z',
    lastPrice: 3580,
    consecutiveFetchFailures: 0,
    ...overrides,
  };
}

describe('tiger_deactivate_dsl', () => {
  let mockStateManager: {
    getDefaultStrategyId: ReturnType<typeof vi.fn>;
    readDslState: ReturnType<typeof vi.fn>;
    writeDslState: ReturnType<typeof vi.fn>;
  };
  let handler: ReturnType<typeof createDeactivateDslHandler>;

  beforeEach(() => {
    mockStateManager = {
      getDefaultStrategyId: vi.fn().mockResolvedValue('default'),
      readDslState: vi.fn(),
      writeDslState: vi.fn().mockResolvedValue(undefined),
    };
    handler = createDeactivateDslHandler(
      mockStateManager as unknown as StateManager,
    );
  });

  it('deactivates active DSL state', async () => {
    mockStateManager.readDslState.mockResolvedValue(makeDslState());

    const result = await handler('call-1', {
      strategy_id: 's1',
      asset: 'ETH',
      reason: 'OI_COLLAPSE',
    });

    expect(mockStateManager.readDslState).toHaveBeenCalledWith('s1', 'ETH');
    expect(mockStateManager.writeDslState).toHaveBeenCalledWith(
      's1',
      'ETH',
      expect.objectContaining({
        active: false,
        closeReason: 'OI_COLLAPSE',
      }),
    );

    const text = JSON.parse(result.content[0].text);
    expect(text.active).toBe(false);
    expect(text.closeReason).toBe('OI_COLLAPSE');
    expect(text.closedAt).toBeTruthy();
  });

  it('returns not_found when DSL state missing', async () => {
    mockStateManager.readDslState.mockResolvedValue(null);

    const result = await handler('call-2', {
      strategy_id: 's1',
      asset: 'NONEXIST',
      reason: 'MANUAL',
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.status).toBe('not_found');
    expect(mockStateManager.writeDslState).not.toHaveBeenCalled();
  });

  it('returns already_inactive when DSL state already inactive (idempotent)', async () => {
    mockStateManager.readDslState.mockResolvedValue(
      makeDslState({
        active: false,
        closedAt: '2025-01-01T02:00:00.000Z',
        closeReason: 'PREVIOUS_REASON',
      }),
    );

    const result = await handler('call-3', {
      strategy_id: 's1',
      asset: 'ETH',
      reason: 'MANUAL',
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.status).toBe('already_inactive');
    expect(text.closeReason).toBe('PREVIOUS_REASON');
    expect(mockStateManager.writeDslState).not.toHaveBeenCalled();
  });

  it('records reason correctly', async () => {
    mockStateManager.readDslState.mockResolvedValue(makeDslState());

    await handler('call-4', {
      asset: 'ETH',
      reason: 'RISK_GUARDIAN',
    });

    const writtenState = mockStateManager.writeDslState.mock.calls[0][2];
    expect(writtenState.closeReason).toBe('RISK_GUARDIAN');
    expect(writtenState.active).toBe(false);
  });

  it('uses default strategy ID when not provided', async () => {
    mockStateManager.readDslState.mockResolvedValue(makeDslState());

    await handler('call-5', { asset: 'ETH', reason: 'MANUAL' });

    expect(mockStateManager.getDefaultStrategyId).toHaveBeenCalled();
    expect(mockStateManager.readDslState).toHaveBeenCalledWith('default', 'ETH');
  });

  it('updates updatedAt timestamp', async () => {
    mockStateManager.readDslState.mockResolvedValue(makeDslState());

    const before = new Date().toISOString();
    await handler('call-6', { asset: 'ETH', reason: 'MANUAL' });

    const writtenState = mockStateManager.writeDslState.mock.calls[0][2];
    expect(writtenState.updatedAt >= before).toBe(true);
  });
});
