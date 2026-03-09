// ─── Utility ───

export function roundTo(value: number, decimals: number): number {
  const factor = Math.pow(10, decimals);
  return Math.round(value * factor) / factor;
}

// ─── DSL Types ───

export interface DslTier {
  triggerPct: number;
  lockPct: number;
  /** Number of consecutive breaches required to close. Aliases: breachesRequired, retraceClose */
  breaches?: number;
  breachesRequired?: number;
  retraceClose?: number;
  /** Per-tier retrace percentage (Phase 2). Falls back to phase2.retraceFromHW if absent. */
  retrace?: number;
}

export interface DslPhase1Config {
  /** Retrace threshold as ROE percentage (e.g. 10 means 10% ROE). Always positive in config. */
  retraceThreshold: number;
  /** Absolute price floor — max loss boundary */
  absoluteFloor: number;
  /** Number of consecutive breaches needed to close in Phase 1 */
  consecutiveBreachesRequired: number;
}

export interface DslPhase2Config {
  /** Default retrace percentage from high-water for Phase 2 (e.g. 5 = 5%) */
  retraceFromHW?: number;
  /** Consecutive breaches required in Phase 2 (fallback when tier doesn't specify) */
  consecutiveBreachesRequired?: number;
}

export interface DslStagnationConfig {
  enabled: boolean;
  /** Minimum ROE% to qualify for stagnation check */
  minROE: number;
  /** Hours the high-water must be stale before triggering stagnation */
  thresholdHours: number;
  /** Max price distance from HW (%) to still count as "stagnating" */
  priceRangePct: number;
}

export interface DslState {
  version?: number;
  asset: string;
  direction: 'LONG' | 'SHORT';
  entryPrice: number;
  size: number;
  leverage: number;
  highWaterPrice: number;
  hwTimestamp?: string;
  phase: 1 | 2;
  currentBreachCount: number;
  currentTierIndex: number | null;
  tierFloorPrice: number;
  floorPrice: number;
  tiers: DslTier[];
  phase1: DslPhase1Config;
  phase2?: DslPhase2Config;
  phase2TriggerTier?: number;
  stagnation?: DslStagnationConfig;
  breachDecay?: 'hard' | 'soft';
  pendingClose?: boolean;
  active?: boolean;
  createdAt?: string;
  closedAt?: string;
  closeReason?: string;
  lastCheck?: string;
  lastPrice?: number;
  peakROE?: number;
  wallet?: string;
  dex?: string;
  strategyKey?: string;
  approximate?: boolean;
  createdBy?: string;
  consecutiveFetchFailures?: number;
  maxFetchFailures?: number;
}

/** Strategy-level DSL config (from registry, not per-position state) */
export interface DslConfig {
  preset?: string;
  tiers?: DslTier[];
  /** Phase 1 hard cap in minutes (default: 90) */
  phase1MaxMinutes?: number;
  /** Weak peak early cut threshold in minutes (default: 45) */
  weakPeakCutMinutes?: number;
  /** Peak ROE below this = "weak peak" (default: 3.0%) */
  weakPeakThreshold?: number;
}

export interface DslTickResult {
  action: 'hold' | 'close';
  closeReason?: string;
  tierChanged: boolean;
  previousTierIndex: number | null;
  newTierIndex: number | null;
  phaseChanged: boolean;
  newPhase: 1 | 2;
  stagnationTriggered: boolean;
  phase1Autocut: boolean;
  phase1AutocutReason?: string;
  updatedState: DslState;
  metrics: {
    upnl: number;
    upnlPct: number;
    retraceFromHw: number;
    effectiveFloor: number;
    trailingFloor: number;
    tierFloor: number;
    breachCount: number;
    breachesNeeded: number;
    breached: boolean;
    elapsedMinutes: number;
    peakROE: number;
    highWater: number;
    phase: 1 | 2;
  };
}

// ─── Strategy Types ───

export type TradingRisk = 'conservative' | 'moderate' | 'aggressive';

export interface StrategyConfig {
  wallet: string;
  strategyId: string;
  budget: number;
  slots: number;
  tradingRisk: TradingRisk;
  dslPreset: string;
  marginPerSlot?: number;
  defaultLeverage?: number;
  dailyLossLimit?: number;
  autoDeleverThreshold?: number;
  dsl?: DslConfig;
  guardRails?: GuardRailConfig;
  enabled?: boolean;
  /** Injected at runtime */
  _key?: string;
  _stateDir?: string;
}

export interface GuardRailConfig {
  maxEntriesPerDay?: number;
  bypassOnProfit?: boolean;
  maxConsecutiveLosses?: number;
  cooldownMinutes?: number;
}

// ─── Gate / Risk Types ───

