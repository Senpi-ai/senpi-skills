import { Type } from '@sinclair/typebox';
import type { StateManager } from '../state-manager.js';
import type { TigerPattern } from '../types.js';
import { buildDslState } from '../dsl-defaults.js';

const PATTERNS = [
  'COMPRESSION_BREAKOUT',
  'CORRELATION_LAG',
  'MOMENTUM_BREAKOUT',
  'MEAN_REVERSION',
  'FUNDING_ARB',
] as const;

export const createDslSchema = Type.Object({
  asset: Type.String({ description: 'Asset symbol, e.g. "ETH"' }),
  direction: Type.Union(
    [Type.Literal('LONG'), Type.Literal('SHORT')],
    { description: 'Position direction' },
  ),
  entry_price: Type.Number({ description: 'Entry price' }),
  size: Type.Number({ description: 'Position size in asset units' }),
  leverage: Type.Number({ description: 'Leverage multiplier' }),
  wallet: Type.String({ description: 'Strategy wallet address' }),
  pattern: Type.Union(
    PATTERNS.map((p) => Type.Literal(p)),
    { description: 'Signal pattern — determines DSL tuning parameters' },
  ),
  strategy_id: Type.Optional(
    Type.String({ description: 'Strategy ID (uses config default if omitted)' }),
  ),
  absolute_floor: Type.Optional(
    Type.Number({ description: 'Override auto-calculated absolute floor price' }),
  ),
});

export function createCreateDslHandler(stateManager: StateManager) {
  return async function createDslExecute(
    _toolCallId: string,
    params: Record<string, unknown>,
  ) {
    const strategyId =
      (params.strategy_id as string | undefined) ??
      (await stateManager.getDefaultStrategyId());
    const asset = params.asset as string;

    // Guard against duplicate DSL state
    const exists = await stateManager.dslStateExists(strategyId, asset);
    if (exists) {
      return {
        content: [
          {
            type: 'text' as const,
            text: JSON.stringify({
              error: `DSL state already exists for asset: ${asset}. Deactivate or remove the existing state first.`,
            }),
          },
        ],
      };
    }

    const dslState = buildDslState({
      asset,
      direction: params.direction as 'LONG' | 'SHORT',
      entryPrice: params.entry_price as number,
      size: params.size as number,
      leverage: params.leverage as number,
      wallet: params.wallet as string,
      pattern: params.pattern as TigerPattern,
      absoluteFloor: params.absolute_floor as number | undefined,
    });

    await stateManager.writeDslState(strategyId, asset, dslState);

    return {
      content: [{ type: 'text' as const, text: JSON.stringify(dslState) }],
    };
  };
}
