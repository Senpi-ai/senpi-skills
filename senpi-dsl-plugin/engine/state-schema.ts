export const DEFAULT_PHASE1_RETRACE = 0.03;
export const DEFAULT_PHASE1_BREACHES = 1;
export const DEFAULT_PHASE2_RETRACE = 0.015;
export const DEFAULT_PHASE2_BREACHES = 1;

export const DEFAULT_TIERS = [
  { triggerPct: 10, lockPct: 5 },
  { triggerPct: 20, lockPct: 14 },
  { triggerPct: 30, lockPct: 22, retrace: 0.012 },
  { triggerPct: 50, lockPct: 40, retrace: 0.01 },
  { triggerPct: 75, lockPct: 60, retrace: 0.008 },
  { triggerPct: 100, lockPct: 80, retrace: 0.006 },
];

const CONFIGURABLE_KEYS = new Set([
  "phase1", "phase2", "phase2TriggerTier", "tiers",
  "breachDecay", "closeRetries", "closeRetryDelaySec", "maxFetchFailures",
]);

export type TierDef = { triggerPct: number; lockPct: number; retrace?: number; breachesRequired?: number };

export function deepMergeConfig(
  base: Record<string, unknown>,
  override: Record<string, unknown>
): void {
  for (const key of Object.keys(override)) {
    if (!CONFIGURABLE_KEYS.has(key)) continue;
    const val = override[key];
    if (key === "tiers") {
      if (Array.isArray(val)) base[key] = [...val];
      continue;
    }
    if ((key === "phase1" || key === "phase2") && val && typeof val === "object" && !Array.isArray(val)) {
      if (!(base[key] && typeof base[key] === "object")) (base as Record<string, unknown>)[key] = {};
      const target = (base as Record<string, unknown>)[key] as Record<string, unknown>;
      for (const k of Object.keys(val as Record<string, unknown>)) {
        target[k] = (val as Record<string, unknown>)[k];
      }
      continue;
    }
    (base as Record<string, unknown>)[key] = val;
  }
}

export function normalizeStatePhaseConfig(state: Record<string, unknown>): boolean {
  let changed = false;
  if (!state.phase1 || typeof state.phase1 !== "object") {
    state.phase1 = {};
    changed = true;
  }
  const p1 = state.phase1 as Record<string, unknown>;
  if (p1.retraceThreshold == null) {
    p1.retraceThreshold = DEFAULT_PHASE1_RETRACE;
    changed = true;
  }
  if (p1.consecutiveBreachesRequired == null) {
    p1.consecutiveBreachesRequired = DEFAULT_PHASE1_BREACHES;
    changed = true;
  }
  if (p1.absoluteFloor == null) {
    const entry = state.entryPrice as number | undefined;
    const lev = Math.max(1, Number(state.leverage ?? 1));
    const isLong = String(state.direction ?? "LONG").toUpperCase() === "LONG";
    if (entry != null) {
      const ret = Number(p1.retraceThreshold ?? DEFAULT_PHASE1_RETRACE);
      p1.absoluteFloor = isLong
        ? Math.round(entry * (1 - ret / lev) * 1e4) / 1e4
        : Math.round(entry * (1 + ret / lev) * 1e4) / 1e4;
    } else {
      p1.absoluteFloor = 0;
    }
    changed = true;
  }

  if (!state.phase2 || typeof state.phase2 !== "object") {
    state.phase2 = {};
    changed = true;
  }
  const p2 = state.phase2 as Record<string, unknown>;
  if (p2.retraceThreshold == null) {
    p2.retraceThreshold = DEFAULT_PHASE2_RETRACE;
    changed = true;
  }
  if (p2.consecutiveBreachesRequired == null) {
    p2.consecutiveBreachesRequired = DEFAULT_PHASE2_BREACHES;
    changed = true;
  }
  return changed;
}

export function buildStateForAddDsl(
  asset: string,
  direction: string,
  leverage: number,
  margin: number,
  entryPrice: number,
  size: number,
  wallet: string,
  strategyId: string,
  preset: string,
  configJson: Record<string, unknown> | null
): Record<string, unknown> {
  const lev = Math.max(1, leverage);
  const isLong = direction.toUpperCase() === "LONG";
  const retraceRoe = DEFAULT_PHASE1_RETRACE;
  const absFloor = isLong
    ? Math.round(entryPrice * (1 - retraceRoe / lev) * 1e4) / 1e4
    : Math.round(entryPrice * (1 + retraceRoe / lev) * 1e4) / 1e4;

  const state: Record<string, unknown> = {
    phase1: {
      retraceThreshold: DEFAULT_PHASE1_RETRACE,
      consecutiveBreachesRequired: DEFAULT_PHASE1_BREACHES,
      absoluteFloor: absFloor,
    },
    phase2: {
      retraceThreshold: DEFAULT_PHASE2_RETRACE,
      consecutiveBreachesRequired: DEFAULT_PHASE2_BREACHES,
    },
    phase2TriggerTier: 0,
    tiers: DEFAULT_TIERS.map((t) => ({ ...t })),
    breachDecay: "hard",
    closeRetries: 2,
    closeRetryDelaySec: 3,
    maxFetchFailures: 10,
  };
  if (configJson && typeof configJson === "object") {
    deepMergeConfig(state, configJson);
  }
  const p1 = state.phase1 as Record<string, unknown>;
  if (p1 && (p1.absoluteFloor == null || p1.absoluteFloor === 0)) {
    p1.absoluteFloor = absFloor;
  }
  state.active = true;
  state.asset = asset;
  state.direction = direction.toUpperCase();
  state.leverage = lev;
  state.entryPrice = Math.round(entryPrice * 1e4) / 1e4;
  state.size = Math.round(size * 1e4) / 1e4;
  state.margin = Math.round(margin * 1e4) / 1e4;
  state.wallet = wallet;
  state.strategyId = strategyId;
  state.preset = preset || "default";
  state.phase = 1;
  state.highWaterPrice = Math.round(entryPrice * 1e4) / 1e4;
  state.floorPrice = (p1?.absoluteFloor as number) ?? absFloor;
  state.currentTierIndex = -1;
  state.tierFloorPrice = null;
  state.currentBreachCount = 0;
  state.consecutiveFetchFailures = 0;
  state.pendingClose = false;
  state.lastCheck = null;
  state.lastPrice = null;
  state.createdAt = new Date().toISOString().replace(/\.\d{3}Z$/, ".000Z");
  return state;
}
