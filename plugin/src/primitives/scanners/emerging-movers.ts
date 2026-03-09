/**
 * Emerging Movers Scanner — Signal detection + operational layer.
 *
 * Ports: emerging-movers.py (511 lines)
 *
 * Tracks SM market concentration rank changes over time.
 * Flags assets accelerating up the ranks EARLY — catch at #50→#20, not #5.
 *
 * Signal types (by priority):
 * 1. FIRST_JUMP — Jumped 10+ ranks from #25+, wasn't in top 50 before
 * 2. CONTRIB_EXPLOSION — 3x+ contribution spike from rank #20+
 * 3. IMMEDIATE_MOVER — 10+ rank jump from #25+, not first time
 * 4. NEW_ENTRY_DEEP — New entry at rank #1-20
 * 5. DEEP_CLIMBER — Steady climb from #30+
 */

import {
  Scanner,
  Signal,
  EmergingMoversSignalMetadata,
  ScanResult,
  EmergingMoversScanMetadata,
  StrategySlotInfo,
  LeaderboardMarket,
  StrategyConfig,
  GateStatus,
  SIGNAL_CONVICTION,
  ROTATION_COOLDOWN_MINUTES,
} from '../../core/types.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { StateManager } from '../../core/state-manager.js';
import { HookSystem } from '../../core/hook-system.js';
import { RiskGuard } from '../risk/risk-guard.js';
import { logger } from '../../core/logger.js';
import { roundTo } from '../../core/types.js';

// ─── EM-local types (not exported from core) ───

export type EmergingMoversSignalType =
  | 'FIRST_JUMP'
  | 'CONTRIB_EXPLOSION'
  | 'IMMEDIATE_MOVER'
  | 'NEW_ENTRY_DEEP'
  | 'DEEP_CLIMBER';

export interface ScanSnapshot {
  time: string;
  markets: ScanMarket[];
}

export interface ScanMarket {
  token: string;
  dex: string;
  rank: number;
  direction: string;
  contribution: number;
  traders: number;
  price_chg_4h: number;
}

/** EM scanner filter config — parsed from generic config bag */
export interface EmergingMoversConfig {
  min_reasons?: number;
  max_rank?: number;
  require_immediate?: boolean;
  exclude_erratic?: boolean;
  min_traders?: number;
}

const MAX_HISTORY = 60;
const TOP_N = 50;
const RANK_CLIMB_THRESHOLD = 3;
const CONTRIBUTION_ACCEL_THRESHOLD = 0.003;
const MIN_SCANS_FOR_TREND = 2;
const MIN_VELOCITY_FOR_DEEP_CLIMBER = 0.0003; // 0.03%
const ERRATIC_REVERSAL_THRESHOLD = 5;
const APPROX_GRACE_MINUTES = 10;

/** Find a market in a scan snapshot by token+dex */
function getMarketInScan(
  scan: ScanSnapshot,
  token: string,
  dex: string,
): ScanMarket | null {
  return scan.markets.find((m) => m.token === token && (m.dex || '') === dex) ?? null;
}

/** Detect zigzag rank patterns */
export function isErraticHistory(
  rankHistory: (number | null)[],
  excludeLast: boolean,
): boolean {
  let nums = rankHistory.filter((r): r is number => r !== null);
  if (excludeLast && nums.length > 1) {
    nums = nums.slice(0, -1);
  }
  if (nums.length < 3) return false;

  for (let i = 1; i < nums.length - 1; i++) {
    const prevDelta = nums[i] - nums[i - 1];
    const nextDelta = nums[i + 1] - nums[i];
    if (prevDelta < 0 && nextDelta > ERRATIC_REVERSAL_THRESHOLD) return true;
    if (prevDelta > 0 && nextDelta < -ERRATIC_REVERSAL_THRESHOLD) return true;
  }
  return false;
}

