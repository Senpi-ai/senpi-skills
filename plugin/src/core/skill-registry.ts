/**
 * Skill Registry — Skill lifecycle management (install, start, stop).
 *
 * Manages the full lifecycle of skills loaded from skill.yaml files.
 * Coordinates background services, hooks, and state for each skill.
 */

import { SkillYamlConfig, StrategyConfig, DslConfig, DslTier, BackgroundService, Scanner } from './types.js';
import { StateManager } from './state-manager.js';
import { SenpiMcpClient } from './mcp-client.js';
import { PriceCache } from './price-cache.js';
import { ServiceManager } from './service-manager.js';
import { HookSystem } from './hook-system.js';
import { LlmDecision, ContextBuilder } from './llm-decision.js';
import { NotificationService } from './notifications.js';
import { loadSkillYaml, parseInterval, saveResolvedConfig } from './skill-loader.js';
import { RiskGuard } from '../primitives/risk/risk-guard.js';
import { DslRunnerService } from '../primitives/exits/dsl-runner.js';
import { EmergingMoversScanner } from '../primitives/scanners/emerging-movers.js';
import { EntryHandler } from '../primitives/entry/entry-handler.js';
import { SmFlipService } from '../primitives/exits/sm-flip.js';
import { WatchdogService } from '../primitives/risk/watchdog.js';
import { HealthCheckerService } from '../primitives/health/health-checker.js';
import { logger } from './logger.js';

interface SkillInstance {
  config: SkillYamlConfig;
  strategies: Map<string, StrategyConfig>;
  services: BackgroundService[];
  riskGuard: RiskGuard;
}

export class SkillRegistry {
  private skills = new Map<string, SkillInstance>();
  private stateManager: StateManager;
  private mcp: SenpiMcpClient;
  private priceCache: PriceCache;
  private serviceManager: ServiceManager;
  private hookSystem: HookSystem;
  private notifications: NotificationService;
  private configDir: string;

  constructor(config: {
    stateManager: StateManager;
    mcp: SenpiMcpClient;
    priceCache: PriceCache;
    serviceManager: ServiceManager;
    hookSystem: HookSystem;
    notifications: NotificationService;
    configDir: string;
  }) {
    this.stateManager = config.stateManager;
    this.mcp = config.mcp;
    this.priceCache = config.priceCache;
    this.serviceManager = config.serviceManager;
    this.hookSystem = config.hookSystem;
    this.notifications = config.notifications;
    this.configDir = config.configDir;
  }

