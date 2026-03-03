import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createDslTickHandler } from '../../tools/dsl-tick.js';
import type { PythonBridge } from '../../python-bridge.js';
import type { StateManager } from '../../state-manager.js';
import type { TigerPaths } from '../../types.js';

describe('tiger_dsl_tick', () => {
  const paths: TigerPaths = {
    workspace: '/tiger',
    scriptsDir: '/tiger/scripts',
    stateDir: '/tiger/state',
    configFile: '/tiger/tiger-config.json',
  };

  let mockBridge: { run: ReturnType<typeof vi.fn> };
  let mockStateManager: { getDefaultStrategyId: ReturnType<typeof vi.fn> };
  let handler: ReturnType<typeof createDslTickHandler>;

  beforeEach(() => {
    mockBridge = {
      run: vi.fn().mockResolvedValue({
        success: true,
        data: { success: true, heartbeat: 'HEARTBEAT_OK' },
        exitCode: 0,
      }),
    };
    mockStateManager = {
      getDefaultStrategyId: vi.fn().mockResolvedValue('default'),
    };
    handler = createDslTickHandler(
      mockBridge as unknown as PythonBridge,
      mockStateManager as unknown as StateManager,
      paths,
    );
  });

  it('runs dsl-v4.py in combined mode by default', async () => {
    const result = await handler('call-1', {});

    expect(mockBridge.run).toHaveBeenCalledWith('dsl-v4.py', { env: {} });
    const text = JSON.parse(result.content[0].text);
    expect(text.success).toBe(true);
    expect(text.heartbeat).toBe('HEARTBEAT_OK');
  });

  it('sets DSL_STATE_FILE env in single mode', async () => {
    await handler('call-2', { mode: 'single', asset: 'ETH', strategy_id: 's1' });

    expect(mockBridge.run).toHaveBeenCalledWith('dsl-v4.py', {
      env: {
        DSL_STATE_FILE: '/tiger/state/s1/dsl-ETH.json',
      },
    });
  });

  it('returns error when single mode without asset', async () => {
    const result = await handler('call-3', { mode: 'single' });

    const text = JSON.parse(result.content[0].text);
    expect(text.success).toBe(false);
    expect(text.error).toContain('asset is required');
    expect(mockBridge.run).not.toHaveBeenCalled();
  });

  it('uses default strategy ID when not provided', async () => {
    mockStateManager.getDefaultStrategyId.mockResolvedValue('my-strat');

    await handler('call-4', { mode: 'single', asset: 'BTC' });

    expect(mockBridge.run).toHaveBeenCalledWith('dsl-v4.py', {
      env: {
        DSL_STATE_FILE: '/tiger/state/my-strat/dsl-BTC.json',
      },
    });
  });

  it('returns bridge error when script fails', async () => {
    mockBridge.run.mockResolvedValue({
      success: false,
      error: 'Script timed out',
      exitCode: -1,
    });

    const result = await handler('call-5', {});

    const text = JSON.parse(result.content[0].text);
    expect(text.success).toBe(false);
    expect(text.error).toBe('Script timed out');
  });

  it('returns data from successful bridge result', async () => {
    mockBridge.run.mockResolvedValue({
      success: true,
      data: {
        success: true,
        processed: 3,
        results: [{ asset: 'ETH', action: 'HOLD' }],
      },
      exitCode: 0,
    });

    const result = await handler('call-6', {});

    const text = JSON.parse(result.content[0].text);
    expect(text.processed).toBe(3);
    expect(text.results[0].asset).toBe('ETH');
  });
});
