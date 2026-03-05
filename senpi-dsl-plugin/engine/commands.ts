import { readdir, stat, readFile, mkdir, rmdir } from "fs/promises";
import { join, dirname } from "path";
import { strategyGet, getClearinghouse, getPositionFromClearinghouse } from "./mcp.js";
import { listStrategyStateFiles, normalizeAssetDex } from "./paths.js";
import { readStateFile, writeStateFile, archiveStateFile, getStateFilePath } from "./state-io.js";
import { buildStateForAddDsl, deepMergeConfig, normalizeStatePhaseConfig, DEFAULT_PHASE1_RETRACE } from "./state-schema.js";
import { DEFAULT_SCHEDULE } from "../constants.js";

export type AddDslOptions = {
  stateDir: string;
  strategyId: string;
  asset: string;
  dex?: string | null;
  direction: string;
  leverage?: number;
  margin?: number | null;
  preset?: string;
  config?: string | null;
};

export async function addDsl(options: AddDslOptions): Promise<Record<string, unknown>> {
  const { stateDir, strategyId, asset, dex, direction, leverage = 1, margin: marginOpt, preset = "default", config } = options;
  const { canonicalAsset: assetNorm } = normalizeAssetDex(asset, dex ?? undefined);
  if (!strategyId?.trim()) {
    return { action: "add-dsl", status: "error", error: "strategy_id_required", message: "Set --strategy-id or DSL_STRATEGY_ID" };
  }
  if (leverage <= 0) {
    return { action: "add-dsl", status: "error", error: "invalid_leverage", message: "leverage must be > 0" };
  }
  let configJson: Record<string, unknown> | null = null;
  if (config) {
    try {
      const parsed = JSON.parse(config);
      if (typeof parsed === "object" && parsed !== null) configJson = parsed;
    } catch (e) {
      return { action: "add-dsl", status: "error", error: "invalid_config_json", message: String(e) };
    }
  }

  const { active, wallet, error: stratErr } = await strategyGet(strategyId);
  if (!active) {
    return { action: "add-dsl", status: "error", error: "strategy_get_failed", message: stratErr ?? "Strategy not active or not found" };
  }
  const { data: chData, error: chErr } = await getClearinghouse(wallet!);
  if (chErr || !chData) {
    return { action: "add-dsl", status: "error", error: "clearinghouse_failed", message: chErr ?? "No data" };
  }
  const pos = getPositionFromClearinghouse(chData, assetNorm);
  if (!pos) {
    return { action: "add-dsl", status: "error", error: "position_not_found", message: `no open position for asset ${assetNorm}`, asset: assetNorm };
  }
  const entryPrice = Number(pos.entryPx ?? 0);
  const szi = Number(pos.szi ?? 0);
  const size = Math.abs(szi);
  if (entryPrice <= 0 || size <= 0) {
    return { action: "add-dsl", status: "error", error: "invalid_position_data", message: "entryPx and size must be > 0" };
  }
  const lev = Math.max(1, leverage);
  let margin = marginOpt;
  if (margin == null || margin <= 0) {
    margin = (entryPrice * size) / lev;
  }
  if (margin <= 0) {
    return { action: "add-dsl", status: "error", error: "invalid_margin", message: "margin must be > 0 (or omit to derive from position)" };
  }

  const state = buildStateForAddDsl(
    assetNorm, direction, lev, margin, entryPrice, size, wallet!, strategyId, preset, configJson
  );
  const stateFile = getStateFilePath(stateDir, strategyId, assetNorm);
  try {
    await readFile(stateFile);
    return { action: "add-dsl", status: "error", error: "state_file_exists", message: "State file already exists; overwrite not allowed", state_file: stateFile };
  } catch {
    // file does not exist, ok
  }
  try {
    await mkdir(dirname(stateFile), { recursive: true });
    await writeStateFile(stateFile, state);
  } catch (e) {
    return { action: "add-dsl", status: "error", error: "write_failed", message: String(e), state_file: stateFile };
  }

  const existing = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);
  const isFirst = existing.length === 1;
  return {
    action: "add-dsl",
    status: "ok",
    preset: state.preset,
    asset: assetNorm,
    strategy_id: strategyId,
    state_file: stateFile,
    is_first_position_for_strategy: isFirst,
    cron_needed: isFirst,
    cron_env: { DSL_STATE_DIR: stateDir, DSL_STRATEGY_ID: strategyId, DSL_PRESET: String(state.preset) },
    cron_schedule: DEFAULT_SCHEDULE,
  };
}

export type UpdateDslOptions = {
  stateDir: string;
  strategyId: string;
  config: string;
  asset?: string | null;
  dex?: string | null;
};

