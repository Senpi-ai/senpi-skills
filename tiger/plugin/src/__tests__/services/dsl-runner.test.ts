import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { DslRunner } from '../../services/dsl-runner.js';
import type { PythonBridge } from '../../python-bridge.js';
import type { TigerPluginConfig } from '../../types.js';

const config: TigerPluginConfig = {
  workspace: '/tiger',
  pythonPath: 'python3',
  scriptTimeout: 55000,
  dslTickInterval: 100, // fast for tests
};

describe('DslRunner', () => {
  let mockBridge: { run: ReturnType<typeof vi.fn> };
  let mockLogger: {
    info: ReturnType<typeof vi.fn>;
    warn: ReturnType<typeof vi.fn>;
    error: ReturnType<typeof vi.fn>;
  };
  let runner: DslRunner;

  beforeEach(() => {
    vi.useFakeTimers();
    mockBridge = {
      run: vi.fn().mockResolvedValue({
        success: true,
        data: { success: true, heartbeat: 'HEARTBEAT_OK' },
        exitCode: 0,
      }),
    };
    mockLogger = {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    };
    runner = new DslRunner(
      mockBridge as unknown as PythonBridge,
      config,
      mockLogger,
    );
  });

  afterEach(() => {
    runner.stop();
    vi.useRealTimers();
  });

  it('starts and stops cleanly', () => {
    runner.start();
    expect(runner.isRunning).toBe(true);
    expect(mockLogger.info).toHaveBeenCalledWith(
      expect.stringContaining('DSL runner started'),
    );

    runner.stop();
    expect(runner.isRunning).toBe(false);
    expect(mockLogger.info).toHaveBeenCalledWith(
      expect.stringContaining('DSL runner stopped'),
    );
  });

  it('runs first tick immediately on start', async () => {
    runner.start();

    // Flush microtasks from the immediate tick
    await vi.advanceTimersByTimeAsync(0);

    expect(mockBridge.run).toHaveBeenCalledWith('dsl-v4.py');
    expect(mockBridge.run).toHaveBeenCalledTimes(1);
  });

  it('runs tick on configured interval', async () => {
    runner.start();

    // First immediate tick
    await vi.advanceTimersByTimeAsync(0);
    expect(mockBridge.run).toHaveBeenCalledTimes(1);

    // Second tick after interval
    await vi.advanceTimersByTimeAsync(config.dslTickInterval);
    expect(mockBridge.run).toHaveBeenCalledTimes(2);

    // Third tick
    await vi.advanceTimersByTimeAsync(config.dslTickInterval);
    expect(mockBridge.run).toHaveBeenCalledTimes(3);
  });

  it('does not double-start', () => {
    runner.start();
    runner.start();
    expect(mockLogger.info).toHaveBeenCalledTimes(1);
  });

  it('stop is idempotent when not running', () => {
    runner.stop();
    expect(runner.isRunning).toBe(false);
  });

  it('tick returns DSL result on success', async () => {
    const result = await runner.tick();
    expect(result).toEqual({ success: true, heartbeat: 'HEARTBEAT_OK' });
  });

  it('tick logs error on script failure', async () => {
    mockBridge.run.mockResolvedValue({
      success: false,
      error: 'API timeout',
      exitCode: 1,
    });

    const result = await runner.tick();

    expect(result).toBeNull();
    expect(mockLogger.error).toHaveBeenCalledWith(
      expect.stringContaining('DSL tick failed'),
    );
  });

  it('tick logs closures from results', async () => {
    mockBridge.run.mockResolvedValue({
      success: true,
      data: {
        success: true,
        processed: 2,
        results: [
          { asset: 'ETH', action: 'HOLD' },
          { asset: 'BTC', action: 'CLOSE', closed: true },
        ],
      },
      exitCode: 0,
    });

    await runner.tick();

    expect(mockLogger.warn).toHaveBeenCalledWith(
      expect.stringContaining('DSL closed position'),
    );
  });

  it('tick handles exceptions gracefully', async () => {
    mockBridge.run.mockRejectedValue(new Error('spawn failed'));

    const result = await runner.tick();

    expect(result).toBeNull();
    expect(mockLogger.error).toHaveBeenCalledWith(
      expect.stringContaining('DSL tick exception'),
    );
  });

  it('guards against overlapping ticks', async () => {
    // Make bridge slow
    let resolveRun: () => void;
    mockBridge.run.mockReturnValue(
      new Promise<{ success: boolean; data: unknown; exitCode: number }>(
        (resolve) => {
          resolveRun = () =>
            resolve({
              success: true,
              data: { success: true, heartbeat: 'HEARTBEAT_OK' },
              exitCode: 0,
            });
        },
      ),
    );

    // Start first tick
    const tick1 = runner.tick();

    // Second tick should return null (guard)
    const tick2Result = await runner.tick();
    expect(tick2Result).toBeNull();

    // Resolve first tick
    resolveRun!();
    await tick1;

    // Now third tick should work
    mockBridge.run.mockResolvedValue({
      success: true,
      data: { success: true },
      exitCode: 0,
    });
    const tick3Result = await runner.tick();
    expect(tick3Result).toEqual({ success: true });
  });

  it('includes interval in start log', () => {
    runner.start();
    expect(mockLogger.info).toHaveBeenCalledWith(
      expect.stringContaining('100ms'),
    );
  });
});
