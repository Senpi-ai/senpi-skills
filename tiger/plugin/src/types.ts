// Tiger Plugin TypeScript types
// Mirrors Python state schemas from tiger_config.py

// ============================================================
// Plugin Configuration
// ============================================================

export interface TigerPluginConfig {
  workspace: string;
  pythonPath: string;
  scriptTimeout: number;
  dslTickInterval: number;
}

export interface TigerPaths {
  workspace: string;
  scriptsDir: string;
  stateDir: string;
  configFile: string;
}

// ============================================================
// Python Bridge
// ============================================================

export interface ScriptResult<T> {
  success: boolean;
  data?: T;
  error?: string;
  stderr?: string;
  exitCode: number;
}

export interface ScriptRunOptions {
  env?: Record<string, string>;
  timeout?: number;
}

// ============================================================
// Tiger Config (tiger-config.json)
// ============================================================

export interface MinConfluenceScore {
  CONSERVATIVE: number;
  NORMAL: number;
  ELEVATED: number;
  ABORT: number;
}

export interface TrailingLockPct {
  CONSERVATIVE: number;
  NORMAL: number;
  ELEVATED: number;
  ABORT: number;
}

export interface TigerConfig {
  version: number;
  budget: number;
  target: number;
  deadlineDays: number;
  startTime: string | null;
  strategyId: string | null;
  strategyWallet: string | null;
  telegramChatId: string | null;
  maxSlots: number;
  maxLeverage: number;
  minLeverage: number;
  maxSingleLossPct: number;
  maxDailyLossPct: number;
  maxDrawdownPct: number;
  bbSqueezePercentile: number;
  minOiChangePct: number;
  rsiOverbought: number;
  rsiOversold: number;
  minFundingAnnualizedPct: number;
  btcCorrelationMovePct: number;
  oiCollapseThresholdPct: number;
  oiReduceThresholdPct: number;
  minConfluenceScore: MinConfluenceScore;
  trailingLockPct: TrailingLockPct;
}

// ============================================================
// Safety State
// ============================================================

export interface SafetyState {
  halted: boolean;
  haltReason: string | null;
  dailyLossPct: number;
  tradesToday: number;
}

// ============================================================
// Active Position
// ============================================================

export interface ActivePosition {
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  leverage: number;
  sizeUsd: number;
  confluenceScore: number;
  openedAt: string;
  dslStateFile: string;
  highWaterRoe: number;
  prevHighWater: number;
  stagnantChecks: number;
  breakoutCandleIndex: number;
  candlesSinceBreakout: number;
  bbReentry: boolean;
}

// ============================================================
// Tiger State (tiger-state.json)
// ============================================================

export type Aggression = 'CONSERVATIVE' | 'NORMAL' | 'ELEVATED' | 'ABORT';

export interface TigerState {
  version: number;
  active: boolean;
  instanceKey: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  currentBalance: number;
  peakBalance: number;
  dayStartBalance: number;
  dailyPnl: number;
  totalPnl: number;
  tradesToday: number;
  winsToday: number;
  totalTrades: number;
  totalWins: number;
  aggression: Aggression;
  dailyRateNeeded: number;
  daysRemaining: number;
  dayNumber: number;
  activePositions: Record<string, ActivePosition>;
  safety: SafetyState;
  lastGoalRecalc: string | null;
  lastBtcPrice: number | null;
  lastBtcCheck: string | null;
}

// ============================================================
// Tiger Pattern & DSL Creation
// ============================================================

export type TigerPattern =
  | 'COMPRESSION_BREAKOUT'
  | 'CORRELATION_LAG'
  | 'MOMENTUM_BREAKOUT'
  | 'MEAN_REVERSION'
  | 'FUNDING_ARB';

export interface CreateDslParams {
  asset: string;
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  size: number;
  leverage: number;
  wallet: string;
  pattern: TigerPattern;
  absoluteFloor?: number;
}

// ============================================================
// DSL State (dsl-{ASSET}.json)
// ============================================================

export interface DslTier {
  triggerPct: number;
  lockPct: number;
  retrace: number;
}

export interface DslPhase1 {
  retraceThreshold: number;
  consecutiveBreachesRequired: number;
  absoluteFloor: number;
}

export interface DslPhase2 {
  retraceThreshold: number;
  consecutiveBreachesRequired: number;
}

export interface DslState {
  active: boolean;
  asset: string;
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  size: number;
  leverage: number;
  wallet: string;
  highWaterPrice: number;
  phase: number;
  currentBreachCount: number;
  currentTierIndex: number;
  tierFloorPrice: number | null;
  pendingClose: boolean;
  phase1: DslPhase1;
  phase2: DslPhase2;
  phase2TriggerTier: number;
  tiers: DslTier[];
  breachDecay: 'soft' | 'hard';
  createdAt: string;
  updatedAt: string;
  lastCheck: string;
  lastPrice: number;
  consecutiveFetchFailures: number;
  closedAt?: string;
  closeReason?: string;
}

// ============================================================
// DSL State Summary (for list mode)
// ============================================================

export interface DslStateSummary {
  asset: string;
  active: boolean;
  phase: number;
  tierIndex: number;
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  lastPrice: number;
  highWaterPrice: number;
}

// ============================================================
// Trade Log Entry
// ============================================================

export interface TradeLogEntry {
  version: number;
  timestamp: string;
  asset: string;
  pattern: string;
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  exitPrice: number;
  leverage: number;
  sizeUsd: number;
  pnlUsd: number;
  feesUsd: number;
  holdMinutes: number;
  exitReason: string;
  confluenceScore: number;
  aggression: Aggression;
}

// ============================================================
// DSL Tick Result (JSON output from dsl-v4.py)
// ============================================================

export interface DslTickResult {
  success: boolean;
  heartbeat?: string;
  processed?: number;
  results?: Array<Record<string, unknown>>;
  error?: string;
}