/** Apply skill.yaml scanner filters + blocked assets to signals */
export function applyFilters(
  signals: Signal<EmergingMoversSignalMetadata>[],
  filters?: EmergingMoversConfig,
  blockedAssets?: string[],
): Signal<EmergingMoversSignalMetadata>[] {
  let filtered = signals;

  if (blockedAssets && blockedAssets.length > 0) {
    const blocked = new Set(blockedAssets.map((a) => a.toUpperCase()));
    filtered = filtered.filter(
      (s) => !blocked.has(s.token.toUpperCase()) && !blocked.has(s.qualifiedAsset.toUpperCase()),
    );
  }

  if (!filters) return filtered;

  if (filters.min_reasons !== undefined) {
    filtered = filtered.filter((s) => s.reasons.length >= filters.min_reasons!);
  }
  if (filters.max_rank !== undefined) {
    filtered = filtered.filter((s) => s.metadata.currentRank <= filters.max_rank!);
  }
  if (filters.min_traders !== undefined) {
    filtered = filtered.filter((s) => s.metadata.traders >= filters.min_traders!);
  }
  if (filters.exclude_erratic) {
    filtered = filtered.filter((s) => !s.metadata.erratic);
  }
  if (filters.require_immediate) {
    filtered = filtered.filter((s) => s.metadata.isImmediate || s.metadata.isFirstJump);
  }

  return filtered;
}

/** Compute top picks from signals based on available slots */
export function computeTopPicks(
  signals: Signal<EmergingMoversSignalMetadata>[],
  totalAvailableSlots: number,
): Signal<EmergingMoversSignalMetadata>[] {
  const firstJumpCount = signals.filter((s) => s.metadata.isFirstJump).length;
  const pickCount = Math.max(totalAvailableSlots, firstJumpCount);
  return pickCount > 0 ? signals.slice(0, pickCount) : [];
}

/** Decide whether signals should be skipped (no slots, no actionable path) */
export function shouldSkipSignals(
  anySlotsAvailable: boolean,
  hasFirstJump: boolean,
  anyRotationCandidate: boolean,
): boolean {
  if (anySlotsAvailable) return false;
  if (!hasFirstJump) return true;
  // FIRST_JUMP with no rotation candidates: keep signals visible but
  // consumer will see hasRotationCandidate=false — don't skip
  return false;
}

export class EmergingMoversScanner implements Scanner {
  readonly scannerType = 'emerging_movers';
  name = 'emerging-movers-scanner';
  intervalMs: number;

  private mcp: SenpiMcpClient;
  private stateManager: StateManager;
  private hookSystem: HookSystem;
  private skillName: string;
  private scanHistory: ScanSnapshot[] = [];
  private maxLeverageData: Record<string, number>;
  private strategies: Record<string, StrategyConfig>;
  private riskGuard: RiskGuard;
  private scannerConfig: EmergingMoversConfig;
  private blockedAssets?: string[];

  constructor(config: {
    mcp: SenpiMcpClient;
    stateManager: StateManager;
    hookSystem: HookSystem;
    skillName: string;
    intervalMs: number;
    maxLeverageData?: Record<string, number>;
    strategies: Record<string, StrategyConfig>;
    riskGuard: RiskGuard;
    scannerConfig?: Record<string, unknown>;
    blockedAssets?: string[];
  }) {
    this.mcp = config.mcp;
    this.stateManager = config.stateManager;
    this.hookSystem = config.hookSystem;
    this.skillName = config.skillName;
    this.intervalMs = config.intervalMs;
    this.maxLeverageData = config.maxLeverageData ?? {};
    this.strategies = config.strategies;
    this.riskGuard = config.riskGuard;
    this.blockedAssets = config.blockedAssets;

    // Parse EM-specific config from generic bag
    const raw = config.scannerConfig ?? {};
    this.scannerConfig = {
      min_reasons: typeof raw.min_reasons === 'number' ? raw.min_reasons : undefined,
      max_rank: typeof raw.max_rank === 'number' ? raw.max_rank : undefined,
      require_immediate: typeof raw.require_immediate === 'boolean' ? raw.require_immediate : undefined,
      exclude_erratic: typeof raw.exclude_erratic === 'boolean' ? raw.exclude_erratic : undefined,
      min_traders: typeof raw.min_traders === 'number' ? raw.min_traders : undefined,
    };

    // Load persisted scan history
    const saved = this.stateManager.read<{ scans: ScanSnapshot[] }>('scan-history');
    if (saved?.scans) {
      this.scanHistory = saved.scans;
    }
  }

