import { pluginConfigSchema } from "./types.js";
import type { PluginConfig } from "./types.js";
import { PLUGIN_ID } from "./constants.js";

/** Shape of OpenClaw config where plugin config lives under plugins[PLUGIN_ID]. */
export type GlobalConfigWithPlugins = {
  plugins?: Record<string, unknown>;
};

/**
 * Read senpi-dsl plugin config from the global OpenClaw config.
 * Uses pluginConfigSchema for validation; OpenClaw has already validated config before register().
 */
export function getPluginConfig(globalConfig: unknown): PluginConfig {
  const plugins = (globalConfig as GlobalConfigWithPlugins)?.plugins;
  const raw = plugins?.[PLUGIN_ID] ?? {};
  return pluginConfigSchema.parse(raw);
}