export type GateStatus = 'OPEN' | 'COOLDOWN' | 'CLOSED';

export interface GateState {
  gate: GateStatus;
  reason?: string;
}

export interface TradeCounter {
  date: string;
  accountValueStart: number | null;
  entries: number;
  closedTrades: number;
  realizedPnl: number;
  gate: GateStatus;
  gateReason: string | null;
  cooldownUntil: string | null;
  lastResults: ('W' | 'L' | 'R')[];
  processedOrderIds: string[];
  updatedAt: string | null;
  maxEntriesPerDay: number;
  bypassOnProfit: boolean;
  maxConsecutiveLosses: number;
  cooldownMinutes: number;
}

// ─── Scanner Types ───

/** Metadata for Emerging Movers scanner signals */
export interface EmergingMoversSignalMetadata {
  scannerType: 'emerging_movers';
  currentRank: number;
  contribution: number;
  contribVelocity: number;
  traders: number;
  priceChg4h: number;
  maxLeverage: number | null;
  rankHistory: (number | null)[];
  contribHistory: (number | null)[];
  isFirstJump: boolean;
  isContribExplosion: boolean;
  isImmediate: boolean;
  isDeepClimber: boolean;
  erratic: boolean;
  lowVelocity: boolean;
  rankJumpThisScan: number;
}

/** Union of all scanner signal metadata types — grows as scanners are added */
export type SignalMetadata = EmergingMoversSignalMetadata; // | FundingRateMetadata | ...

/** Base signal produced by ANY scanner */
export interface Signal<M extends SignalMetadata = SignalMetadata> {
  scannerType: string;
  signalType: string;
  signalPriority: number;
  conviction: number;
  token: string;
  dex: string | null;
  qualifiedAsset: string;
  direction: string;
  reasons: string[];
  metadata: M;
}

/** Metadata for Emerging Movers scan results */
export interface EmergingMoversScanMetadata {
  scannerType: 'emerging_movers';
  hasFirstJump: boolean;
  hasImmediate: boolean;
  hasContribExplosion: boolean;
}

/** Union of all scan result metadata types — grows as scanners are added */
export type ScanResultMetadata = EmergingMoversScanMetadata; // | FundingRateScanMetadata | ...

/** Full scan result passed to on_signal_detected hook (batch) */
export interface ScanResult<M extends ScanResultMetadata = ScanResultMetadata> {
  scannerType: string;
  signals: Signal[];
  topPicks: Signal[];
  strategySlots: Record<string, StrategySlotInfo>;
  anySlotsAvailable: boolean;
  totalAvailableSlots: number;
  scansInHistory: number;
  metadata: M;
}

/** Scanner interface — all scanners implement this */
export interface Scanner extends BackgroundService {
  readonly scannerType: string;
}

export interface StrategySlotInfo {
  name: string;
  slots: number;
  used: number;
  available: number;
  dslActive: number;
  onChain: number;
  onChainCoins: string[];
  slotAges: { coin: string; ageMinutes: number | null }[];
  rotationEligibleCoins: string[];
  hasRotationCandidate: boolean;
  gate: GateStatus;
  gateReason: string | null;
}

// ─── Hook Types ───

export type HookEventType =
  | 'on_signal_detected'
  | 'on_position_opened'
  | 'on_position_closed'
  | 'on_tier_changed'
  | 'on_daily_limit_hit'
  | 'on_drawdown_cap_hit'
  | 'on_consecutive_losses'
  | 'on_position_at_risk'
  | 'on_schedule';

export interface HookConfig {
  action: string;
  decision_mode: 'llm' | 'script' | 'agent' | 'none';
  decision_prompt?: string;
  decision_model?: string;
  context?: string[];
  notify?: boolean;
  wake_agent?: boolean;
  threshold?: number;
  cron?: string;
}

export interface HookEvent {
  type: HookEventType;
  skillName: string;
  strategyKey?: string;
  data: Record<string, unknown>;
  timestamp: string;
}

// ─── Skill Config Types (from skill.yaml) ───

export interface SkillYamlConfig {
  name: string;
  version: string;
  description?: string;
  strategies: Record<string, SkillStrategyConfig>;
  scanner: SkillScannerConfig;
  entry: SkillEntryConfig;
  exit: SkillExitConfig;
  risk: SkillRiskConfig;
  hooks: Record<string, HookConfig>;
  notifications: SkillNotificationConfig;
}

export interface SkillStrategyConfig {
  wallet: string;
  strategy_id: string;
  budget: string | number;
  slots: number;
  trading_risk: TradingRisk;
  dsl_preset: string;
}

export interface SkillScannerConfig {
  type: string;
  interval: string;
  config: Record<string, unknown>;
  blocked_assets: string[];
}