  async tick(): Promise<void> {
    // 1. Fetch leaderboard
    let rawMarkets: LeaderboardMarket[];
    try {
      rawMarkets = await this.mcp.getLeaderboardMarkets(100);
    } catch (err) {
      logger.error('Scanner: leaderboard fetch failed', { error: String(err) });
      return;
    }

    // 2. Parse current scan
    const now = new Date().toISOString();
    const currentScan: ScanSnapshot = { time: now, markets: [] };

    for (let i = 0; i < Math.min(rawMarkets.length, TOP_N); i++) {
      const m = rawMarkets[i];
      currentScan.markets.push({
        token: m.token,
        dex: m.dex || '',
        rank: i + 1,
        direction: m.direction,
        contribution: roundTo(m.pct_of_top_traders_gain, 6),
        traders: m.trader_count,
        price_chg_4h: roundTo(m.token_price_change_pct_4h || 0, 4),
      });
    }

    // 3. Build previous scan token set
    const prevScans = this.scanHistory;
    const prevTop50Tokens = new Set<string>();
    if (prevScans.length > 0) {
      for (const m of prevScans[prevScans.length - 1].markets) {
        prevTop50Tokens.add(`${m.token}:${m.dex || ''}`);
      }
    }

    // 4. Analyze trends
    let alerts: Signal<EmergingMoversSignalMetadata>[] = [];

    if (prevScans.length >= MIN_SCANS_FOR_TREND) {
      const latestPrev = prevScans[prevScans.length - 1];
      const oldestAvailable = prevScans[prevScans.length - Math.min(prevScans.length, 5)];

      for (const market of currentScan.markets) {
        const { token, dex } = market;
        const currentRank = market.rank;
        const currentContrib = market.contribution;

        const prevMarket = getMarketInScan(latestPrev, token, dex);
        const oldMarket = getMarketInScan(oldestAvailable, token, dex);

        const alertReasons: string[] = [];
        let isDeepClimber = false;
        let isImmediate = false;
        let isFirstJump = false;
        let isContribExplosion = false;
        let rankJumpThisScan = 0;

        // 1. Fresh entry
        if (!prevMarket) {
          if (currentRank <= 20) {
            alertReasons.push(`NEW_ENTRY_DEEP at rank #${currentRank}`);
            isDeepClimber = true;
            isImmediate = true;
          } else if (currentRank <= 35) {
            alertReasons.push(`NEW_ENTRY at rank #${currentRank}`);
          }
        }

        // 2. Rank climbing (single scan)
        if (prevMarket) {
          const rankChange1 = prevMarket.rank - currentRank;
          rankJumpThisScan = rankChange1;

          if (rankChange1 >= 2) {
            alertReasons.push(`RANK_UP +${rankChange1} (#${prevMarket.rank}→#${currentRank})`);
          }

          if (rankChange1 >= 10 && prevMarket.rank >= 25) {
            isDeepClimber = true;
            isImmediate = true;
            alertReasons.push(`IMMEDIATE_MOVER +${rankChange1} from #${prevMarket.rank} in ONE scan`);

            const wasInPrev = prevTop50Tokens.has(`${token}:${dex || ''}`);
            if (!wasInPrev || prevMarket.rank >= 30) {
              isFirstJump = true;
              alertReasons.push(`FIRST_JUMP from #${prevMarket.rank}→#${currentRank} — highest priority`);
            }
          } else if (rankChange1 >= 5 && prevMarket.rank >= 25) {
            isDeepClimber = true;
            alertReasons.push(`DEEP_CLIMBER +${rankChange1} from #${prevMarket.rank}`);
          }
        }

        // 3. Contribution explosion
        if (prevMarket && prevMarket.contribution > 0) {
          const contribRatio = currentContrib / prevMarket.contribution;
          if (contribRatio >= 3.0) {
            alertReasons.push(
              `CONTRIB_EXPLOSION ${contribRatio.toFixed(1)}x in one scan (${(prevMarket.contribution * 100).toFixed(2)}→${(currentContrib * 100).toFixed(2)})`,
            );
            isContribExplosion = true;
            if (prevMarket.rank >= 20) {
              isImmediate = true;
              isDeepClimber = true;
            }
          }
        }

        // 4. Multi-scan climb
        if (oldMarket) {
          const rankChangeTotal = oldMarket.rank - currentRank;
          if (rankChangeTotal >= RANK_CLIMB_THRESHOLD) {
            alertReasons.push(
              `CLIMBING +${rankChangeTotal} over ${Math.min(prevScans.length, 5)} scans`,
            );
          }
          if (rankChangeTotal >= 10 && oldMarket.rank >= 30) {
            isDeepClimber = true;
            const hasDeepOrImm = alertReasons.some((r) => r.includes('DEEP_CLIMBER') || r.includes('IMMEDIATE'));
            if (!hasDeepOrImm) {
              alertReasons.push(`DEEP_CLIMBER +${rankChangeTotal} from #${oldMarket.rank} over ${Math.min(prevScans.length, 5)} scans`);
            }
          }
        }

        // 5. Contribution acceleration
        if (prevMarket) {
          const contribDelta = currentContrib - prevMarket.contribution;
          if (contribDelta >= CONTRIBUTION_ACCEL_THRESHOLD) {
            alertReasons.push(`ACCEL +${contribDelta.toFixed(3)} contribution`);
          }
        }

        // 6. Consistent climb streak
        if (prevScans.length >= 3) {
          const ranks: number[] = [];
          for (const scan of prevScans.slice(-3)) {
            const m = getMarketInScan(scan, token, dex);
            ranks.push(m ? m.rank : TOP_N + 1);
          }
          ranks.push(currentRank);

          const isConsistent = ranks.every((_, i) =>
            i === ranks.length - 1 || ranks[i] >= ranks[i + 1],
          );
          if (isConsistent && ranks[0] > ranks[ranks.length - 1]) {
            const streak = ranks[0] - ranks[ranks.length - 1];
            if (streak >= 2) {
              alertReasons.push(`STREAK climbing ${streak} ranks over 4 checks`);
            }
          }
        }

        // Calculate contribution velocity
        let contribVelocity = 0;
        const recentContribs: number[] = [];
        for (const scan of prevScans.slice(-5)) {
          const m = getMarketInScan(scan, token, dex);
          if (m) recentContribs.push(m.contribution);
        }
        recentContribs.push(currentContrib);

        if (recentContribs.length >= 2) {
          const deltas = recentContribs
            .slice(1)
            .map((c, i) => c - recentContribs[i]);
          contribVelocity = deltas.reduce((a, b) => a + b, 0) / deltas.length;

          if (contribVelocity > 0.002 && recentContribs.length >= 3 && alertReasons.length === 0) {
            alertReasons.push(`VELOCITY +${(contribVelocity * 100).toFixed(3)}%/scan sustained`);
          }
        }

        if (alertReasons.length === 0) continue;

        // Build history arrays
        const contribHistory: (number | null)[] = [];
        const rankHistory: (number | null)[] = [];
        for (const scan of prevScans.slice(-5)) {
          const m = getMarketInScan(scan, token, dex);
          if (m) {
            contribHistory.push(roundTo(m.contribution * 100, 2));
            rankHistory.push(m.rank);
          } else {
            contribHistory.push(null);
            rankHistory.push(null);
          }
        }
        contribHistory.push(roundTo(currentContrib * 100, 2));
        rankHistory.push(currentRank);

        // Determine signal type + priority + conviction
        let signalType: EmergingMoversSignalType;
        let signalPriority: number;
        let conviction: number;

        if (isFirstJump) {
          signalType = 'FIRST_JUMP';
          signalPriority = 1;
          conviction = SIGNAL_CONVICTION.FIRST_JUMP;
        } else if (isContribExplosion) {
          signalType = 'CONTRIB_EXPLOSION';
          signalPriority = 2;
          conviction = SIGNAL_CONVICTION.CONTRIB_EXPLOSION;
        } else if (isImmediate) {
          signalType = 'IMMEDIATE_MOVER';
          signalPriority = 3;
          conviction = SIGNAL_CONVICTION.IMMEDIATE_MOVER;
        } else if (isDeepClimber && alertReasons.some((r) => r.includes('NEW_ENTRY_DEEP'))) {
          signalType = 'NEW_ENTRY_DEEP';
          signalPriority = 4;
          conviction = SIGNAL_CONVICTION.NEW_ENTRY_DEEP;
        } else {
          signalType = 'DEEP_CLIMBER';
          signalPriority = 5;
          conviction = SIGNAL_CONVICTION.DEEP_CLIMBER;
        }

        // Max leverage lookup
        const levKey = dex ? `xyz:${token}` : token;
        const maxLeverage = this.maxLeverageData[levKey] ?? this.maxLeverageData[token] ?? null;

        // Erratic + velocity filters
        let erratic: boolean;
        if (isContribExplosion) {
          erratic = false;
        } else if (rankJumpThisScan >= 10 || isFirstJump) {
          erratic = isErraticHistory(rankHistory, true);
        } else {
          erratic = isErraticHistory(rankHistory, false);
        }

        let lowVelocity: boolean;
        if (isImmediate || isFirstJump) {
          lowVelocity = contribVelocity * 100 <= 0;
        } else {
          lowVelocity = contribVelocity * 100 < MIN_VELOCITY_FOR_DEEP_CLIMBER * 100;
        }

        // Downgrade logic
        if (!isFirstJump && !isContribExplosion && isImmediate && (erratic || lowVelocity)) {
          isImmediate = false;
          signalType = 'DEEP_CLIMBER';
          signalPriority = 5;
          conviction = SIGNAL_CONVICTION.DEEP_CLIMBER;
          if (erratic) alertReasons.push('DOWNGRADED: erratic rank history');
          if (lowVelocity) alertReasons.push(`DOWNGRADED: non-positive velocity (${roundTo(contribVelocity * 100, 4)})`);
        }

        alerts.push({
          scannerType: 'emerging_movers',
          token,
          dex: dex || null,
          qualifiedAsset: dex === 'xyz' ? `xyz:${token}` : token,
          direction: market.direction.toUpperCase(),
          conviction,
          signalType,
          signalPriority,
          reasons: alertReasons,
          metadata: {
            scannerType: 'emerging_movers',
            currentRank,
            contribution: roundTo(currentContrib * 100, 3),
            contribVelocity: roundTo(contribVelocity * 100, 4),
            traders: market.traders,
            priceChg4h: market.price_chg_4h,
            maxLeverage,
            rankHistory,
            contribHistory,
            isFirstJump,
            isContribExplosion,
            isImmediate,
            isDeepClimber,
            erratic,
            lowVelocity,
            rankJumpThisScan,
          },
        });
      }
    }

    // Sort: priority > velocity > reason count
    alerts.sort((a, b) => {
      if (a.signalPriority !== b.signalPriority) return a.signalPriority - b.signalPriority;
      if (Math.abs(b.metadata.contribVelocity) !== Math.abs(a.metadata.contribVelocity))
        return Math.abs(b.metadata.contribVelocity) - Math.abs(a.metadata.contribVelocity);
      return b.reasons.length - a.reasons.length;
    });

    // 5. Apply skill.yaml filters
    alerts = applyFilters(alerts, this.scannerConfig, this.blockedAssets);

    // 6. Compute slot availability per strategy
    const strategySlots = await this.computeSlotAvailability();

    const anySlotsAvailable = Object.keys(strategySlots).length === 0
      ? true // no strategies configured = don't gate
      : Object.values(strategySlots).some((s) => s.available > 0);
    const totalAvailableSlots = Object.values(strategySlots)
      .reduce((sum, s) => sum + s.available, 0);

    // 7. Pre-filter: no slots + no FIRST_JUMP → skip
    const hasFirstJump = alerts.some((a) => a.metadata.isFirstJump);
    if (shouldSkipSignals(anySlotsAvailable, hasFirstJump, this.anyRotationCandidate(strategySlots))) {
      alerts = [];
    }

    // 8. Compute topPicks
    const topPicks = computeTopPicks(alerts, totalAvailableSlots);

    // Save scan history
    this.scanHistory.push(currentScan);
    if (this.scanHistory.length > MAX_HISTORY) {
      this.scanHistory = this.scanHistory.slice(-MAX_HISTORY);
    }
    this.stateManager.write('scan-history', { scans: this.scanHistory });

    // 9. Fire single batch hook with full ScanResult
    const scanResult: ScanResult<EmergingMoversScanMetadata> = {
      scannerType: 'emerging_movers',
      signals: alerts,
      topPicks,
      strategySlots,
      anySlotsAvailable,
      totalAvailableSlots,
      scansInHistory: this.scanHistory.length,
      metadata: {
        scannerType: 'emerging_movers',
        hasFirstJump,
        hasImmediate: alerts.some((a) => a.metadata.isImmediate),
        hasContribExplosion: alerts.some((a) => a.metadata.isContribExplosion),
      },
    };

    if (alerts.length > 0) {
      await this.hookSystem.fire(this.skillName, {
        type: 'on_signal_detected',
        skillName: this.skillName,
        data: scanResult as unknown as Record<string, unknown>,
        timestamp: now,
      });

      logger.info(`Scanner detected ${alerts.length} signals`, {
        firstJumps: alerts.filter((a) => a.metadata.isFirstJump).length,
        immediates: alerts.filter((a) => a.metadata.isImmediate).length,
        topPicks: topPicks.length,
        totalAvailableSlots,
      });
    }
  }

