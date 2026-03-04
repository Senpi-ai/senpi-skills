import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PythonBridge } from '../python-bridge.js';
import type { TigerPluginConfig, TigerPaths } from '../types.js';
import { EventEmitter } from 'node:events';
import type { ChildProcess } from 'node:child_process';

// Mock child_process
vi.mock('node:child_process', () => ({
  spawn: vi.fn(),
}));

import { spawn } from 'node:child_process';
const mockedSpawn = vi.mocked(spawn);

function createMockProcess(): ChildProcess {
  const proc = new EventEmitter() as ChildProcess;
  (proc as { stdout: EventEmitter }).stdout = new EventEmitter();
  (proc as { stderr: EventEmitter }).stderr = new EventEmitter();
  return proc;
}

const paths: TigerPaths = {
  workspace: '/tiger',
  scriptsDir: '/tiger/scripts',
  stateDir: '/tiger/state',
  configFile: '/tiger/tiger-config.json',
};

const config: TigerPluginConfig = {
  workspace: '/tiger',
  pythonPath: 'python3',
  scriptTimeout: 55000,
};

describe('PythonBridge', () => {
  let bridge: PythonBridge;

  beforeEach(() => {
    vi.resetAllMocks();
    bridge = new PythonBridge(paths, config);
  });

  it('parses valid JSON output from script', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run<{ success: boolean }>('dsl-v4.py');

    proc.stdout.emit('data', Buffer.from('{"success":true}'));
    proc.emit('close', 0);

    const result = await promise;
    expect(result.success).toBe(true);
    expect(result.data).toEqual({ success: true });
    expect(result.exitCode).toBe(0);
  });

  it('sets TIGER_WORKSPACE in child env', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('dsl-v4.py');

    proc.stdout.emit('data', Buffer.from('{}'));
    proc.emit('close', 0);

    await promise;

    expect(mockedSpawn).toHaveBeenCalledWith(
      'python3',
      ['/tiger/scripts/dsl-v4.py'],
      expect.objectContaining({
        env: expect.objectContaining({
          TIGER_WORKSPACE: '/tiger',
        }),
      }),
    );
  });

  it('passes additional env vars', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('dsl-v4.py', {
      env: { DSL_STATE_FILE: '/tiger/state/s1/dsl-ETH.json' },
    });

    proc.stdout.emit('data', Buffer.from('{}'));
    proc.emit('close', 0);

    await promise;

    expect(mockedSpawn).toHaveBeenCalledWith(
      'python3',
      ['/tiger/scripts/dsl-v4.py'],
      expect.objectContaining({
        env: expect.objectContaining({
          TIGER_WORKSPACE: '/tiger',
          DSL_STATE_FILE: '/tiger/state/s1/dsl-ETH.json',
        }),
      }),
    );
  });

  it('parses JSON even on non-zero exit code', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run<{ success: boolean; error: string }>('dsl-v4.py');

    proc.stdout.emit('data', Buffer.from('{"success":false,"error":"API timeout"}'));
    proc.emit('close', 1);

    const result = await promise;
    expect(result.success).toBe(false);
    expect(result.data).toEqual({ success: false, error: 'API timeout' });
    expect(result.exitCode).toBe(1);
  });

  it('returns error when stdout is not valid JSON', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('dsl-v4.py');

    proc.stdout.emit('data', Buffer.from('not json'));
    proc.emit('close', 1);

    const result = await promise;
    expect(result.success).toBe(false);
    expect(result.error).toContain('Failed to parse script output');
    expect(result.exitCode).toBe(1);
  });

  it('returns error when script produces no output', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('dsl-v4.py');

    proc.emit('close', 1);

    const result = await promise;
    expect(result.success).toBe(false);
    expect(result.error).toContain('Script produced no output');
  });

  it('captures stderr', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('dsl-v4.py');

    proc.stderr.emit('data', Buffer.from('warning: something'));
    proc.stdout.emit('data', Buffer.from('{"ok":true}'));
    proc.emit('close', 0);

    const result = await promise;
    expect(result.stderr).toBe('warning: something');
  });

  it('handles spawn error', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('nonexistent.py');

    const err = new Error('ENOENT');
    err.name = 'Error';
    proc.emit('error', err);

    const result = await promise;
    expect(result.success).toBe(false);
    expect(result.error).toContain('Failed to spawn script');
  });

  it('handles abort/timeout error', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run('slow.py', { timeout: 100 });

    const err = new Error('aborted');
    err.name = 'AbortError';
    proc.emit('error', err);

    const result = await promise;
    expect(result.success).toBe(false);
    expect(result.error).toContain('Script timed out');
  });

  it('concatenates chunked stdout', async () => {
    const proc = createMockProcess();
    mockedSpawn.mockReturnValue(proc);

    const promise = bridge.run<{ key: string }>('dsl-v4.py');

    proc.stdout.emit('data', Buffer.from('{"ke'));
    proc.stdout.emit('data', Buffer.from('y":"val'));
    proc.stdout.emit('data', Buffer.from('ue"}'));
    proc.emit('close', 0);

    const result = await promise;
    expect(result.data).toEqual({ key: 'value' });
  });
});
