import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createGetStateHandler } from '../../tools/get-state.js';
import type { StateManager } from '../../state-manager.js';

describe('tiger_get_state', () => {
  let mockStateManager: {
    getDefaultStrategyId: ReturnType<typeof vi.fn>;
    readState: ReturnType<typeof vi.fn>;
  };
  let handler: ReturnType<typeof createGetStateHandler>;

  beforeEach(() => {
    mockStateManager = {
      getDefaultStrategyId: vi.fn().mockResolvedValue('default'),
      readState: vi.fn().mockResolvedValue({
        version: 1,
        active: true,
        currentBalance: 1500,
        aggression: 'NORMAL',
        safety: { halted: false, haltReason: null, dailyLossPct: 0, tradesToday: 0 },
        activePositions: {},
      }),
    };
    handler = createGetStateHandler(mockStateManager as unknown as StateManager);
  });

  it('reads state with provided strategy_id', async () => {
    const result = await handler('call-1', { strategy_id: 'my-strat' });

    expect(mockStateManager.readState).toHaveBeenCalledWith('my-strat');
    const text = JSON.parse(result.content[0].text);
    expect(text.currentBalance).toBe(1500);
  });

  it('uses default strategy_id when not provided', async () => {
    await handler('call-2', {});

    expect(mockStateManager.getDefaultStrategyId).toHaveBeenCalled();
    expect(mockStateManager.readState).toHaveBeenCalledWith('default');
  });

  it('returns complete state as JSON', async () => {
    const result = await handler('call-3', { strategy_id: 's1' });

    const text = JSON.parse(result.content[0].text);
    expect(text.active).toBe(true);
    expect(text.aggression).toBe('NORMAL');
    expect(text.safety.halted).toBe(false);
  });
});
