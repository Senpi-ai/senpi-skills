import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createGetDslStateHandler } from '../../tools/get-dsl-state.js';
import type { StateManager } from '../../state-manager.js';

describe('tiger_get_dsl_state', () => {
  let mockStateManager: {
    getDefaultStrategyId: ReturnType<typeof vi.fn>;
    readDslState: ReturnType<typeof vi.fn>;
    listDslStates: ReturnType<typeof vi.fn>;
  };
  let handler: ReturnType<typeof createGetDslStateHandler>;

  beforeEach(() => {
    mockStateManager = {
      getDefaultStrategyId: vi.fn().mockResolvedValue('default'),
      readDslState: vi.fn(),
      listDslStates: vi.fn().mockResolvedValue([]),
    };
    handler = createGetDslStateHandler(
      mockStateManager as unknown as StateManager,
    );
  });

  it('returns full DSL state when asset provided', async () => {
    mockStateManager.readDslState.mockResolvedValue({
      active: true,
      asset: 'ETH',
      direction: 'LONG',
      entryPrice: 3500,
      phase: 1,
      currentTierIndex: -1,
      highWaterPrice: 3550,
      lastPrice: 3540,
    });

    const result = await handler('call-1', {
      strategy_id: 's1',
      asset: 'ETH',
    });

    expect(mockStateManager.readDslState).toHaveBeenCalledWith('s1', 'ETH');
    const text = JSON.parse(result.content[0].text);
    expect(text.asset).toBe('ETH');
    expect(text.direction).toBe('LONG');
    expect(text.entryPrice).toBe(3500);
  });

  it('returns error when asset not found', async () => {
    mockStateManager.readDslState.mockResolvedValue(null);

    const result = await handler('call-2', {
      strategy_id: 's1',
      asset: 'NONEXIST',
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.error).toContain('No DSL state found');
  });

  it('returns summary list when asset omitted', async () => {
    mockStateManager.listDslStates.mockResolvedValue(['ETH', 'BTC']);
    mockStateManager.readDslState
      .mockResolvedValueOnce({
        asset: 'ETH',
        active: true,
        phase: 2,
        currentTierIndex: 1,
        direction: 'LONG',
        entryPrice: 3500,
        lastPrice: 3600,
        highWaterPrice: 3650,
      })
      .mockResolvedValueOnce({
        asset: 'BTC',
        active: true,
        phase: 1,
        currentTierIndex: -1,
        direction: 'SHORT',
        entryPrice: 65000,
        lastPrice: 64000,
        highWaterPrice: 64000,
      });

    const result = await handler('call-3', { strategy_id: 's1' });

    const text = JSON.parse(result.content[0].text);
    expect(text).toHaveLength(2);
    expect(text[0].asset).toBe('ETH');
    expect(text[0].phase).toBe(2);
    expect(text[1].asset).toBe('BTC');
    expect(text[1].direction).toBe('SHORT');
  });

  it('returns empty list when no DSL states exist', async () => {
    mockStateManager.listDslStates.mockResolvedValue([]);

    const result = await handler('call-4', { strategy_id: 's1' });

    const text = JSON.parse(result.content[0].text);
    expect(text).toEqual([]);
  });

  it('uses default strategy_id when not provided', async () => {
    mockStateManager.listDslStates.mockResolvedValue([]);

    await handler('call-5', {});

    expect(mockStateManager.getDefaultStrategyId).toHaveBeenCalled();
    expect(mockStateManager.listDslStates).toHaveBeenCalledWith('default');
  });

  it('skips null DSL states in list mode', async () => {
    mockStateManager.listDslStates.mockResolvedValue(['ETH', 'BAD']);
    mockStateManager.readDslState
      .mockResolvedValueOnce({
        asset: 'ETH',
        active: true,
        phase: 1,
        currentTierIndex: 0,
        direction: 'LONG',
        entryPrice: 3500,
        lastPrice: 3600,
        highWaterPrice: 3650,
      })
      .mockResolvedValueOnce(null); // BAD asset returns null

    const result = await handler('call-6', { strategy_id: 's1' });

    const text = JSON.parse(result.content[0].text);
    expect(text).toHaveLength(1);
    expect(text[0].asset).toBe('ETH');
  });
});
