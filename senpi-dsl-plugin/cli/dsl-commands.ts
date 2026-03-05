import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { CronScheduler } from "../lib/cron-scheduler.js";
import { discoverActiveStrategies } from "../lib/state-manager.js";
import { createTick } from "../services/monitor-service.js";
import { getPluginConfig } from "../config.js";
import { SUBCOMMANDS, type Subcommand } from "../constants.js";
import { parseArgv, getArgsAfterSubcommand } from "./parse-argv.js";
import * as engine from "../engine/index.js";

interface DslCommand {
  description: (d: string) => DslCommand;
  command: (subName: string) => {
    description: (d: string) => { allowUnknownOption: () => { action: (fn: () => void | Promise<void>) => void } };
  };
}

type CliOutput = Record<string, unknown>;

async function handleAddDslPostAction(
  api: OpenClawPluginApi,
  cronScheduler: CronScheduler,
  out: CliOutput
): Promise<void> {
  if (out.status !== "ok" || !out.cron_needed || !out.strategy_id) return;
  const globalConfig = await api.runtime.config.loadConfig();
  const pluginConfig = getPluginConfig(globalConfig);
  const logger = api.runtime.logging.getChildLogger("senpi-dsl-cli");
  const tick = createTick({
    stateDir: pluginConfig.stateDir,
    strategyId: String(out.strategy_id),
    alertChannelId: pluginConfig.alertChannelId,
    api,
    cronScheduler,
    logger,
  });
  cronScheduler.start(String(out.strategy_id), tick, pluginConfig.schedule);
}

async function handleDeleteDslPostAction(
  api: OpenClawPluginApi,
  cronScheduler: CronScheduler,
  out: CliOutput
): Promise<void> {
  if (out.status !== "ok" || !out.strategy_id) return;
  const globalConfig = await api.runtime.config.loadConfig();
  const pluginConfig = getPluginConfig(globalConfig);
  const active = await discoverActiveStrategies(pluginConfig.stateDir);
  if (!active.includes(String(out.strategy_id))) {
    cronScheduler.stop(String(out.strategy_id));
  }
}

function runCommandAndPrint(
  subcommand: Subcommand,
  pluginConfig: { stateDir: string },
  argv: Record<string, string>
): Promise<CliOutput> {
  const stateDir = argv.stateDir || pluginConfig.stateDir;
  const strategyId = (argv.strategyId ?? process.env.DSL_STRATEGY_ID ?? "").trim();

  switch (subcommand) {
    case "add-dsl": {
      const direction = (argv.direction ?? "").toUpperCase();
      if (direction !== "LONG" && direction !== "SHORT") {
        return Promise.resolve({
          action: "add-dsl",
          status: "error",
          error: "invalid_direction",
          message: "direction must be LONG or SHORT",
        });
      }
      const leverage = argv.leverage ? parseFloat(argv.leverage) : 1;
      const margin = argv.margin ? parseFloat(argv.margin) : undefined;
      return engine.addDsl({
        stateDir,
        strategyId,
        asset: argv.asset ?? "",
        dex: argv.dex ?? null,
        direction,
        leverage,
        margin: margin ?? null,
        preset: argv.preset ?? "default",
        config: argv.config ?? null,
      });
    }
    case "update-dsl":
      return engine.updateDsl({
        stateDir,
        strategyId,
        config: argv.config ?? "{}",
        asset: argv.asset ?? null,
        dex: argv.dex ?? null,
      });
    case "pause-dsl":
      return engine.pauseResumeDsl({
        stateDir,
        strategyId,
        asset: argv.asset ?? null,
        dex: argv.dex ?? null,
        active: false,
      });
    case "resume-dsl":
      return engine.pauseResumeDsl({
        stateDir,
        strategyId,
        asset: argv.asset ?? null,
        dex: argv.dex ?? null,
        active: true,
      });
    case "delete-dsl":
      return engine.deleteDsl({
        stateDir,
        strategyId,
        asset: argv.asset ?? null,
        dex: argv.dex ?? null,
      });
    case "status-dsl": {
      return engine.statusDsl({
        stateDir,
        strategyId,
        asset: argv.asset ?? null,
        dex: argv.dex ?? null,
      });
    }
    default:
      return Promise.resolve({ action: subcommand, status: "error", error: "unknown_subcommand" });
  }
}

export function createDslCommands(
  api: OpenClawPluginApi,
  cronScheduler: CronScheduler
): (registrar: { program: { command: (name: string) => DslCommand } }) => void {
  return ({ program }) => {
    const dsl = program
      .command("senpi-dsl")
      .description("Dynamic Stop-Loss: add-dsl, update-dsl, pause-dsl, resume-dsl, delete-dsl, status-dsl");

    const logger = api.runtime.logging.getChildLogger("senpi-dsl-cli");

    const register = (
      subcommand: Subcommand,
      description: string,
      postAction?: (out: CliOutput) => Promise<void>
    ) => {
      const argSuffix = subcommand === "add-dsl" ? " [preset]" : "";
      dsl
        .command(`${subcommand}${argSuffix}`)
        .description(description)
        .allowUnknownOption()
        .action(async () => {
          try {
            const args = getArgsAfterSubcommand(subcommand);
            const argv = parseArgv(args);
            const globalConfig = await api.runtime.config.loadConfig();
            const pluginConfig = getPluginConfig(globalConfig);

            const out = await runCommandAndPrint(subcommand, pluginConfig, argv);

            if (out.status === "error") {
              process.exitCode = 1;
            }
            if (subcommand === "status-dsl") {
              const positions = (out as { positions?: unknown[] }).positions;
              if (Array.isArray(positions) && positions.length === 1 && argv.asset) {
                console.log(JSON.stringify(positions[0], null, 2));
              } else {
                console.log(JSON.stringify(out, null, 2));
              }
            } else {
              console.log(JSON.stringify(out));
            }
            if (postAction) {
              await postAction(out);
            }
          } catch (err) {
            logger.info(String(err));
            console.error(String(err));
            process.exitCode = 1;
          }
        });
    };

    register("add-dsl", "Add DSL for a position (preset optional)", (out) =>
      handleAddDslPostAction(api, cronScheduler, out)
    );
    register("update-dsl", "Update DSL config");
    register("pause-dsl", "Pause DSL for position(s)");
    register("resume-dsl", "Resume DSL for position(s)");
    register("delete-dsl", "Remove DSL for a position", (out) =>
      handleDeleteDslPostAction(api, cronScheduler, out)
    );
    register("status-dsl", "Show DSL status (formatted JSON)");
  };
}
