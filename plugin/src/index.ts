/**
 * @senpi/trading-core — Plugin entry point
 *
 * Exports all public APIs for the plugin architecture.
 */

// Core
export { StateManager } from './core/state-manager.js';
export { SenpiMcpClient } from './core/mcp-client.js';
export { PriceCache } from './core/price-cache.js';
export { ServiceManager } from './core/service-manager.js';
export { HookSystem } from './core/hook-system.js';
export { LlmDecision, ContextBuilder } from './core/llm-decision.js';
export { NotificationService } from './core/notifications.js';
export { SkillRegistry } from './core/skill-registry.js';
export { loadSkillYaml, parseInterval, saveResolvedConfig, loadResolvedConfig } from './core/skill-loader.js';
export { logger, setLogLevel } from './core/logger.js';

// Primitives — Exits
export { dslTick } from './primitives/exits/dsl-engine.js';
export { DslRunnerService } from './primitives/exits/dsl-runner.js';
export { SmFlipService } from './primitives/exits/sm-flip.js';

// Primitives — Risk
export { calculateLeverage } from './primitives/risk/position-sizer.js';
export { RiskGuard } from './primitives/risk/risk-guard.js';
export { WatchdogService } from './primitives/risk/watchdog.js';

// Primitives — Execution
export { openPosition } from './primitives/execution/position-opener.js';

// Primitives — Entry
export { EntryHandler } from './primitives/entry/entry-handler.js';

// Primitives — Scanners
export { EmergingMoversScanner } from './primitives/scanners/emerging-movers.js';

// Primitives — Health
export { HealthCheckerService } from './primitives/health/health-checker.js';

// Types
export * from './core/types.js';
