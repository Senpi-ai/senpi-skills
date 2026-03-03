import * as fs from 'node:fs';
import * as path from 'node:path';
import type { TigerPluginConfig, TigerPaths } from './types.js';

const DEFAULT_PYTHON_PATH = 'python3';
const DEFAULT_SCRIPT_TIMEOUT = 55000;
const DEFAULT_DSL_TICK_INTERVAL = 30000;

export function parsePluginConfig(
  raw: Record<string, unknown> | undefined,
): TigerPluginConfig {
  const workspace =
    (raw?.workspace as string | undefined) ??
    process.env['TIGER_WORKSPACE'] ??
    undefined;

  if (!workspace) {
    throw new Error(
      'Tiger workspace not configured. Set plugins.tiger.workspace in config.yaml or TIGER_WORKSPACE env var.',
    );
  }

  return {
    workspace,
    pythonPath:
      (raw?.pythonPath as string | undefined) ?? DEFAULT_PYTHON_PATH,
    scriptTimeout:
      (raw?.scriptTimeout as number | undefined) ?? DEFAULT_SCRIPT_TIMEOUT,
    dslTickInterval:
      (raw?.dslTickInterval as number | undefined) ?? DEFAULT_DSL_TICK_INTERVAL,
  };
}

export function resolvePaths(config: TigerPluginConfig): TigerPaths {
  const { workspace } = config;

  if (!fs.existsSync(workspace)) {
    throw new Error(`Tiger workspace does not exist: ${workspace}`);
  }

  const scriptsDir = path.join(workspace, 'scripts');
  const stateDir = path.join(workspace, 'state');
  const configFile = path.join(workspace, 'tiger-config.json');

  const dslScript = path.join(scriptsDir, 'dsl-v4.py');
  if (!fs.existsSync(dslScript)) {
    throw new Error(
      `Tiger scripts not found. Expected: ${dslScript}`,
    );
  }

  return { workspace, scriptsDir, stateDir, configFile };
}
