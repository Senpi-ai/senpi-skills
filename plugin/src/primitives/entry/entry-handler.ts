/**
 * Entry Handler — Scanner → LLM → Position Opener pipeline.
 *
 * Connects the scan result (from on_signal_detected hook) to the LLM decision
 * layer and the position opener. Registered as a hook handler on the HookSystem.
 */

import {
  HookEvent,
  ScanResult,
  Signal,
  SkillEntryConfig,
  StrategyConfig,
  EntryDecision,
  PositionOpenRequest,
} from '../../core/types.js';
import { ContextBuilder, LlmDecision } from '../../core/llm-decision.js';
import { StateManager } from '../../core/state-manager.js';
import { SenpiMcpClient } from '../../core/mcp-client.js';
import { RiskGuard } from '../risk/risk-guard.js';
import { HookSystem } from '../../core/hook-system.js';
import { NotificationService } from '../../core/notifications.js';
import { openPosition } from '../execution/position-opener.js';
import { logger } from '../../core/logger.js';

export class EntryHandler {
  private skillName: string;
  private entryConfig: SkillEntryConfig;
  private strategies: Map<string, StrategyConfig>;
  private contextBuilder: ContextBuilder;
  private llmDecision: LlmDecision;
  private stateManager: StateManager;
  private mcp: SenpiMcpClient;
  private riskGuard: RiskGuard;
  private hookSystem: HookSystem;
  private notifications: NotificationService;
  private maxLeverageData: Record<string, number>;

  constructor(config: {
    skillName: string;
    entryConfig: SkillEntryConfig;
    strategies: Map<string, StrategyConfig>;
    contextBuilder: ContextBuilder;
    llmDecision: LlmDecision;
    stateManager: StateManager;
    mcp: SenpiMcpClient;
    riskGuard: RiskGuard;
    hookSystem: HookSystem;
    notifications: NotificationService;
    maxLeverageData?: Record<string, number>;
  }) {
    this.skillName = config.skillName;
    this.entryConfig = config.entryConfig;
    this.strategies = config.strategies;
    this.contextBuilder = config.contextBuilder;
    this.llmDecision = config.llmDecision;
    this.stateManager = config.stateManager;
    this.mcp = config.mcp;
    this.riskGuard = config.riskGuard;
    this.hookSystem = config.hookSystem;
    this.notifications = config.notifications;
    this.maxLeverageData = config.maxLeverageData ?? {};

    // Register on the hook system
    this.hookSystem.on('on_signal_detected', (event) => this.handleScanResult(event));
  }

  /** Handle a scan result from the on_signal_detected hook */
  async handleScanResult(event: HookEvent): Promise<void> {
    const scanResult = event.data as unknown as ScanResult;

    // Nothing to act on
    if (!scanResult.signals || scanResult.signals.length === 0) {
      return;
    }
    if (!scanResult.anySlotsAvailable && scanResult.totalAvailableSlots === 0) {
      logger.debug('EntryHandler: no slots available, skipping');
      return;
    }

    // Skip if decision mode is not LLM
    if (this.entryConfig.decision_mode !== 'llm') {
      logger.debug(`EntryHandler: decision_mode is ${this.entryConfig.decision_mode}, skipping`);
      return;
    }

    // Register the signal data as a context provider for this call
    this.contextBuilder.registerProvider('signal', () => ({
      signals: scanResult.signals,
      topPicks: scanResult.topPicks,
      strategySlots: scanResult.strategySlots,
      anySlotsAvailable: scanResult.anySlotsAvailable,
      totalAvailableSlots: scanResult.totalAvailableSlots,
      metadata: scanResult.metadata,
    }));

    // Build context from the entry config's context list
    const context = this.contextBuilder.build(this.entryConfig.context);

    // Call LLM for entry decision
    let decision: EntryDecision;
    try {
      const response = await this.llmDecision.decide<EntryDecision>({
        prompt: this.entryConfig.decision_prompt,
        context,
        model: this.entryConfig.decision_model,
      });
      decision = response.decision;
    } catch (err) {
      logger.error('EntryHandler: LLM decision failed', { error: String(err) });
      return;
    }

    // Check decision
    if (!decision.enter) {
      logger.info('EntryHandler: LLM decided not to enter', {
        reasoning: decision.reasoning,
      });
      return;
    }

    // Check confidence threshold
    if (decision.confidence < this.entryConfig.min_confidence) {
      logger.info('EntryHandler: confidence below threshold', {
        confidence: decision.confidence,
        minConfidence: this.entryConfig.min_confidence,
        reasoning: decision.reasoning,
      });
      return;
    }

    // Validate target strategy exists
    const strategy = this.strategies.get(decision.target_strategy);
    if (!strategy) {
      logger.error('EntryHandler: unknown target strategy', {
        targetStrategy: decision.target_strategy,
        availableStrategies: [...this.strategies.keys()],
      });
      return;
    }

    // Find the matching signal for the decision
    const matchingSignal = this.findMatchingSignal(scanResult.topPicks, decision);

    // Build position open request
    const openReq: PositionOpenRequest = {
      strategyKey: decision.target_strategy,
      asset: matchingSignal?.qualifiedAsset ?? scanResult.topPicks[0]?.qualifiedAsset ?? '',
      direction: decision.direction,
      conviction: decision.confidence / 10, // normalize 1-10 to 0-1
      signalType: matchingSignal?.signalType ?? 'UNKNOWN',
      rotateOut: decision.rotate_out ?? undefined,
    };

    // Open position
    try {
      const result = await openPosition(openReq, {
        strategy,
        skillName: this.skillName,
        stateManager: this.stateManager,
        mcp: this.mcp,
        riskGuard: this.riskGuard,
        maxLeverageData: this.maxLeverageData,
      });

      // Fire on_position_opened hook
      await this.hookSystem.fire(this.skillName, {
        type: 'on_position_opened',
        skillName: this.skillName,
        strategyKey: decision.target_strategy,
        data: result as unknown as Record<string, unknown>,
        timestamp: new Date().toISOString(),
      });

      // Send notification
      await this.notifications.send(result.notification);

      logger.info('EntryHandler: position opened', {
        asset: result.asset,
        direction: result.direction,
        strategyKey: result.strategyKey,
        leverage: result.leverage,
      });
    } catch (err) {
      logger.error('EntryHandler: position open failed', {
        asset: openReq.asset,
        error: String(err),
      });
    }
  }

  /** Find the signal that best matches the LLM decision */
  private findMatchingSignal(
    signals: Signal[],
    decision: EntryDecision,
  ): Signal | undefined {
    return signals.find(
      (s) => s.direction === decision.direction,
    ) ?? signals[0];
  }
}
