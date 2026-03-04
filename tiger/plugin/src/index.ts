import { parsePluginConfig, resolvePaths } from './config-resolver.js';
import { PythonBridge } from './python-bridge.js';
import { StateManager } from './state-manager.js';
import { DslRunner } from './services/dsl-runner.js';
import {
  dslTickSchema,
  createDslTickHandler,
  getStateSchema,
  createGetStateHandler,
  getDslStateSchema,
  createGetDslStateHandler,
  getTradeLogSchema,
  createGetTradeLogHandler,
  createDslSchema,
  createCreateDslHandler,
  deactivateDslSchema,
  createDeactivateDslHandler,
} from './tools/index.js';

// Lightweight PluginApi interface — avoids compile-time dependency on openclaw peer package.
// At runtime, OpenClaw passes the real object that satisfies this shape.
interface PluginLogger {
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

interface PluginApi {
  id: string;
  name: string;
  pluginConfig?: Record<string, unknown>;
  logger: PluginLogger;

  registerTool(
    tool: {
      name: string;
      label?: string;
      description: string;
      parameters: unknown;
      execute(
        toolCallId: string,
        params: Record<string, unknown>,
      ): Promise<{ content: Array<{ type: string; text: string }> }>;
    },
    opts?: { name?: string },
  ): void;

  registerService(service: {
    id: string;
    start: (ctx: { stateDir: string }) => void | Promise<void>;
    stop?: (ctx: { stateDir: string }) => void | Promise<void>;
  }): void;

  on(
    hookName: string,
    handler: (...args: unknown[]) => void | Promise<void>,
    opts?: { priority?: number },
  ): void;
}

const VERSION = '1.0.0';

const tigerPlugin = {
  id: 'tiger',
  name: 'Tiger Plugin',
  version: VERSION,
  description: 'Multi-scanner goal-based trading system for Hyperliquid perpetuals',

  configSchema: {
    parse(value: unknown) {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as Record<string, unknown>;
      }
      return {};
    },
  },

  register(api: PluginApi) {
    let config;
    let paths;

    try {
      config = parsePluginConfig(api.pluginConfig);
      paths = resolvePaths(config);
    } catch (err) {
      api.logger.error(
        `[Tiger] Configuration error: ${(err as Error).message}`,
      );
      return;
    }

    const bridge = new PythonBridge(paths, config);
    const stateManager = new StateManager(paths);
    const dslRunner = new DslRunner(bridge, config, api.logger);

    // --- Tools ---

    api.registerTool({
      name: 'tiger_dsl_tick',
      label: 'Tiger DSL Tick',
      description:
        'Run DSL v4 trailing stop tick on demand. In combined mode (default), processes all active DSL states. In single mode, processes one asset. Note: the plugin also runs DSL automatically via the tiger-dsl-runner service.',
      parameters: dslTickSchema,
      execute: createDslTickHandler(bridge, stateManager, paths),
    });

    api.registerTool({
      name: 'tiger_get_state',
      label: 'Tiger Get State',
      description:
        'Read the current Tiger strategy state including balances, P&L, positions, aggression level, and safety status.',
      parameters: getStateSchema,
      execute: createGetStateHandler(stateManager),
    });

    api.registerTool({
      name: 'tiger_get_dsl_state',
      label: 'Tiger Get DSL State',
      description:
        'Read DSL trailing stop state. With asset param: full state for that asset. Without: summary list of all active DSL assets.',
      parameters: getDslStateSchema,
      execute: createGetDslStateHandler(stateManager),
    });

    api.registerTool({
      name: 'tiger_get_trade_log',
      label: 'Tiger Get Trade Log',
      description:
        'Read recent trade log entries. Returns the last N entries (default 20) with P&L, exit reason, and confluence data.',
      parameters: getTradeLogSchema,
      execute: createGetTradeLogHandler(stateManager),
    });

    api.registerTool({
      name: 'tiger_create_dsl',
      label: 'Tiger Create DSL',
      description:
        'Create DSL trailing stop state for a new position. Pattern-specific tuning (retrace thresholds, breach decay) is applied automatically based on the pattern parameter. Writes dsl-{ASSET}.json atomically.',
      parameters: createDslSchema,
      execute: createCreateDslHandler(stateManager),
    });

    api.registerTool({
      name: 'tiger_deactivate_dsl',
      label: 'Tiger Deactivate DSL',
      description:
        'Deactivate DSL monitoring for a closed position. Sets active: false with timestamp and reason. Idempotent — safe to call on already-inactive or missing states.',
      parameters: deactivateDslSchema,
      execute: createDeactivateDslHandler(stateManager),
    });

    // --- DSL Runner Service ---

    api.registerService({
      id: 'tiger-dsl-runner',
      start() {
        dslRunner.start();
      },
      stop() {
        dslRunner.stop();
      },
    });

    // --- Lifecycle ---

    api.on('gateway_start', () => {
      api.logger.info(
        `[Senpi][Tiger] v${VERSION} plugin registered — workspace: ${paths.workspace}, DSL tick: ${config.dslTickInterval}ms`,
      );
    });
  },
};

export default tigerPlugin;
