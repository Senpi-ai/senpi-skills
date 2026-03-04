import { Type } from '@sinclair/typebox';
import type { StateManager } from '../state-manager.js';

export const deactivateDslSchema = Type.Object({
  asset: Type.String({ description: 'Asset symbol, e.g. "ETH"' }),
  reason: Type.String({
    description:
      'Deactivation reason, e.g. "OI_COLLAPSE", "MANUAL", "RISK_GUARDIAN"',
  }),
  strategy_id: Type.Optional(
    Type.String({ description: 'Strategy ID (uses config default if omitted)' }),
  ),
});

export function createDeactivateDslHandler(stateManager: StateManager) {
  return async function deactivateDslExecute(
    _toolCallId: string,
    params: Record<string, unknown>,
  ) {
    const strategyId =
      (params.strategy_id as string | undefined) ??
      (await stateManager.getDefaultStrategyId());
    const asset = params.asset as string;
    const reason = params.reason as string;

    const dslState = await stateManager.readDslState(strategyId, asset);

    if (!dslState) {
      return {
        content: [
          {
            type: 'text' as const,
            text: JSON.stringify({
              status: 'not_found',
              message: `No DSL state found for asset: ${asset}`,
            }),
          },
        ],
      };
    }

    if (!dslState.active) {
      return {
        content: [
          {
            type: 'text' as const,
            text: JSON.stringify({
              status: 'already_inactive',
              message: `DSL state for ${asset} is already inactive`,
              closedAt: dslState.closedAt ?? null,
              closeReason: dslState.closeReason ?? null,
            }),
          },
        ],
      };
    }

    const now = new Date().toISOString();
    dslState.active = false;
    dslState.closedAt = now;
    dslState.closeReason = reason;
    dslState.updatedAt = now;

    await stateManager.writeDslState(strategyId, asset, dslState);

    return {
      content: [{ type: 'text' as const, text: JSON.stringify(dslState) }],
    };
  };
}