export async function updateDsl(options: UpdateDslOptions): Promise<Record<string, unknown>> {
  const { stateDir, strategyId, config, asset, dex } = options;
  if (!strategyId?.trim()) {
    return { action: "update-dsl", status: "error", error: "strategy_id_required" };
  }
  let configJson: Record<string, unknown>;
  try {
    const parsed = JSON.parse(config);
    if (typeof parsed !== "object" || parsed === null) throw new Error("config must be a JSON object");
    configJson = parsed;
  } catch (e) {
    return { action: "update-dsl", status: "error", error: "invalid_config", message: String(e) };
  }
  let files = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);
  if (asset != null && asset !== "") {
    const { canonicalAsset } = normalizeAssetDex(asset, dex);
    files = files.filter((f) => f.asset === canonicalAsset);
  }
  if (files.length === 0) {
    return { action: "update-dsl", status: "error", error: "no_state_files", message: "No matching state file(s)" };
  }
  let updated = 0;
  for (const { path } of files) {
    const state = await readStateFile<Record<string, unknown>>(path);
    if (!state) continue;
    deepMergeConfig(state, configJson);
    const p1 = state.phase1 as Record<string, unknown> | undefined;
    if (configJson.phase1 && p1 && p1.absoluteFloor == null) {
      const entry = state.entryPrice as number | undefined;
      const lev = Math.max(1, Number(state.leverage ?? 1));
      const isLong = String(state.direction ?? "LONG").toUpperCase() === "LONG";
      if (entry != null) {
        const ret = Number(p1.retraceThreshold ?? DEFAULT_PHASE1_RETRACE);
        p1.absoluteFloor = isLong
          ? Math.round(entry * (1 - ret / lev) * 1e4) / 1e4
          : Math.round(entry * (1 + ret / lev) * 1e4) / 1e4;
      }
    }
    try {
      await writeStateFile(path, state);
      updated++;
    } catch {
      // ignore
    }
  }
  return { action: "update-dsl", status: "ok", strategy_id: strategyId, updated_count: updated };
}

export type PauseResumeDslOptions = {
  stateDir: string;
  strategyId: string;
  asset?: string | null;
  dex?: string | null;
  active: boolean;
};

export async function pauseResumeDsl(options: PauseResumeDslOptions): Promise<Record<string, unknown>> {
  const { stateDir, strategyId, asset, dex, active } = options;
  const name = active ? "resume-dsl" : "pause-dsl";
  if (!strategyId?.trim()) {
    return { action: name, status: "error", error: "strategy_id_required" };
  }
  let files = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);
  if (asset != null && asset !== "") {
    const { canonicalAsset } = normalizeAssetDex(asset, dex);
    files = files.filter((f) => f.asset === canonicalAsset);
  }
  for (const { path } of files) {
    const state = await readStateFile<Record<string, unknown>>(path);
    if (!state) continue;
    state.active = active;
    await writeStateFile(path, state).catch(() => {});
  }
  return { action: name, status: "ok", strategy_id: strategyId, active };
}

export type DeleteDslOptions = {
  stateDir: string;
  strategyId: string;
  asset?: string | null;
  dex?: string | null;
};

export async function deleteDsl(options: DeleteDslOptions): Promise<Record<string, unknown>> {
  const { stateDir, strategyId, asset, dex } = options;
  if (!strategyId?.trim()) {
    return { action: "delete-dsl", status: "error", error: "strategy_id_required" };
  }
  let files = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);
  if (asset != null && asset !== "") {
    const { canonicalAsset } = normalizeAssetDex(asset, dex);
    files = files.filter((f) => f.asset === canonicalAsset);
  }
  let deleted = 0;
  for (const { path } of files) {
    try {
      await archiveStateFile(path, stateDir, strategyId);
      deleted++;
    } catch {
      // ignore
    }
  }
  const strategyDir = join(stateDir, strategyId);
  try {
    const names = await readdir(strategyDir);
    if (names.length === 0) {
      await rmdir(strategyDir);
    }
  } catch {
    // ignore
  }
  return { action: "delete-dsl", status: "ok", strategy_id: strategyId, deleted_count: deleted };
}

export type StatusDslOptions = {
  stateDir: string;
  strategyId: string;
  asset?: string | null;
  dex?: string | null;
};

export async function statusDsl(options: StatusDslOptions): Promise<Record<string, unknown>> {
  const { stateDir, strategyId, asset, dex } = options;
  if (!strategyId?.trim()) {
    return { action: "status-dsl", status: "error", error: "strategy_id_required" };
  }
  let files = await listStrategyStateFiles(stateDir, strategyId, readdir, stat);
  if (asset != null && asset !== "") {
    const { canonicalAsset } = normalizeAssetDex(asset, dex);
    files = files.filter((f) => f.asset === canonicalAsset);
  }
  if (files.length === 0) {
    return { action: "status-dsl", status: "ok", strategy_id: strategyId, positions: [] };
  }
  const positions: Record<string, unknown>[] = [];
  for (const { path } of files) {
    const state = await readStateFile<Record<string, unknown>>(path);
    if (state) positions.push(state);
  }
  return { action: "status-dsl", status: "ok", strategy_id: strategyId, positions };
}
