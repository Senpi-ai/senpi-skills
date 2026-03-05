import { readdir, stat } from "fs/promises";
import { strategyGet, getClearinghouse, getActivePositionCoins } from "./mcp.js";
import { listStrategyStateFiles } from "./paths.js";
import { archiveStateFile } from "./state-io.js";
import { processOnePosition } from "./process-one.js";
import type { DslMonitorOutput } from "../types.js";

/**
 * Archives all state files for a strategy without checking for active positions.
 * Only called after MCP confirms the strategy is inactive (confirmedInactive: true).
 * For user-initiated cleanup that must check for active positions, use cleanup.ts:cleanupStrategy().
 */
async function cleanupStrategyStateDir(
  stateDir: string,
  strategyId: string
): Promise<number> {
  const files = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);
  let count = 0;
  for (const { path } of files) {
    try {
      await archiveStateFile(path, stateDir, strategyId);
      count++;
    } catch {
      // ignore
    }
  }
  return count;
}

export async function runMonitor(
  stateDir: string,
  strategyId: string,
  preset: string = "default"
): Promise<DslMonitorOutput[]> {
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const outputs: DslMonitorOutput[] = [];

  const { active, wallet, error: activeError, confirmedInactive } = await strategyGet(strategyId);
  if (!active) {
    if (confirmedInactive) {
      const deleted = await cleanupStrategyStateDir(stateDir, strategyId);
      outputs.push({
        status: "strategy_inactive",
        strategy_id: strategyId,
        preset,
        message: "Strategy not active (Senpi MCP). State files cleaned. Agent: remove cron for this strategy.",
        reason: activeError ?? undefined,
        state_files_deleted: deleted,
        time: now,
      });
      return outputs;
    }
    outputs.push({
      status: "error",
      error: "strategy_get_failed",
      strategy_id: strategyId,
      preset,
      message: activeError ?? undefined,
      time: now,
    });
    return outputs;
  }

  const { data: chData, error: chError } = await getClearinghouse(wallet!);
  if (chError != null) {
    outputs.push({
      status: "error",
      error: "clearinghouse_failed",
      strategy_id: strategyId,
      preset,
      message: chError,
      time: now,
    });
    return outputs;
  }

  const coins = getActivePositionCoins(chData!);
  const stateFiles = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);

  for (const { path, asset } of stateFiles) {
    if (!coins.has(asset)) {
      try {
        await archiveStateFile(path, stateDir, strategyId);
      } catch {
        // ignore
      }
    }
  }

  const coinsSorted = [...coins].sort();
  for (const coin of coinsSorted) {
    const pair = stateFiles.find((p) => p.asset === coin);
    if (pair) {
      const out = await processOnePosition(pair.path, stateDir, strategyId, now);
      if (out) outputs.push(out);
    }
  }

  if (outputs.length === 0) {
    outputs.push({
      status: "no_positions",
      strategy_id: strategyId,
      preset,
      message: "Strategy active but no position state files to process. Agent: keep cron; next run may have positions or output strategy_inactive after cleanup.",
      time: now,
    });
  }

  return outputs;
}