export interface SkillEntryConfig {
  decision_mode: 'llm' | 'agent' | 'none';
  decision_model?: string;
  decision_prompt: string;
  context: string[];
  min_confidence: number;
}

export interface SkillExitConfig {
  engine: string;
  dsl_presets: Record<string, DslPresetConfig>;
  sm_flip: SmFlipConfig;
}

export interface DslPresetConfig {
  tiers: { trigger_pct: number; lock_pct: number; breaches: number }[];
  max_loss_pct: number;
  stagnation: { enabled: boolean; min_roe: number; timeout_minutes: number };
}

export interface SmFlipConfig {
  enabled: boolean;
  interval: string;
  conviction_collapse: {
    from_min: number;
    to_max: number;
    window_minutes: number;
    action: string;
  };
  dead_weight: { conviction_zero_action: string };
}

export interface SkillRiskConfig {
  per_strategy: {
    daily_loss_limit_pct: number;
    margin_buffer_pct: number;
    auto_delever: { enabled: boolean; threshold_pct: number; reduce_to: number };
  };
  guard_rails: {
    max_entries_per_day: number;
    bypass_on_profit: boolean;
    max_consecutive_losses: number;
    cooldown_minutes: number;
  };
  leverage: {
    aggressive_cap_pct: number;
    moderate_cap_pct: number;
    conservative_cap_pct: number;
  };
  directional_guard: { max_same_direction: number };
  rotation_cooldown_minutes: number;
}

export interface SkillNotificationConfig {
  telegram_chat_id: string;
}

// ─── MCP Response Types ───

export interface ClearinghouseState {
  main: ClearinghouseSection;
  xyz: ClearinghouseSection;
}

export interface ClearinghouseSection {
  marginSummary: {
    accountValue: string;
    totalMarginUsed: string;
  };
  crossMaintenanceMarginUsed: number;
  withdrawable: string;
  assetPositions: AssetPosition[];
}

export interface AssetPosition {
  position: {
    coin: string;
    szi: string;
    entryPx: string;
    positionValue: string;
    marginUsed: string;
    unrealizedPnl: string;
    returnOnEquity: string;
    liquidationPx: string | null;
  };
}

export interface LeaderboardMarket {
  token: string;
  dex?: string;
  direction: string;
  pct_of_top_traders_gain: number;
  trader_count: number;
  token_price_change_pct_4h?: number;
  avgAtPeak?: number;
  nearPeakPct?: number;
}

// ─── Order / Position Types (MCP) ───

export type OrderType = 'MARKET' | 'LIMIT' | 'FEE_OPTIMIZED_LIMIT';
export type CloseOrderType = 'MARKET' | 'FEE_OPTIMIZED_LIMIT';
export type SlTpOrderType = 'MARKET' | 'LIMIT';
export type LeverageType = 'CROSS' | 'ISOLATED';
export type TimeInForce = 'GTC' | 'IOC' | 'ALO';

export interface SlTpConfig {
  percentage?: number;
  price?: number;
  orderType?: SlTpOrderType;
}

export interface CreateOrderParams {
  coin: string;
  direction: 'LONG' | 'SHORT';
  leverage: number;
  marginAmount: number;
  orderType: OrderType;
  leverageType?: LeverageType;
  limitPrice?: number;
  timeInForce?: TimeInForce;
  slippagePercent?: number;
  ensureExecutionAsTaker?: boolean;
  takeProfit?: SlTpConfig;
  stopLoss?: SlTpConfig;
}

export interface CreatePositionResult {
  results: Array<{
    coin: string;
    status: string;
    orderId?: number;
    entryPrice?: number;
    filledSize?: number;
    executionAsMaker?: boolean;
    slOrderId?: number;
    tpOrderId?: number;
    error?: string;
  }>;
}

export interface ClosePositionResult {
  success: boolean;
  closedPrice?: number;
  closedSize?: number;
  cancelledSlTpOrders?: number[];
  executionAsMaker?: boolean;
  result: string;
}

export interface EditPositionParams {
  coin: string;
  direction?: 'LONG' | 'SHORT';
  leverage?: number;
  leverageType?: LeverageType;
  targetMargin?: number;
  orderType?: OrderType;
  slippagePercent?: number;
  ensureExecutionAsTaker?: boolean;
  takeProfit?: SlTpConfig;
  stopLoss?: SlTpConfig;
  reason?: string;
}

export interface EditPositionResult {
  actionsPerformed: string[];
  ordersUpdated?: Record<string, unknown>;
  executionAsMaker?: boolean;
}

export interface OpenOrder {
  coin: string;
  side: string;
  sz: string;
  limitPx: string;
  orderType: string;
  triggerPx?: string;
  triggerCondition?: string;
  reduceOnly: boolean;
  oid: number;
  timestamp: number;
}

