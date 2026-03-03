import { Type } from '@sinclair/typebox';
import type { StateManager } from '../state-manager.js';

export const getTradeLogSchema = Type.Object({
  strategy_id: Type.Optional(
    Type.String({ description: 'Strategy ID (uses config default if omitted)' }),
  ),
  limit: Type.Optional(
    Type.Number({
      description: 'Maximum number of entries to return (default: 20)',
      minimum: 1,
      maximum: 500,
    }),
  ),
});

export function createGetTradeLogHandler(stateManager: StateManager) {
  return async function getTradeLogExecute(
    _toolCallId: string,
    params: Record<string, unknown>,
  ) {
    const strategyId =
      (params.strategy_id as string | undefined) ??
      (await stateManager.getDefaultStrategyId());
    const limit = (params.limit as number | undefined) ?? 20;

    const tradeLog = await stateManager.readTradeLog(strategyId);
    const recent = tradeLog.slice(-limit);

    return {
      content: [{ type: 'text' as const, text: JSON.stringify(recent) }],
    };
  };
}
