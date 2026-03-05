import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { pluginConfigSchema } from "./types.js";
import { CronScheduler } from "./lib/cron-scheduler.js";
import { createMonitorService } from "./services/monitor-service.js";
import { createDslCommands } from "./cli/dsl-commands.js";
import { PLUGIN_ID } from "./constants.js";

const plugin = {
  id: PLUGIN_ID,
  name: "DSL — Dynamic Stop-Loss",
  description: "Autonomous dynamic stop-loss monitoring. No LLM required.",
  configSchema: pluginConfigSchema,
  register(api: OpenClawPluginApi) {
    const logger = api.runtime.logging.getChildLogger("senpi-dsl");
    const cronScheduler = new CronScheduler({
      onTickError(strategyId, err) {
        logger.info(`Tick error for strategy ${strategyId}: ${String(err)}`);
      },
    });
    api.registerService(createMonitorService(api, cronScheduler));
    api.registerCli(createDslCommands(api, cronScheduler));
  },
};

export default plugin;