export interface TradingLimits {
  coin: string;
  leverage: {
    type: LeverageType;
    value: number;
  };
  maxLeverage: number;
  maxLongSize: number;
  maxShortSize: number;
  availableToTrade: number;
  markPrice: number;
}

export interface OrderStatus {
  orderId: number;
  status: string;
  coin: string;
  side: string;
  sz: string;
  limitPx: string;
  filledSz?: string;
  avgPx?: string;
}

export interface InstrumentInfo {
  name: string;
  szDecimals: number;
  maxLeverage: number;
  dex?: string;
  markPx?: number;
  midPx?: number;
  fundingRate?: string;
  openInterest?: string;
  dayNtlVlm?: string;
  prevDayPx?: string;
  onlyIsolated?: boolean;
}

export interface MarketAssetData {
  assetCtx: Record<string, unknown>;
  candles?: Record<string, Array<{
    t: number;
    o: number;
    h: number;
    l: number;
    c: number;
    v: number;
  }>>;
  orderBook?: {
    bids: Array<[string, string]>;
    asks: Array<[string, string]>;
  };
  funding?: Array<{
    time: number;
    coin: string;
    fundingRate: string;
    premium: string;
  }>;
}

export interface ClosedPosition {
  coin: string;
  direction: string;
  entryPx: number;
  closedPx: number;
  szi: number;
  leverage: number;
  realizedPnl: number;
  totalFees: number;
  totalFills: number;
  marginUsed: number;
  openTime: number;
  closeTime: number;
  openOrderId?: string;
  closedOrderId?: string;
}

export interface PortfolioData {
  total_balance_usd: number;
  total_allocated_in_strategy: number;
  total_withdrawable: number;
  total_spot_usd_in_hyperliquid: number;
  positions: Array<{
    coin: string;
    szi: string;
    entryPx: string;
    unrealizedPnl: string;
    returnOnEquity: string;
    strategyId?: string;
    strategyWalletAddress?: string;
  }>;
  token_balances: Array<{
    token: string;
    balance: string;
    chain?: string;
  }>;
  total_count: number;
}

export interface OpenPositionDetails {
  coin: string;
  direction: string;
  szi: string;
  entryPx: string;
  leverage: number;
  marginUsed: string;
  unrealizedPnl: string;
  returnOnEquity: string;
  liquidationPx: string | null;
  fundingRate?: string;
  orders?: Array<{
    orderId: number;
    side: string;
    sz: string;
    limitPx: string;
    orderType: string;
    timestamp: number;
  }>;
}

// ─── Service Types ───

export interface BackgroundService {
  name: string;
  intervalMs: number;
  tick(): Promise<void>;
}

export interface ServiceHealth {
  name: string;
  lastTickTime: string | null;
  lastTickDurationMs: number;
  consecutiveFailures: number;
  isRunning: boolean;
}

// ─── Position Types ───

export interface PositionOpenRequest {
  strategyKey: string;
  asset: string;
  direction: 'LONG' | 'SHORT';
  conviction: number;
  signalType: string;
  leverage?: number;
  marginOverride?: number;
  rotateOut?: string;
}

export interface PositionOpenResult {
  success: boolean;
  asset: string;
  direction: string;
  entryPrice: number;
  size: number;
  leverage: number;
  approximate: boolean;
  strategyKey: string;
  notification: string;
}

// ─── Leverage Calculation Types ───

export const RISK_LEVERAGE_RANGES: Record<TradingRisk, [number, number]> = {
  conservative: [0.15, 0.25],
  moderate: [0.25, 0.50],
  aggressive: [0.50, 0.75],
};

export const SIGNAL_CONVICTION: Record<string, number> = {
  FIRST_JUMP: 0.9,
  CONTRIB_EXPLOSION: 0.8,
  IMMEDIATE_MOVER: 0.7,
  NEW_ENTRY_DEEP: 0.7,
  DEEP_CLIMBER: 0.5,
};

export const ROTATION_COOLDOWN_MINUTES = 45;

// ─── LLM Decision Types ───

export interface LlmDecisionRequest {
  prompt: string;
  context: Record<string, unknown>;
  model?: string;
  maxTokens?: number;
  temperature?: number;
}

export interface LlmDecisionResponse<T = unknown> {
  decision: T;
  rawResponse: string;
  tokensUsed: number;
  latencyMs: number;
}

// ─── Entry Decision Types ───

export interface EntryDecision {
  enter: boolean;
  target_strategy: string;
  direction: 'LONG' | 'SHORT';
  confidence: number;
  reasoning: string;
  rotate_out: string | null;
}

// ─── Logger Types ───

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEntry {
  level: LogLevel;
  message: string;
  timestamp: string;
  context?: Record<string, unknown>;
}