  /** Install a skill from a YAML file */
  install(yamlPath: string, variables: Record<string, string> = {}): string {
    const config = loadSkillYaml(yamlPath, variables);
    const skillName = config.name;

    // Set up notifications
    if (config.notifications?.telegram_chat_id) {
      this.notifications.setChatId(config.notifications.telegram_chat_id);
    }

    // Build strategy configs
    const strategies = new Map<string, StrategyConfig>();
    for (const [key, sc] of Object.entries(config.strategies)) {
      const preset = config.exit.dsl_presets[sc.dsl_preset];
      const tiers: DslTier[] = preset
        ? preset.tiers.map((t) => ({
            triggerPct: t.trigger_pct,
            lockPct: t.lock_pct,
            breaches: t.breaches,
          }))
        : [];

      strategies.set(key, {
        wallet: sc.wallet,
        strategyId: sc.strategy_id,
        budget: typeof sc.budget === 'string' ? parseFloat(sc.budget) : sc.budget,
        slots: sc.slots,
        tradingRisk: sc.trading_risk,
        dslPreset: sc.dsl_preset,
        marginPerSlot: 0, // Calculated from budget/slots
        dsl: { tiers },
        guardRails: {
          maxEntriesPerDay: config.risk.guard_rails.max_entries_per_day,
          bypassOnProfit: config.risk.guard_rails.bypass_on_profit,
          maxConsecutiveLosses: config.risk.guard_rails.max_consecutive_losses,
          cooldownMinutes: config.risk.guard_rails.cooldown_minutes,
        },
        enabled: true,
        _key: key,
      });
    }

    // Create risk guard
    const riskGuard = new RiskGuard(this.stateManager, this.mcp);

    // Register hooks
    this.hookSystem.registerSkillHooks(skillName, config.hooks);

    // Build DSL configs per strategy
    const dslConfigs = new Map<string, DslConfig>();
    for (const [key] of strategies) {
      dslConfigs.set(key, {});
    }

    // Create background services
    const services: BackgroundService[] = [];

    // DSL Runner
    const dslRunner = new DslRunnerService({
      stateManager: this.stateManager,
      mcp: this.mcp,
      priceCache: this.priceCache,
      hookSystem: this.hookSystem,
      notifications: this.notifications,
      skillName,
      dslConfigs,
    });
    services.push(dslRunner);

    // Scanner
    const strategiesRecord: Record<string, StrategyConfig> = {};
    for (const [key, strat] of strategies) {
      strategiesRecord[key] = strat;
    }

    let scanner: Scanner | undefined;
    if (config.scanner.type === 'emerging_movers') {
      scanner = new EmergingMoversScanner({
        mcp: this.mcp,
        stateManager: this.stateManager,
        hookSystem: this.hookSystem,
        skillName,
        intervalMs: parseInterval(config.scanner.interval),
        strategies: strategiesRecord,
        riskGuard,
        scannerConfig: config.scanner.config,
        blockedAssets: config.scanner.blocked_assets,
      });
      services.push(scanner);
    }

    // Entry Handler (wires scanner → LLM → position opener)
    const contextBuilder = new ContextBuilder();
    const llmDecision = new LlmDecision();
    const _entryHandler = new EntryHandler({
      skillName,
      entryConfig: config.entry,
      strategies,
      contextBuilder,
      llmDecision,
      stateManager: this.stateManager,
      mcp: this.mcp,
      riskGuard,
      hookSystem: this.hookSystem,
      notifications: this.notifications,
    });

    // SM Flip Checker
    if (config.exit.sm_flip?.enabled) {
      const smFlip = new SmFlipService({
        stateManager: this.stateManager,
        mcp: this.mcp,
        hookSystem: this.hookSystem,
        notifications: this.notifications,
        skillName,
        intervalMs: parseInterval(config.exit.sm_flip.interval),
      });
      services.push(smFlip);
    }

    // Watchdog
    const strategyMap = new Map<string, { wallet: string; slots: number }>();
    for (const [key, sc] of strategies) {
      strategyMap.set(key, { wallet: sc.wallet, slots: sc.slots });
    }

    const watchdog = new WatchdogService({
      mcp: this.mcp,
      stateManager: this.stateManager,
      hookSystem: this.hookSystem,
      notifications: this.notifications,
      skillName,
      strategies: strategyMap,
    });
    services.push(watchdog);

    // Health Checker
    const healthStrategies = new Map<string, { wallet: string; tiers?: DslTier[] }>();
    for (const [key, sc] of strategies) {
      healthStrategies.set(key, { wallet: sc.wallet, tiers: sc.dsl?.tiers });
    }

    const healthChecker = new HealthCheckerService({
      stateManager: this.stateManager,
      mcp: this.mcp,
      notifications: this.notifications,
      skillName,
      strategies: healthStrategies,
    });
    services.push(healthChecker);

    // Register all services with the service manager
    for (const service of services) {
      this.serviceManager.register(service);
    }

    // Store skill instance
    this.skills.set(skillName, {
      config,
      strategies,
      services,
      riskGuard,
    });

    // Save resolved config for restart recovery
    saveResolvedConfig(this.configDir, skillName, config);

    logger.info(`Skill installed: ${skillName} v${config.version}`, {
      strategies: [...strategies.keys()],
      services: services.map((s) => s.name),
    });

    return skillName;
  }

  /** Start all services for all installed skills */
  startAll(): void {
    this.priceCache.start();
    this.stateManager.start();
    this.serviceManager.startAll();
    logger.info('All skills started');
  }

  /** Stop all services */
  stopAll(): void {
    this.serviceManager.stopAll();
    this.priceCache.stop();
    this.stateManager.stop();
    logger.info('All skills stopped');
  }

  /** Get a skill instance by name */
  getSkill(skillName: string): SkillInstance | undefined {
    return this.skills.get(skillName);
  }

  /** Get strategy config for a skill */
  getStrategy(skillName: string, strategyKey: string): StrategyConfig | undefined {
    return this.skills.get(skillName)?.strategies.get(strategyKey);
  }

  /** Get risk guard for a skill */
  getRiskGuard(skillName: string): RiskGuard | undefined {
    return this.skills.get(skillName)?.riskGuard;
  }

  /** List all installed skills */
  listSkills(): string[] {
    return [...this.skills.keys()];
  }
}
