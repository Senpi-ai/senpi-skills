import { existsSync } from "fs";
import { readdir, stat, mkdir, rename, rm } from "fs/promises";
import { join } from "path";
import { ARCHIVE_DIR_NAME } from "../constants.js";
import { readStateFile } from "./state-io.js";

export type CleanupStrategyResult = {
  status: "cleaned" | "blocked" | "error";
  strategy_id: string;
  positions_archived?: number;
  blocked_by_active?: string[];
  note?: string;
  error?: string;
  time: string;
};

export async function cleanupStrategy(
  stateDir: string,
  strategyId: string
): Promise<CleanupStrategyResult> {
  const time = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  if (!strategyId?.trim()) {
    return { status: "error", strategy_id: strategyId, error: "DSL_STRATEGY_ID required", time };
  }
  const strategyDir = join(stateDir, strategyId);
  let names: string[];
  try {
    names = await readdir(strategyDir);
  } catch {
    return {
      status: "cleaned",
      strategy_id: strategyId,
      positions_archived: 0,
      blocked_by_active: [],
      note: "strategy_dir_missing",
      time,
    };
  }

  const blocked: string[] = [];
  // Count of closed positions to archive (directory is moved as a whole later, not per-file).
  let deletedCount = 0;
  for (const name of names) {
    if (!name.endsWith(".json")) continue;
    const path = join(strategyDir, name);
    try {
      const s = await stat(path);
      if (!s.isFile()) continue;
    } catch {
      continue;
    }
    const state = await readStateFile<Record<string, unknown>>(path);
    if (state?.active) {
      blocked.push((state.asset as string) ?? name.replace(/\.json$/, ""));
    } else {
      deletedCount++;
    }
  }
  if (blocked.length > 0) {
    return {
      status: "blocked",
      strategy_id: strategyId,
      blocked_by_active: blocked,
      time,
    };
  }

  const archiveBase = join(stateDir, ARCHIVE_DIR_NAME);
  const archiveDest = join(archiveBase, strategyId);
  await mkdir(archiveBase, { recursive: true });
  if (existsSync(archiveDest)) {
    const toMove = await readdir(strategyDir);
    for (const n of toMove) {
      const src = join(strategyDir, n);
      try {
        await rename(src, join(archiveDest, n));
      } catch {
        // ignore
      }
    }
    await rm(strategyDir, { recursive: true, force: true });
  } else {
    await rename(strategyDir, archiveDest);
  }
  return {
    status: "cleaned",
    strategy_id: strategyId,
    positions_archived: deletedCount,
    blocked_by_active: [],
    time,
  };
}
