import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as fs from 'node:fs';

vi.mock('node:fs');
const mockedFs = vi.mocked(fs);

describe('Tiger Plugin', () => {
  let plugin: typeof import('../index.js').default;

  beforeEach(async () => {
    vi.resetModules();
    vi.resetAllMocks();
    // Make workspace validation pass
    mockedFs.existsSync.mockReturnValue(true);
    const mod = await import('../index.js');
    plugin = mod.default;
  });

  it('exports plugin with correct metadata', () => {
    expect(plugin.id).toBe('tiger');
    expect(plugin.name).toBe('Tiger Plugin');
    expect(plugin.version).toBe('1.0.0');
  });

  it('has a register function', () => {
    expect(typeof plugin.register).toBe('function');
  });

  it('has configSchema with parse method', () => {
    expect(typeof plugin.configSchema.parse).toBe('function');
  });

  it('configSchema.parse returns object from valid input', () => {
    const result = plugin.configSchema.parse({ workspace: '/w' });
    expect(result).toEqual({ workspace: '/w' });
  });

  it('configSchema.parse returns empty object from invalid input', () => {
    expect(plugin.configSchema.parse(null)).toEqual({});
    expect(plugin.configSchema.parse('string')).toEqual({});
    expect(plugin.configSchema.parse(undefined)).toEqual({});
  });

  it('registers 6 tools on valid config', () => {
    const registeredTools: Array<{ name: string }> = [];

    const mockApi = {
      id: 'tiger',
      name: 'Tiger Plugin',
      pluginConfig: { workspace: '/tiger' },
      logger: {
        info: vi.fn(),
        warn: vi.fn(),
        error: vi.fn(),
      },
      registerTool: vi.fn((tool: { name: string }) => {
        registeredTools.push(tool);
      }),
      registerService: vi.fn(),
      on: vi.fn(),
    };

    plugin.register(mockApi);

    expect(registeredTools).toHaveLength(6);
    const toolNames = registeredTools.map((t) => t.name);
    expect(toolNames).toContain('tiger_dsl_tick');
    expect(toolNames).toContain('tiger_get_state');
    expect(toolNames).toContain('tiger_get_dsl_state');
    expect(toolNames).toContain('tiger_get_trade_log');
    expect(toolNames).toContain('tiger_create_dsl');
    expect(toolNames).toContain('tiger_deactivate_dsl');
  });

  it('registers tiger-dsl-runner service', () => {
    const registeredServices: Array<{ id: string }> = [];

    const mockApi = {
      id: 'tiger',
      name: 'Tiger Plugin',
      pluginConfig: { workspace: '/tiger' },
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerTool: vi.fn(),
      registerService: vi.fn((service: { id: string }) => {
        registeredServices.push(service);
      }),
      on: vi.fn(),
    };

    plugin.register(mockApi);

    expect(registeredServices).toHaveLength(1);
    expect(registeredServices[0].id).toBe('tiger-dsl-runner');
  });

  it('registers gateway_start hook', () => {
    const registeredHooks: string[] = [];

    const mockApi = {
      id: 'tiger',
      name: 'Tiger Plugin',
      pluginConfig: { workspace: '/tiger' },
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerTool: vi.fn(),
      registerService: vi.fn(),
      on: vi.fn((hookName: string) => {
        registeredHooks.push(hookName);
      }),
    };

    plugin.register(mockApi);

    expect(registeredHooks).toContain('gateway_start');
  });

  it('logs error and returns when workspace missing', () => {
    const mockApi = {
      id: 'tiger',
      name: 'Tiger Plugin',
      pluginConfig: {},
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerTool: vi.fn(),
      registerService: vi.fn(),
      on: vi.fn(),
    };

    // Remove TIGER_WORKSPACE to trigger config error
    const orig = process.env['TIGER_WORKSPACE'];
    delete process.env['TIGER_WORKSPACE'];

    plugin.register(mockApi);

    process.env['TIGER_WORKSPACE'] = orig;

    expect(mockApi.logger.error).toHaveBeenCalledWith(
      expect.stringContaining('[Tiger] Configuration error'),
    );
    expect(mockApi.registerTool).not.toHaveBeenCalled();
  });

  it('logs error when workspace dir does not exist', () => {
    mockedFs.existsSync.mockReturnValue(false);

    const mockApi = {
      id: 'tiger',
      name: 'Tiger Plugin',
      pluginConfig: { workspace: '/nonexistent' },
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerTool: vi.fn(),
      registerService: vi.fn(),
      on: vi.fn(),
    };

    plugin.register(mockApi);

    expect(mockApi.logger.error).toHaveBeenCalledWith(
      expect.stringContaining('does not exist'),
    );
    expect(mockApi.registerTool).not.toHaveBeenCalled();
  });

  it('each registered tool has description and parameters', () => {
    const registeredTools: Array<{
      name: string;
      description: string;
      parameters: unknown;
    }> = [];

    const mockApi = {
      id: 'tiger',
      name: 'Tiger Plugin',
      pluginConfig: { workspace: '/tiger' },
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerTool: vi.fn(
        (tool: { name: string; description: string; parameters: unknown }) => {
          registeredTools.push(tool);
        },
      ),
      registerService: vi.fn(),
      on: vi.fn(),
    };

    plugin.register(mockApi);

    for (const tool of registeredTools) {
      expect(tool.description).toBeTruthy();
      expect(tool.parameters).toBeTruthy();
    }
  });
});
