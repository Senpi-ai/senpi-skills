import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createCreateDslHandler } from '../../tools/create-dsl.js';
import type { StateManager } from '../../state-manager.js';

describe('tiger_create_dsl', () => {
  let mockStateManager: {
    getDefaultStrategyId: ReturnType<typeof vi.fn>;
    dslStateExists: ReturnType<typeof vi.fn>;
    writeDslState: ReturnType<typeof vi.fn>;
  };
  let handler: ReturnType<typeof createCreateDslHandler>;

  beforeEach(() => {
    mockStateManager = {
      getDefaultStrategyId: vi.fn().mockResolvedValue('default'),
      dslStateExists: vi.fn().mockResolvedValue(false),
      writeDslState: vi.fn().mockResolvedValue(undefined),
    };
    handler = createCreateDslHandler(
      mockStateManager as unknown as StateManager,
    );
  });

  const baseParams = {
    asset: 'ETH',
    direction: 'LONG',
    entry_price: 3500,
    size: 1.5,
    leverage: 10,
    wallet: '0xabc',
    pattern: 'COMPRESSION_BREAKOUT',
  };

  it('creates DSL state and returns it', async () => {
    const result = await handler('call-1', { ...baseParams, strategy_id: 's1' });

    expect(mockStateManager.dslStateExists).toHaveBeenCalledWith('s1', 'ETH');
    expect(mockStateManager.writeDslState).toHaveBeenCalledWith(
      's1',
      'ETH',
      expect.objectContaining({
        active: true,
        asset: 'ETH',
        direction: 'LONG',
        entryPrice: 3500,
        size: 1.5,
        leverage: 10,
        wallet: '0xabc',
        phase: 1,
      }),
    );

    const text = JSON.parse(result.content[0].text);
    expect(text.active).toBe(true);
    expect(text.asset).toBe('ETH');
    expect(text.phase1.retraceThreshold).toBe(0.015);
  });

  it('returns error when DSL state already exists', async () => {
    mockStateManager.dslStateExists.mockResolvedValue(true);

    const result = await handler('call-2', baseParams);

    const text = JSON.parse(result.content[0].text);
    expect(text.error).toContain('already exists');
    expect(mockStateManager.writeDslState).not.toHaveBeenCalled();
  });

  it('uses default strategy ID when not provided', async () => {
    await handler('call-3', baseParams);

    expect(mockStateManager.getDefaultStrategyId).toHaveBeenCalled();
    expect(mockStateManager.dslStateExists).toHaveBeenCalledWith('default', 'ETH');
  });

  it('applies pattern-specific tuning for MOMENTUM_BREAKOUT', async () => {
    const result = await handler('call-4', {
      ...baseParams,
      pattern: 'MOMENTUM_BREAKOUT',
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.phase1.retraceThreshold).toBe(0.012);
    expect(text.phase2.retraceThreshold).toBe(0.010);
  });

  it('applies pattern-specific tuning for FUNDING_ARB', async () => {
    const result = await handler('call-5', {
      ...baseParams,
      pattern: 'FUNDING_ARB',
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.phase1.retraceThreshold).toBe(0.020);
    expect(text.phase2.retraceThreshold).toBe(0.015);
  });

  it('passes custom absolute_floor to buildDslState', async () => {
    const result = await handler('call-6', {
      ...baseParams,
      absolute_floor: 3400,
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.phase1.absoluteFloor).toBe(3400);
  });

  it('creates correct floor for SHORT direction', async () => {
    const result = await handler('call-7', {
      ...baseParams,
      direction: 'SHORT',
      entry_price: 1000,
    });

    const text = JSON.parse(result.content[0].text);
    expect(text.direction).toBe('SHORT');
    expect(text.phase1.absoluteFloor).toBe(1020); // 1000 * 1.02
  });
});
