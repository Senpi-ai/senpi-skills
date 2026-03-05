import { readdir } from "fs/promises";
import { join } from "path";
import { ARCHIVE_DIR_NAME } from "../constants.js";

/**
 * Discover strategy IDs that have at least one .json state file in {stateDir}/{strategyId}/.
 * Excludes the archive directory. Returns [] if stateDir does not exist.
 */
export async function discoverActiveStrategies(stateDir: string): Promise<string[]> {
  let entries: string[];
  try {
    entries = await readdir(stateDir, { withFileTypes: true });
  } catch (err: unknown) {
    const code = err && typeof err === "object" && "code" in err ? (err as NodeJS.ErrnoException).code : "";
    if (code === "ENOENT") {
      return [];
    }
    throw err;
  }

  const strategyIds: string[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name === ARCHIVE_DIR_NAME) {
      continue;
    }
    const strategyDir = join(stateDir, entry.name);
    const files = await readdir(strategyDir).catch(() => [] as string[]);
    const hasJson = files.some((f) => f.endsWith(".json"));
    if (hasJson) {
      strategyIds.push(entry.name);
    }
  }
  return strategyIds;
}