  /** Check if any strategy has rotation candidates */
  private anyRotationCandidate(strategySlots: Record<string, StrategySlotInfo>): boolean {
    if (Object.keys(strategySlots).length === 0) return true;
    return Object.values(strategySlots).some((s) => s.hasRotationCandidate);
  }

  /** Compute slot availability for all configured strategies */
  private async computeSlotAvailability(): Promise<Record<string, StrategySlotInfo>> {
    const result: Record<string, StrategySlotInfo> = {};

    for (const [key, strategy] of Object.entries(this.strategies)) {
      if (strategy.enabled === false) continue;

      const maxSlots = strategy.slots;
      const wallet = strategy.wallet;

      // Count active DSL states, excluding stale approximate DSLs
      let dslActiveCount = 0;
      const slotAges: { coin: string; ageMinutes: number | null }[] = [];
      const rotationEligibleCoins: string[] = [];
      const now = Date.now();

      const dslStates = this.stateManager.listActiveDslStates(this.skillName, key);
      for (const { asset, state } of dslStates) {
        // Skip stale approximate DSLs
        if (state.approximate && state.createdAt) {
          const created = new Date(state.createdAt).getTime();
          const ageMin = (now - created) / 60_000;
          if (ageMin > APPROX_GRACE_MINUTES) continue;
        }

        dslActiveCount++;

        let slotAgeMin: number | null = null;
        if (state.createdAt) {
          const created = new Date(state.createdAt).getTime();
          slotAgeMin = roundTo((now - created) / 60_000, 1);
        }
        slotAges.push({ coin: asset, ageMinutes: slotAgeMin });

        if (slotAgeMin === null || slotAgeMin >= ROTATION_COOLDOWN_MINUTES) {
          rotationEligibleCoins.push(asset);
        }
      }

      // Cross-check against on-chain positions
      let onChainCount = 0;
      const onChainCoins: string[] = [];
      if (wallet) {
        try {
          const chData = await this.mcp.getClearinghouseState(wallet);
          if (chData) {
            for (const sectionKey of ['main', 'xyz'] as const) {
              const section = chData[sectionKey];
              if (!section?.assetPositions) continue;
              for (const ap of section.assetPositions) {
                const coin = ap.position?.coin ?? '';
                const szi = parseFloat(String(ap.position?.szi ?? 0));
                if (coin && szi !== 0) {
                  onChainCount++;
                  onChainCoins.push(coin);
                }
              }
            }
          }
        } catch {
          // Non-critical — use DSL count only
        }
      }

      // Use max of both counts (handles desync both directions)
      const used = Math.max(dslActiveCount, onChainCount);

      // Gate check
      let gate: GateStatus = 'OPEN';
      let gateReason: string | null = null;
      try {
        const gateResult = this.riskGuard.checkGate(key, strategy.guardRails);
        gate = gateResult.gate;
        gateReason = gateResult.reason ?? null;
      } catch {
        // Default to OPEN on error
      }

      const available = gate !== 'OPEN' ? 0 : Math.max(0, maxSlots - used);

      result[key] = {
        name: strategy._key ?? key,
        slots: maxSlots,
        used,
        available,
        dslActive: dslActiveCount,
        onChain: onChainCount,
        onChainCoins: onChainCoins.sort(),
        slotAges,
        rotationEligibleCoins: rotationEligibleCoins.sort(),
        hasRotationCandidate: rotationEligibleCoins.length > 0,
        gate,
        gateReason,
      };
    }

    return result;
  }
}
