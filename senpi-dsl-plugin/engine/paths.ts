import { join } from "path";

export function assetToFilename(asset: string): string {
  if (asset.startsWith("xyz:")) return asset.replace(":", "--");
  return asset;
}

export function filenameToAsset(filename: string): string | null {
  if (!filename.endsWith(".json")) return null;
  const base = filename.slice(0, -5);
  if (base.includes("--") && !base.startsWith("xyz--")) return null;
  if (base.startsWith("xyz--")) return "xyz:" + base.slice(5);
  return base;
}

export async function listStrategyStateFiles(
  stateDir: string,
  strategyId: string,
  readdir: (path: string) => Promise<string[]>,
  stat: (path: string) => Promise<{ isFile: () => boolean }>
): Promise<Array<{ path: string; asset: string }>> {
  const strategyDir = join(stateDir, strategyId);
  let names: string[];
  try {
    names = await readdir(strategyDir);
  } catch {
    return [];
  }
  const out: Array<{ path: string; asset: string }> = [];
  for (const name of names) {
    if (!name.endsWith(".json")) continue;
    const path = join(strategyDir, name);
    try {
      const s = await stat(path);
      if (!s.isFile()) continue;
    } catch {
      continue;
    }
    const asset = filenameToAsset(name);
    if (asset != null) out.push({ path, asset });
  }
  return out;
}

export function dexAndLookupSymbol(asset: string): { dex: string; lookupSymbol: string } {
  if (asset.startsWith("xyz:")) {
    return { dex: "xyz", lookupSymbol: asset.split(":", 1)[1] ?? asset };
  }
  return { dex: "", lookupSymbol: asset };
}

export function normalizeAssetDex(asset: string, dex: string | null | undefined): { canonicalAsset: string; dex: string } {
  const a = (asset ?? "").trim();
  const d = (dex ?? "").trim().toLowerCase();
  if (a.startsWith("xyz:")) return { canonicalAsset: a, dex: "xyz" };
  if (d === "xyz") return { canonicalAsset: `xyz:${a}`, dex: "xyz" };
  return { canonicalAsset: a, dex: "" };
}

