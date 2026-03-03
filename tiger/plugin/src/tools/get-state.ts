import { Type } from '@sinclair/typebox';
import type { StateManager } from '../state-manager.js';

export const getStateSchema = Type.Object({
  strategy_id: Type.Optional(
    Type.String({ description: 'Strategy ID (uses config default if omitted)' }),
  ),
});

export function createGetStateHandler(stateManager: StateManager) {
  return async function getStateExecute(
    _toolCallId: string,
    params: Record<string, unknown>,
  ) {
    const strategyId =
      (params.strategy_id as string | undefined) ??
      (await stateManager.getDefaultStrategyId());

    const state = await stateManager.readState(strategyId);

    return {
      content: [{ type: 'text' as const, text: JSON.stringify(state) }],
    };
  };
}
