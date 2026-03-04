import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fs from 'node:fs';
import { parsePluginConfig, resolvePaths } from '../config-resolver.js';

vi.mock('node:fs');

const mockedFs = vi.mocked(fs);

describe('parsePluginConfig', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env['TIGER_WORKSPACE'];
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it('uses workspace from plugin config', () => {
    const config = parsePluginConfig({ workspace: '/path/to/workspace' });
    expect(config.workspace).toBe('/path/to/workspace');
  });

  it('falls back to TIGER_WORKSPACE env var', () => {
    process.env['TIGER_WORKSPACE'] = '/env/workspace';
    const config = parsePluginConfig({});
    expect(config.workspace).toBe('/env/workspace');
  });

  it('plugin config takes priority over env var', () => {
    process.env['TIGER_WORKSPACE'] = '/env/workspace';
    const config = parsePluginConfig({ workspace: '/config/workspace' });
    expect(config.workspace).toBe('/config/workspace');
  });

  it('throws when no workspace configured', () => {
    expect(() => parsePluginConfig({})).toThrow('Tiger workspace not configured');
  });

  it('throws when undefined config', () => {
    expect(() => parsePluginConfig(undefined)).toThrow(
      'Tiger workspace not configured',
    );
  });

  it('uses default pythonPath when not specified', () => {
    const config = parsePluginConfig({ workspace: '/w' });
    expect(config.pythonPath).toBe('python3');
  });

  it('uses custom pythonPath when specified', () => {
    const config = parsePluginConfig({
      workspace: '/w',
      pythonPath: '/usr/bin/python3.11',
    });
    expect(config.pythonPath).toBe('/usr/bin/python3.11');
  });

  it('uses default scriptTimeout when not specified', () => {
    const config = parsePluginConfig({ workspace: '/w' });
    expect(config.scriptTimeout).toBe(55000);
  });

  it('uses custom scriptTimeout when specified', () => {
    const config = parsePluginConfig({
      workspace: '/w',
      scriptTimeout: 30000,
    });
    expect(config.scriptTimeout).toBe(30000);
  });

  it('uses default dslTickInterval when not specified', () => {
    const config = parsePluginConfig({ workspace: '/w' });
    expect(config.dslTickInterval).toBe(30000);
  });

  it('uses custom dslTickInterval when specified', () => {
    const config = parsePluginConfig({
      workspace: '/w',
      dslTickInterval: 15000,
    });
    expect(config.dslTickInterval).toBe(15000);
  });
});

describe('resolvePaths', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('resolves all paths from workspace', () => {
    mockedFs.existsSync.mockReturnValue(true);

    const paths = resolvePaths({
      workspace: '/tiger',
      pythonPath: 'python3',
      scriptTimeout: 55000,
      dslTickInterval: 30000,
    });

    expect(paths.workspace).toBe('/tiger');
    expect(paths.scriptsDir).toBe('/tiger/scripts');
    expect(paths.stateDir).toBe('/tiger/state');
    expect(paths.configFile).toBe('/tiger/tiger-config.json');
  });

  it('throws when workspace does not exist', () => {
    mockedFs.existsSync.mockReturnValue(false);

    expect(() =>
      resolvePaths({
        workspace: '/missing',
        pythonPath: 'python3',
        scriptTimeout: 55000,
        dslTickInterval: 30000,
      }),
    ).toThrow('Tiger workspace does not exist: /missing');
  });

  it('throws when dsl-v4.py not found', () => {
    mockedFs.existsSync
      .mockReturnValueOnce(true) // workspace exists
      .mockReturnValueOnce(false); // dsl-v4.py missing

    expect(() =>
      resolvePaths({
        workspace: '/tiger',
        pythonPath: 'python3',
        scriptTimeout: 55000,
        dslTickInterval: 30000,
      }),
    ).toThrow('Tiger scripts not found');
  });
});
