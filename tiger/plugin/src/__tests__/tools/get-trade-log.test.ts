import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createGetTradeLogHandler } from '../../tools/get-trade-log.js';
import type { StateManager } from '../../state-manager.js';

describe('tiger_get_trade_log', () => {
  let mockStateManager: {
    getDefaultStrategyId: ReturnType<typeof vi.fn>;
    readTradeLog: ReturnType<typeof vi.fn>;
  };
  let handler: ReturnType<typeof createGetTradeLogHandler>;

  const sampleEntries = Array.from({ length: 30 }, (_, i) => ({
    version: 1,
    timestamp: `2025-01-${String(i + 1).padStart(2, '0')}T00:00:00Z`,
    asset: i % 2 === 0 ? 'ETH' : 'BTC',
    pattern: 'COMPRESSION_BREAKOUT',
    direction: 'LONG' as const,
    entryPrice: 3500 + i * 10,
    exitPrice: 3600 + i * 10,
    leverage: 10,
    sizeUsd: 300,
    pnlUsd: 100 + i,
    feesUsd: 5,
    holdMinutes: 180,
    exitReason: 'DSL_TIER_2',
    confluenceScore: 0.65,
    aggression: 'NORMAL' as const,
  }));

  beforeEach(() => {
    mockStateManager = {
      getDefaultStrategyId: vi.fn().mockResolvedValue('default'),
      readTradeLog: vi.fn().mockResolvedValue(sampleEntries),
    };
    handler = createGetTradeLogHandler(
      mockStateManager as unknown as StateManager,
    );
  });

  it('returns last 20 entries by default', async () => {
    const result = await handler('call-1', { strategy_id: 's1' });

    const text = JSON.parse(result.content[0].text);
    expect(text).toHaveLength(20);
    // Should be the last 20 (indices 10-29)
    expect(text[0].pnlUsd).toBe(110);
    expect(text[19].pnlUsd).toBe(129);
  });

  it('respects custom limit', async () => {
    const result = await handler('call-2', { strategy_id: 's1', limit: 5 });

    const text = JSON.parse(result.content[0].text);
    expect(text).toHaveLength(5);
    // Last 5 entries (indices 25-29)
    expect(text[0].pnlUsd).toBe(125);
  });

  it('returns all entries when limit exceeds log size', async () => {
    const result = await handler('call-3', { strategy_id: 's1', limit: 100 });

    const text = JSON.parse(result.content[0].text);
    expect(text).toHaveLength(30);
  });

  it('returns empty array for empty log', async () => {
    mockStateManager.readTradeLog.mockResolvedValue([]);

    const result = await handler('call-4', { strategy_id: 's1' });

    const text = JSON.parse(result.content[0].text);
    expect(text).toEqual([]);
  });

  it('uses default strategy_id when not provided', async () => {
    await handler('call-5', {});

    expect(mockStateManager.getDefaultStrategyId).toHaveBeenCalled();
    expect(mockStateManager.readTradeLog).toHaveBeenCalledWith('default');
  });
});
