import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { DslMonitorOutput } from "../types.js";
import type { CronScheduler } from "../lib/cron-scheduler.js";
import { discoverActiveStrategies } from "../lib/state-manager.js";
import { runMonitor } from "../engine/monitor.js";
import { getPluginConfig } from "../config.js";

const ALERTABLE_STATUSES = ["closed", "breached", "tier_changed", "error"] as const;

function isAlertableEvent(output: DslMonitorOutput): boolean {
  return "status" in output && ALERTABLE_STATUSES.includes(output.status as (typeof ALERTABLE_STATUSES)[number]);
}

function formatAlert(output: DslMonitorOutput): string {
  const status = "status" in output ? output.status : "unknown";
  const strategyId = "strategy_id" in output ? output.strategy_id : "";
  const asset = "asset" in output ? output.asset : "";
  const extra =
    status === "closed" && "close_result" in output
      ? ` (${output.close_result})`
      : status === "error" && "error" in output
        ? ` — ${output.error}`
        : "";
  return `[DSL] ${status} — strategy=${strategyId} asset=${asset}${extra}`;
}

export type CreateTickParams = {
  stateDir: string;
  strategyId: string;
  alertChannelId: string | null | undefined;
  api: OpenClawPluginApi;
  cronScheduler: CronScheduler;
  logger: { info: (msg: string) => void };
};

/**
 * Build a tick function for a strategy. Used by the monitor service and by add-dsl CLI.
 */
export function createTick(params: CreateTickParams): () => Promise<void> {
  const { stateDir, strategyId, alertChannelId, api, cronScheduler, logger } = params;

  return async () => {
    try {
      const outputs = await runMonitor(stateDir, strategyId);
      for (const output of outputs) {
        logger.info(JSON.stringify(output));
        if (alertChannelId && isAlertableEvent(output)) {
          const sendProactive =
            api.runtime?.channel?.reply?.sendProactive as
              | ((channelId: string, payload: { text: string }) => Promise<void>)
              | undefined;
          if (sendProactive) {
            sendProactive(alertChannelId, { text: formatAlert(output) }).catch((err: unknown) => {
              logger.info(`DSL alert send failed: ${String(err)}`);
            });
          }
        }
        if ("status" in output && output.status === "strategy_inactive") {
          cronScheduler.stop(strategyId);
          logger.info(`Strategy ${strategyId} inactive — cron stopped`);
        }
      }
    } catch (err) {
      logger.info(`DSL tick error for ${strategyId}: ${String(err)}`);
    }
  };
}

export type MonitorServiceContext = {
  config: unknown;
  stateDir: string;
  logger: { info: (msg: string) => void };
};

export function createMonitorService(
  api: OpenClawPluginApi,
  cronScheduler: CronScheduler
): {
  id: string;
  start(ctx: MonitorServiceContext): Promise<void>;
  stop(): Promise<void>;
} {
  const logger = api.runtime.logging.getChildLogger("senpi-dsl-monitor");

  return {
    id: "senpi-dsl-monitor",
    async start(ctx) {
      const pluginConfig = getPluginConfig(ctx.config);
      const { stateDir, schedule, alertChannelId } = pluginConfig;

      const strategyIds = await discoverActiveStrategies(stateDir);
      for (const strategyId of strategyIds) {
        const tick = createTick({
          stateDir,
          strategyId,
          alertChannelId,
          api,
          cronScheduler,
          logger,
        });
        cronScheduler.start(strategyId, tick, schedule);
      }
      logger.info(`Started cron for ${strategyIds.length} strategies`);
    },
    async stop() {
      cronScheduler.stopAll();
    },
  };
}
