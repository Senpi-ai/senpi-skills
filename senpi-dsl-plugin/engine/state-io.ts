import { rename, mkdir, readFile, writeFile } from "fs/promises";
import { dirname, join } from "path";
import { ARCHIVE_DIR_NAME } from "../constants.js";
import { assetToFilename } from "./paths.js";

export async function archiveStateFile(
  stateFilePath: string,
  stateDir: string,
  strategyId: string
): Promise<void> {
  const archiveDir = join(stateDir, ARCHIVE_DIR_NAME, strategyId);
  await mkdir(archiveDir, { recursive: true });
  const stem = stateFilePath.replace(/\.json$/i, "").split(/[/\\]/).pop() ?? "state";
  const ts = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "Z");
  const archivePath = join(archiveDir, `${stem}-${ts}.json`);
  await rename(stateFilePath, archivePath);
}

export async function readStateFile<T = Record<string, unknown>>(path: string): Promise<T | null> {
  try {
    const raw = await readFile(path, "utf8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export async function writeStateFile(path: string, state: Record<string, unknown>): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(state, null, 2), "utf8");
}

export async function saveOrDeleteState(
  state: Record<string, unknown>,
  stateFilePath: string,
  closed: boolean,
  stateDir: string,
  strategyId: string,
  writeState: (path: string, data: Record<string, unknown>) => Promise<void>,
  archiveState: (file: string, dir: string, stratId: string) => Promise<void>
): Promise<string | null> {
  state.lastCheck = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  if (closed) {
    try {
      await archiveState(stateFilePath, stateDir, strategyId);
      return state.closeResult != null ? String(state.closeResult) : null;
    } catch (e) {
      try {
        state.active = false;
        await writeState(stateFilePath, state);
      } catch {
        // ignore
      }
      return ((state.closeResult as string) ?? "") + `; archive_failed: ${e}`;
    }
  }
  await writeState(stateFilePath, state);
  return null;
}

export function getStateFilePath(stateDir: string, strategyId: string, asset: string): string {
  return join(stateDir, strategyId, `${assetToFilename(asset)}.json`);
}
