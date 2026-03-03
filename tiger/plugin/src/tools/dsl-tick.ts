import * as path from 'node:path';
import { Type } from '@sinclair/typebox';
import type { PythonBridge } from '../python-bridge.js';
import type { StateManager } from '../state-manager.js';
import type { TigerPaths, DslTickResult } from '../types.js';

export const dslTickSchema = Type.Object({
  mode: Type.Optional(
    Type.Unsafe<'combined' | 'single'>({
      type: 'string',
      enum: ['combined', 'single'],
      description: 'combined processes all active DSL states; single processes one asset',
    }),
  ),
  asset: Type.Optional(
    Type.String({ description: 'Asset symbol (required when mode is single)' }),
  ),
  strategy_id: Type.Optional(
    Type.String({ description: 'Strategy ID (uses config default if omitted)' }),
  ),
});

export function createDslTickHandler(
  bridge: PythonBridge,
  stateManager: StateManager,
  paths: TigerPaths,
) {
  return async function dslTickExecute(
    _toolCallId: string,
    params: Record<string, unknown>,
  ) {
    const mode = (params.mode as string | undefined) ?? 'combined';
    const asset = params.asset as string | undefined;
    const strategyId =
      (params.strategy_id as string | undefined) ??
      (await stateManager.getDefaultStrategyId());

    if (mode === 'single' && !asset) {
      return {
        content: [
          {
            type: 'text' as const,
            text: JSON.stringify({
              success: false,
              error: 'asset is required when mode is single',
            }),
          },
        ],
      };
    }

    const env: Record<string, string> = {};
    if (mode === 'single' && asset) {
      const instanceDir = path.join(paths.stateDir, strategyId);
      env['DSL_STATE_FILE'] = path.join(instanceDir, `dsl-${asset}.json`);
    }

    const result = await bridge.run<DslTickResult>('dsl-v4.py', { env });

    return {
      content: [
        {
          type: 'text' as const,
          text: JSON.stringify(result.data ?? { success: false, error: result.error }),
        },
      ],
    };
  };
}
