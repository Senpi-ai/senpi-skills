import { Type } from '@sinclair/typebox';
import type { StateManager } from '../state-manager.js';
import type { DslStateSummary } from '../types.js';

export const getDslStateSchema = Type.Object({
  strategy_id: Type.Optional(
    Type.String({ description: 'Strategy ID (uses config default if omitted)' }),
  ),
  asset: Type.Optional(
    Type.String({
      description:
        'Asset symbol. If provided, returns full DSL state. If omitted, lists all active DSL assets with summary.',
    }),
  ),
});

export function createGetDslStateHandler(stateManager: StateManager) {
  return async function getDslStateExecute(
    _toolCallId: string,
    params: Record<string, unknown>,
  ) {
    const strategyId =
      (params.strategy_id as string | undefined) ??
      (await stateManager.getDefaultStrategyId());
    const asset = params.asset as string | undefined;

    if (asset) {
      const dslState = await stateManager.readDslState(strategyId, asset);
      if (!dslState) {
        return {
          content: [
            {
              type: 'text' as const,
              text: JSON.stringify({
                error: `No DSL state found for asset: ${asset}`,
              }),
            },
          ],
        };
      }
      return {
        content: [{ type: 'text' as const, text: JSON.stringify(dslState) }],
      };
    }

    // List mode: return summaries for all DSL assets
    const assets = await stateManager.listDslStates(strategyId);
    const summaries: DslStateSummary[] = [];

    for (const a of assets) {
      const state = await stateManager.readDslState(strategyId, a);
      if (state) {
        summaries.push({
          asset: state.asset,
          active: state.active,
          phase: state.phase,
          tierIndex: state.currentTierIndex,
          direction: state.direction,
          entryPrice: state.entryPrice,
          lastPrice: state.lastPrice,
          highWaterPrice: state.highWaterPrice,
        });
      }
    }

    return {
      content: [{ type: 'text' as const, text: JSON.stringify(summaries) }],
    };
  };
}
